"""
transform.py

Data Transformation stage.

Reads the raw source data (from data/raw/*.csv, mirroring what lives in
the `raw` Postgres schema after ingestion) and produces standardized,
warehouse-ready DataFrames for guests, properties, rooms, reservations,
and payments.

Responsibilities:
    - Standardize column names / casing / whitespace
    - Normalize booking_status and payment_status to a controlled vocabulary
    - Parse mixed date/timestamp formats into a single ISO format
    - Cast columns to correct types
    - Drop exact-duplicate records
    - Flag and quarantine rows with invalid foreign-key references or
      unparseable required fields, rather than silently dropping them

This module is pure pandas (no DB dependency) so it can be unit tested
and run standalone. `load.py` is responsible for writing the results
(and the rejected-records log) into PostgreSQL.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
logger = logging.getLogger("transform")

# Canonical status vocabularies. Any raw value is lowercased/stripped and
# matched against these; unmatched values fall through to "unknown" and are
# flagged (not silently kept) so they surface as a data-quality issue.
BOOKING_STATUS_MAP = {
    "confirmed": "confirmed", "conf": "confirmed", "cnf": "confirmed",
    "cancelled": "cancelled", "canceled": "cancelled", "cxl": "cancelled",
    "checked_in": "checked_in", "checked-in": "checked_in", "checked in": "checked_in", "ci": "checked_in",
    "checked_out": "checked_out", "checked-out": "checked_out", "checked out": "checked_out", "co": "checked_out",
    "no_show": "no_show", "no-show": "no_show", "no show": "no_show", "noshow": "no_show", "ns": "no_show",
    "pending": "pending", "pnd": "pending",
}

PAYMENT_STATUS_MAP = {
    "paid": "paid", "success": "paid",
    "failed": "failed", "declined": "failed",
    "refunded": "refunded",
    "pending": "pending",
}

DATE_FORMATS_TO_TRY = [
    "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%d-%b-%Y", "%Y/%m/%d",
]
TIMESTAMP_FORMATS_TO_TRY = [
    "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%m-%d-%Y %I:%M %p", "%Y-%m-%d",
]


@dataclass
class RejectedRecord:
    source_table: str
    raw_record: dict
    reason: str


@dataclass
class TransformResult:
    guests: pd.DataFrame
    properties: pd.DataFrame
    rooms: pd.DataFrame
    reservations: pd.DataFrame
    payments: pd.DataFrame
    rejected: list = field(default_factory=list)  # list[RejectedRecord]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_str(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().replace({"nan": None, "": None})


def _parse_flexible_date(value, formats):
    if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == "":
        return None
    value = str(value).strip()
    for fmt in formats:
        try:
            return pd.to_datetime(value, format=fmt)
        except (ValueError, TypeError):
            continue
    # last resort: let pandas infer
    try:
        return pd.to_datetime(value, errors="raise")
    except (ValueError, TypeError):
        return None


def _standardize_status(value, mapping: dict):
    if value is None or str(value).strip() == "":
        return "unknown"
    key = str(value).strip().lower()
    return mapping.get(key, "unknown")


# ---------------------------------------------------------------------------
# Per-table transforms
# ---------------------------------------------------------------------------

def transform_guests(raw: pd.DataFrame, rejected: list) -> pd.DataFrame:
    df = raw.copy()
    df.columns = [c.strip().lower() for c in df.columns]

    for col in ["first_name", "last_name", "email", "phone", "country"]:
        df[col] = _clean_str(df[col])

    df["guest_id"] = pd.to_numeric(df["guest_id"], errors="coerce").astype("Int64")
    df["email"] = df["email"].str.lower()

    missing_id = df["guest_id"].isna()
    for _, row in df[missing_id].iterrows():
        rejected.append(RejectedRecord("guests", row.to_dict(), "missing/invalid guest_id"))
    df = df[~missing_id]

    missing_name = df["first_name"].isna() | df["last_name"].isna()
    for _, row in df[missing_name].iterrows():
        rejected.append(RejectedRecord("guests", row.to_dict(), "missing first_name or last_name"))
    df = df[~missing_name]

    before = len(df)
    df = df.drop_duplicates(subset=["guest_id"], keep="first")
    logger.info(f"guests: dropped {before - len(df)} duplicate guest_id rows")

    return df.reset_index(drop=True)


def transform_properties(raw: pd.DataFrame, rejected: list) -> pd.DataFrame:
    df = raw.copy()
    df.columns = [c.strip().lower() for c in df.columns]

    for col in ["property_name", "city", "country"]:
        df[col] = _clean_str(df[col])

    df["property_id"] = pd.to_numeric(df["property_id"], errors="coerce").astype("Int64")

    missing_id = df["property_id"].isna()
    for _, row in df[missing_id].iterrows():
        rejected.append(RejectedRecord("properties", row.to_dict(), "missing/invalid property_id"))
    df = df[~missing_id]

    df = df.drop_duplicates(subset=["property_id"], keep="first")
    return df.reset_index(drop=True)


def transform_rooms(raw: pd.DataFrame, valid_property_ids: set, rejected: list) -> pd.DataFrame:
    df = raw.copy()
    df.columns = [c.strip().lower() for c in df.columns]

    df["room_id"] = pd.to_numeric(df["room_id"], errors="coerce").astype("Int64")
    df["property_id"] = pd.to_numeric(df["property_id"], errors="coerce").astype("Int64")
    df["capacity"] = pd.to_numeric(df["capacity"], errors="coerce").astype("Int64")
    df["room_type"] = _clean_str(df["room_type"])

    bad = df["room_id"].isna() | df["property_id"].isna() | df["capacity"].isna() | (df["capacity"] <= 0)
    for _, row in df[bad].iterrows():
        rejected.append(RejectedRecord("rooms", row.to_dict(), "missing/invalid room_id, property_id, or capacity"))
    df = df[~bad]

    invalid_fk = ~df["property_id"].isin(valid_property_ids)
    for _, row in df[invalid_fk].iterrows():
        rejected.append(RejectedRecord("rooms", row.to_dict(), "property_id not found in properties"))
    df = df[~invalid_fk]

    df = df.drop_duplicates(subset=["room_id"], keep="first")
    return df.reset_index(drop=True)


def transform_reservations(raw: pd.DataFrame, valid_guest_ids: set, valid_property_ids: set,
                            valid_room_ids: set, rejected: list) -> pd.DataFrame:
    df = raw.copy()
    df.columns = [c.strip().lower() for c in df.columns]

    df["reservation_id"] = _clean_str(df["reservation_id"])
    df["guest_id"] = pd.to_numeric(df["guest_id"], errors="coerce").astype("Int64")
    df["property_id"] = pd.to_numeric(df["property_id"], errors="coerce").astype("Int64")
    df["room_id"] = pd.to_numeric(df["room_id"], errors="coerce").astype("Int64")

    df["check_in_date"] = df["check_in_date"].apply(lambda v: _parse_flexible_date(v, DATE_FORMATS_TO_TRY))
    df["check_out_date"] = df["check_out_date"].apply(lambda v: _parse_flexible_date(v, DATE_FORMATS_TO_TRY))
    df["booking_date"] = df["booking_date"].apply(lambda v: _parse_flexible_date(v, TIMESTAMP_FORMATS_TO_TRY))
    df["booking_status"] = df["booking_status"].apply(lambda v: _standardize_status(v, BOOKING_STATUS_MAP))

    # Required-field checks
    missing_required = (
        df["reservation_id"].isna() | df["guest_id"].isna() | df["property_id"].isna()
        | df["room_id"].isna() | df["check_in_date"].isna() | df["booking_date"].isna()
    )
    for _, row in df[missing_required].iterrows():
        rejected.append(RejectedRecord("reservations", row.astype(str).to_dict(), "missing/unparseable required field"))
    df = df[~missing_required]

    # Referential integrity checks
    invalid_guest = ~df["guest_id"].isin(valid_guest_ids)
    invalid_property = ~df["property_id"].isin(valid_property_ids)
    invalid_room = ~df["room_id"].isin(valid_room_ids)
    invalid_fk = invalid_guest | invalid_property | invalid_room
    for _, row in df[invalid_fk].iterrows():
        rejected.append(RejectedRecord("reservations", row.astype(str).to_dict(), "invalid guest_id/property_id/room_id reference"))
    df = df[~invalid_fk]

    # Logical consistency: check_out must not precede check_in
    bad_dates = df["check_out_date"].notna() & (df["check_out_date"] < df["check_in_date"])
    for _, row in df[bad_dates].iterrows():
        rejected.append(RejectedRecord("reservations", row.astype(str).to_dict(), "check_out_date before check_in_date"))
    df = df[~bad_dates]

    unknown_status = df["booking_status"] == "unknown"
    for _, row in df[unknown_status].iterrows():
        rejected.append(RejectedRecord("reservations", row.astype(str).to_dict(), "unrecognized booking_status value"))
    df = df[~unknown_status]

    before = len(df)
    df = df.drop_duplicates(subset=["reservation_id"], keep="first")
    logger.info(f"reservations: dropped {before - len(df)} duplicate reservation_id rows")

    df["check_in_date"] = df["check_in_date"].dt.date
    df["check_out_date"] = df["check_out_date"].dt.date

    return df.reset_index(drop=True)


def transform_payments(raw: pd.DataFrame, valid_reservation_ids: set, rejected: list) -> pd.DataFrame:
    df = raw.copy()
    df.columns = [c.strip().lower() for c in df.columns]

    df["payment_id"] = _clean_str(df["payment_id"])
    df["reservation_id"] = _clean_str(df["reservation_id"])
    df["payment_method"] = _clean_str(df["payment_method"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df["payment_date"] = df["payment_date"].apply(lambda v: _parse_flexible_date(v, TIMESTAMP_FORMATS_TO_TRY))
    df["payment_status"] = df["payment_status"].apply(lambda v: _standardize_status(v, PAYMENT_STATUS_MAP))

    missing_required = df["payment_id"].isna() | df["reservation_id"].isna() | df["amount"].isna()
    for _, row in df[missing_required].iterrows():
        rejected.append(RejectedRecord("payments", row.astype(str).to_dict(), "missing/unparseable required field"))
    df = df[~missing_required]

    invalid_fk = ~df["reservation_id"].isin(valid_reservation_ids)
    for _, row in df[invalid_fk].iterrows():
        rejected.append(RejectedRecord("payments", row.astype(str).to_dict(), "reservation_id not found in reservations"))
    df = df[~invalid_fk]

    negative_amount = df["amount"] < 0
    for _, row in df[negative_amount].iterrows():
        rejected.append(RejectedRecord("payments", row.astype(str).to_dict(), "negative payment amount"))
    df = df[~negative_amount]

    unknown_status = df["payment_status"] == "unknown"
    for _, row in df[unknown_status].iterrows():
        rejected.append(RejectedRecord("payments", row.astype(str).to_dict(), "unrecognized payment_status value"))
    df = df[~unknown_status]

    df = df.drop_duplicates(subset=["payment_id"], keep="first")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_transformations(raw_dir: Path) -> TransformResult:
    rejected: list = []

    raw_guests = pd.read_csv(raw_dir / "guests.csv", dtype=str, keep_default_na=False).replace("", None)
    raw_properties = pd.read_csv(raw_dir / "properties.csv", dtype=str, keep_default_na=False).replace("", None)
    raw_rooms = pd.read_csv(raw_dir / "rooms.csv", dtype=str, keep_default_na=False).replace("", None)
    raw_reservations = pd.read_csv(raw_dir / "reservations.csv", dtype=str, keep_default_na=False).replace("", None)
    raw_payments = pd.read_csv(raw_dir / "payments.csv", dtype=str, keep_default_na=False).replace("", None)

    guests = transform_guests(raw_guests, rejected)
    properties = transform_properties(raw_properties, rejected)
    rooms = transform_rooms(raw_rooms, set(properties["property_id"]), rejected)
    reservations = transform_reservations(
        raw_reservations,
        set(guests["guest_id"]), set(properties["property_id"]), set(rooms["room_id"]),
        rejected,
    )
    payments = transform_payments(raw_payments, set(reservations["reservation_id"]), rejected)

    logger.info(f"Transformation complete. {len(rejected):,} records quarantined across all tables.")

    return TransformResult(
        guests=guests, properties=properties, rooms=rooms,
        reservations=reservations, payments=payments, rejected=rejected,
    )


if __name__ == "__main__":
    import sys
    raw_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/raw")
    result = run_transformations(raw_dir)
    print(f"guests:       {len(result.guests):,}")
    print(f"properties:   {len(result.properties):,}")
    print(f"rooms:        {len(result.rooms):,}")
    print(f"reservations: {len(result.reservations):,}")
    print(f"payments:     {len(result.payments):,}")
    print(f"rejected:     {len(result.rejected):,}")
