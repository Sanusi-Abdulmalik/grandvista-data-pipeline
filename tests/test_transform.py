"""
Unit tests for src/transformation/transform.py.

Run with: pytest tests/ -v
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
from src.transformation.transform import (
    transform_guests,
    transform_properties,
    transform_rooms,
    transform_reservations,
    transform_payments,
    _standardize_status,
    _parse_flexible_date,
    BOOKING_STATUS_MAP,
    DATE_FORMATS_TO_TRY,
)


# ---------------------------------------------------------------------------
# Status / date normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw_value,expected", [
    ("Confirmed", "confirmed"),
    ("CNF", "confirmed"),
    ("cancelled", "cancelled"),
    ("CANCELED", "cancelled"),
    ("checked-in", "checked_in"),
    ("garbage_value", "unknown"),
    (None, "unknown"),
    ("", "unknown"),
])
def test_standardize_booking_status(raw_value, expected):
    assert _standardize_status(raw_value, BOOKING_STATUS_MAP) == expected


@pytest.mark.parametrize("raw_value", [
    "2025-01-15", "15/01/2025", "01-15-2025", "15-Jan-2025", "2025/01/15",
])
def test_parse_flexible_date_formats(raw_value):
    result = _parse_flexible_date(raw_value, DATE_FORMATS_TO_TRY)
    assert result is not None
    assert result.year == 2025 and result.month == 1 and result.day == 15


def test_parse_flexible_date_handles_blank():
    assert _parse_flexible_date("", DATE_FORMATS_TO_TRY) is None
    assert _parse_flexible_date(None, DATE_FORMATS_TO_TRY) is None


# ---------------------------------------------------------------------------
# transform_guests
# ---------------------------------------------------------------------------

def test_transform_guests_drops_rows_missing_required_fields():
    raw = pd.DataFrame([
        {"guest_id": "1", "first_name": "Ada", "last_name": "Lovelace", "email": "a@b.com", "phone": "123", "country": "UK"},
        {"guest_id": "", "first_name": "No", "last_name": "Id", "email": "", "phone": "", "country": ""},
        {"guest_id": "2", "first_name": "", "last_name": "Missing", "email": "", "phone": "", "country": ""},
    ])
    rejected = []
    result = transform_guests(raw, rejected)
    assert len(result) == 1
    assert result.iloc[0]["guest_id"] == 1
    assert len(rejected) == 2


def test_transform_guests_deduplicates_on_guest_id():
    raw = pd.DataFrame([
        {"guest_id": "1", "first_name": "Ada", "last_name": "Lovelace", "email": "a@b.com", "phone": "", "country": ""},
        {"guest_id": "1", "first_name": "Ada", "last_name": "Lovelace", "email": "a@b.com", "phone": "", "country": ""},
    ])
    result = transform_guests(raw, [])
    assert len(result) == 1


# ---------------------------------------------------------------------------
# transform_rooms — referential integrity
# ---------------------------------------------------------------------------

def test_transform_rooms_rejects_invalid_property_fk():
    raw = pd.DataFrame([
        {"room_id": "1", "property_id": "1", "room_type": "Standard", "capacity": "2"},
        {"room_id": "2", "property_id": "999", "room_type": "Standard", "capacity": "2"},  # invalid FK
    ])
    rejected = []
    result = transform_rooms(raw, valid_property_ids={1}, rejected=rejected)
    assert len(result) == 1
    assert result.iloc[0]["room_id"] == 1
    assert len(rejected) == 1
    assert "property_id not found" in rejected[0].reason


def test_transform_rooms_rejects_non_positive_capacity():
    raw = pd.DataFrame([
        {"room_id": "1", "property_id": "1", "room_type": "Standard", "capacity": "0"},
    ])
    result = transform_rooms(raw, valid_property_ids={1}, rejected=[])
    assert len(result) == 0


# ---------------------------------------------------------------------------
# transform_reservations
# ---------------------------------------------------------------------------

def _base_reservation_row(**overrides):
    row = {
        "reservation_id": "RES-1",
        "guest_id": "1",
        "property_id": "1",
        "room_id": "1",
        "check_in_date": "2025-06-01",
        "check_out_date": "2025-06-05",
        "booking_status": "Confirmed",
        "booking_date": "2025-05-20 10:00:00",
    }
    row.update(overrides)
    return row


def test_transform_reservations_happy_path():
    raw = pd.DataFrame([_base_reservation_row()])
    result = transform_reservations(raw, valid_guest_ids={1}, valid_property_ids={1}, valid_room_ids={1}, rejected=[])
    assert len(result) == 1
    assert result.iloc[0]["booking_status"] == "confirmed"


def test_transform_reservations_rejects_checkout_before_checkin():
    raw = pd.DataFrame([_base_reservation_row(check_in_date="2025-06-05", check_out_date="2025-06-01")])
    rejected = []
    result = transform_reservations(raw, valid_guest_ids={1}, valid_property_ids={1}, valid_room_ids={1}, rejected=rejected)
    assert len(result) == 0
    assert any("check_out_date before check_in_date" in r.reason for r in rejected)


def test_transform_reservations_rejects_invalid_guest_fk():
    raw = pd.DataFrame([_base_reservation_row(guest_id="999")])
    rejected = []
    result = transform_reservations(raw, valid_guest_ids={1}, valid_property_ids={1}, valid_room_ids={1}, rejected=rejected)
    assert len(result) == 0
    assert len(rejected) == 1


def test_transform_reservations_deduplicates_exact_duplicates():
    raw = pd.DataFrame([_base_reservation_row(), _base_reservation_row()])
    result = transform_reservations(raw, valid_guest_ids={1}, valid_property_ids={1}, valid_room_ids={1}, rejected=[])
    assert len(result) == 1


# ---------------------------------------------------------------------------
# transform_payments
# ---------------------------------------------------------------------------

def test_transform_payments_rejects_invalid_reservation_fk():
    raw = pd.DataFrame([
        {"payment_id": "PMT-1", "reservation_id": "RES-999", "payment_date": "2025-06-01 10:00:00",
         "payment_method": "credit_card", "amount": "100.00", "payment_status": "paid"},
    ])
    rejected = []
    result = transform_payments(raw, valid_reservation_ids={"RES-1"}, rejected=rejected)
    assert len(result) == 0
    assert len(rejected) == 1


def test_transform_payments_rejects_negative_amount():
    raw = pd.DataFrame([
        {"payment_id": "PMT-1", "reservation_id": "RES-1", "payment_date": "2025-06-01 10:00:00",
         "payment_method": "credit_card", "amount": "-50.00", "payment_status": "paid"},
    ])
    result = transform_payments(raw, valid_reservation_ids={"RES-1"}, rejected=[])
    assert len(result) == 0


def test_transform_payments_accepts_valid_record():
    raw = pd.DataFrame([
        {"payment_id": "PMT-1", "reservation_id": "RES-1", "payment_date": "2025-06-01 10:00:00",
         "payment_method": "credit_card", "amount": "150.00", "payment_status": "Paid"},
    ])
    result = transform_payments(raw, valid_reservation_ids={"RES-1"}, rejected=[])
    assert len(result) == 1
    assert result.iloc[0]["payment_status"] == "paid"
