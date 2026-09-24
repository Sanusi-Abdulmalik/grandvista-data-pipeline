"""
generate_data.py

Generates synthetic source data for the GrandVista Hospitality Group
case study: guests, properties, rooms, reservations, payments.

The data intentionally mimics a fragmented multi-system source
environment (booking platform, property management system, customer
database, payment gateway) rather than a single clean export. That
means: inconsistent column naming/casing, mixed date formats, mixed
status vocabularies, duplicate records, missing values, and a small
number of invalid foreign-key references.

Usage:
    python generate_data.py [--seed 42] [--out-dir ../raw]

Output (data/raw/):
    guests.csv
    properties.csv
    rooms.csv
    reservations.csv
    payments.csv
"""

import argparse
import random
import string
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

N_GUESTS = 1200
N_PROPERTIES = 15
N_ROOMS_PER_PROPERTY = (20, 80)   # min, max rooms per property
N_RESERVATIONS = 8000
PAYMENT_MULTI_RECORD_RATE = 0.12  # % of reservations that get >1 payment row

ROOM_TYPES = ["Standard", "Deluxe", "Suite", "Executive", "Family", "Penthouse"]

# Each source system spells statuses differently — this is intentional
# raw-data messiness the transformation layer must standardize.
BOOKING_STATUS_VARIANTS = {
    "confirmed": ["confirmed", "Confirmed", "CONFIRMED", "conf", "CNF"],
    "cancelled": ["cancelled", "canceled", "Cancelled", "CANCELLED", "CXL"],
    "checked_in": ["checked_in", "checked-in", "Checked In", "CHECKED_IN", "CI"],
    "checked_out": ["checked_out", "checked-out", "Checked Out", "CHECKED_OUT", "CO"],
    "no_show": ["no_show", "no-show", "No Show", "NOSHOW", "NS"],
    "pending": ["pending", "Pending", "PENDING", "PND"],
}

PAYMENT_STATUS_VARIANTS = {
    "paid": ["paid", "Paid", "PAID", "success", "SUCCESS"],
    "failed": ["failed", "Failed", "FAILED", "declined", "DECLINED"],
    "refunded": ["refunded", "Refunded", "REFUNDED"],
    "pending": ["pending", "Pending", "PENDING"],
}

PAYMENT_METHODS = ["credit_card", "debit_card", "bank_transfer", "cash", "mobile_money", "paypal"]

DATE_FORMATS = [
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m-%d-%Y",
    "%d-%b-%Y",
    "%Y/%m/%d",
]

TIMESTAMP_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%m-%d-%Y %I:%M %p",
]


def rand_choice_weighted(mapping):
    """Pick a canonical key, then a random raw-string variant for it."""
    key = random.choice(list(mapping.keys()))
    return random.choice(mapping[key])


def format_random_date(d: datetime, formats=DATE_FORMATS) -> str:
    return d.strftime(random.choice(formats))


def format_random_timestamp(d: datetime, formats=TIMESTAMP_FORMATS) -> str:
    return d.strftime(random.choice(formats))


def maybe_blank(value, rate=0.03):
    """Randomly null out a value to simulate missing data."""
    return "" if random.random() < rate else value


def maybe_whitespace_pad(value: str, rate=0.05) -> str:
    if isinstance(value, str) and random.random() < rate:
        return f"  {value}  "
    return value


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def generate_guests(fake: Faker, n: int) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        first = fake.first_name()
        last = fake.last_name()
        email = f"{first.lower()}.{last.lower()}{random.randint(1, 999)}@{fake.free_email_domain()}"
        row = {
            "guest_id": i,
            "first_name": maybe_whitespace_pad(first),
            "last_name": maybe_whitespace_pad(last),
            "email": maybe_blank(email.upper() if random.random() < 0.2 else email),
            "phone": maybe_blank(fake.phone_number(), rate=0.05),
            "country": maybe_blank(fake.country(), rate=0.02),
        }
        rows.append(row)

    df = pd.DataFrame(rows)

    # Inject duplicate guest records (same person, re-entered by another
    # channel with a new guest_id) — a realistic CRM fragmentation issue.
    dup_sample = df.sample(frac=0.03, random_state=1).copy()
    dup_sample["guest_id"] = range(n + 1, n + 1 + len(dup_sample))
    df = pd.concat([df, dup_sample], ignore_index=True)

    return df


def generate_properties(fake: Faker, n: int) -> pd.DataFrame:
    used_names = set()
    rows = []
    for i in range(1, n + 1):
        name = f"GrandVista {fake.city()} {random.choice(['Hotel', 'Resort', 'Suites', 'Plaza'])}"
        while name in used_names:
            name = f"GrandVista {fake.city()} {random.choice(['Hotel', 'Resort', 'Suites', 'Plaza'])}"
        used_names.add(name)
        rows.append({
            "property_id": i,
            "property_name": name,
            "city": fake.city(),
            "country": fake.country(),
        })
    return pd.DataFrame(rows)


def generate_rooms(property_ids: list) -> pd.DataFrame:
    rows = []
    room_id = 1
    for pid in property_ids:
        n_rooms = random.randint(*N_ROOMS_PER_PROPERTY)
        for _ in range(n_rooms):
            rows.append({
                "room_id": room_id,
                "property_id": pid,
                "room_type": random.choice(ROOM_TYPES),
                "capacity": random.choice([1, 2, 2, 3, 4, 6]),
            })
            room_id += 1
    return pd.DataFrame(rows)


def generate_reservations(guest_ids: list, property_ids: list, rooms_df: pd.DataFrame, n: int) -> pd.DataFrame:
    rows = []
    rooms_by_property = rooms_df.groupby("property_id")["room_id"].apply(list).to_dict()

    start_window = datetime(2024, 1, 1)
    end_window = datetime(2026, 9, 23)

    for i in range(1, n + 1):
        pid = random.choice(property_ids)
        # 0.5% of reservations reference a room_id that doesn't belong to
        # this property (or doesn't exist at all) — invalid-reference case.
        if random.random() < 0.005:
            room_id = random.randint(1, rooms_df["room_id"].max() + 500)
        else:
            room_id = random.choice(rooms_by_property.get(pid, rooms_df["room_id"].tolist()))

        # 0.3% reference a guest_id that doesn't exist in guests.csv
        if random.random() < 0.003:
            gid = max(guest_ids) + random.randint(1, 50)
        else:
            gid = random.choice(guest_ids)

        booking_days_out = random.randint(1, 400)
        booking_dt = start_window + timedelta(days=random.randint(0, (end_window - start_window).days))
        check_in = booking_dt + timedelta(days=random.randint(1, 60))
        stay_length = random.randint(1, 10)
        check_out = check_in + timedelta(days=stay_length)

        reservation_id = f"RES-{uuid.uuid4().hex[:10].upper()}"

        rows.append({
            "reservation_id": reservation_id,
            "guest_id": gid,
            "property_id": pid,
            "room_id": room_id,
            "check_in_date": format_random_date(check_in),
            "check_out_date": maybe_blank(format_random_date(check_out), rate=0.01),
            "booking_status": rand_choice_weighted(BOOKING_STATUS_VARIANTS),
            "booking_date": format_random_timestamp(booking_dt),
        })

    df = pd.DataFrame(rows)

    # Inject exact-duplicate reservation rows (source system resent the
    # same record on retry) — ~1.5% of rows duplicated.
    dup_sample = df.sample(frac=0.015, random_state=2)
    df = pd.concat([df, dup_sample], ignore_index=True)

    return df


def generate_payments(reservations_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    payment_counter = 1

    for _, res in reservations_df.iterrows():
        n_payments = 1
        if random.random() < PAYMENT_MULTI_RECORD_RATE:
            n_payments = random.choice([2, 2, 3])  # e.g. deposit + balance, or failed retry

        # ~1% of reservations have no payment row at all (booking made,
        # payment not yet captured / lost record) — deliberately skipped.
        if random.random() < 0.01:
            continue

        remaining_status_pool = list(PAYMENT_STATUS_VARIANTS.keys())
        for _ in range(n_payments):
            payment_id = f"PMT-{uuid.uuid4().hex[:10].upper()}"
            amount = round(random.uniform(45, 1800), 2)
            status_key = random.choice(remaining_status_pool)
            rows.append({
                "payment_id": payment_id,
                "reservation_id": res["reservation_id"],
                "payment_date": maybe_blank(format_random_timestamp(
                    datetime.strptime(res["booking_date"].split(" ")[0] if " " in res["booking_date"] else res["booking_date"], "%Y-%m-%d")
                    if False else datetime(2024, 1, 1) + timedelta(days=random.randint(0, 900))
                ), rate=0.02),
                "payment_method": random.choice(PAYMENT_METHODS),
                "amount": maybe_blank(amount, rate=0.01),
                "payment_status": rand_choice_weighted(PAYMENT_STATUS_VARIANTS),
            })
            payment_counter += 1

    df = pd.DataFrame(rows)

    # A handful of payments referencing a reservation_id that does not
    # exist (payment gateway record with no matching booking) — invalid FK.
    orphan_rows = []
    for _ in range(15):
        orphan_rows.append({
            "payment_id": f"PMT-{uuid.uuid4().hex[:10].upper()}",
            "reservation_id": f"RES-{uuid.uuid4().hex[:10].upper()}",
            "payment_date": format_random_timestamp(datetime(2025, 1, 1) + timedelta(days=random.randint(0, 600))),
            "payment_method": random.choice(PAYMENT_METHODS),
            "amount": round(random.uniform(45, 1800), 2),
            "payment_status": rand_choice_weighted(PAYMENT_STATUS_VARIANTS),
        })
    df = pd.concat([df, pd.DataFrame(orphan_rows)], ignore_index=True)

    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", type=str, default=str(Path(__file__).resolve().parent.parent / "raw"))
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    fake = Faker()
    Faker.seed(args.seed)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Generating guests...")
    guests_df = generate_guests(fake, N_GUESTS)

    print("Generating properties...")
    properties_df = generate_properties(fake, N_PROPERTIES)

    print("Generating rooms...")
    rooms_df = generate_rooms(properties_df["property_id"].tolist())

    print("Generating reservations...")
    reservations_df = generate_reservations(
        guests_df["guest_id"].tolist(),
        properties_df["property_id"].tolist(),
        rooms_df,
        N_RESERVATIONS,
    )

    print("Generating payments...")
    payments_df = generate_payments(reservations_df)

    guests_df.to_csv(out_dir / "guests.csv", index=False)
    properties_df.to_csv(out_dir / "properties.csv", index=False)
    rooms_df.to_csv(out_dir / "rooms.csv", index=False)
    reservations_df.to_csv(out_dir / "reservations.csv", index=False)
    payments_df.to_csv(out_dir / "payments.csv", index=False)

    print(f"\nDone. Files written to {out_dir.resolve()}")
    print(f"  guests:       {len(guests_df):,} rows")
    print(f"  properties:   {len(properties_df):,} rows")
    print(f"  rooms:        {len(rooms_df):,} rows")
    print(f"  reservations: {len(reservations_df):,} rows")
    print(f"  payments:     {len(payments_df):,} rows")


if __name__ == "__main__":
    main()
