"""
validate.py

Data Quality validation stage.

Runs a set of automated checks against the loaded `warehouse` tables in
PostgreSQL and reports pass/fail for each. Complements (does not
replace) the dbt tests in dbt/models — this module is meant to run as
a discrete Airflow task that can hard-fail the DAG before data is
considered "delivered", independent of whether dbt is invoked.

Checks performed, per table:
    - Primary key uniqueness
    - Required (NOT NULL) fields populated
    - Foreign key referential integrity
    - Controlled vocabulary membership (booking_status, payment_status)
    - Date logic (check_out_date >= check_in_date)
    - No duplicate rows

Usage:
    python validate.py            # exits 1 if any check fails
"""

import logging
import os
import sys

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
logger = logging.getLogger("validate")


def get_engine():
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "grandvista")
    user = os.getenv("POSTGRES_USER", "grandvista")
    password = os.getenv("POSTGRES_PASSWORD", "grandvista")
    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
    return create_engine(url)


CHECKS = [
    # (description, SQL that returns a count of FAILING rows — must be 0 to pass)
    ("guests: guest_id is unique",
     "SELECT count(*) FROM (SELECT guest_id FROM warehouse.guests GROUP BY guest_id HAVING count(*) > 1) t"),
    ("guests: first_name/last_name not null",
     "SELECT count(*) FROM warehouse.guests WHERE first_name IS NULL OR last_name IS NULL"),

    ("properties: property_id is unique",
     "SELECT count(*) FROM (SELECT property_id FROM warehouse.properties GROUP BY property_id HAVING count(*) > 1) t"),

    ("rooms: room_id is unique",
     "SELECT count(*) FROM (SELECT room_id FROM warehouse.rooms GROUP BY room_id HAVING count(*) > 1) t"),
    ("rooms: property_id references an existing property",
     "SELECT count(*) FROM warehouse.rooms r LEFT JOIN warehouse.properties p ON r.property_id = p.property_id WHERE p.property_id IS NULL"),
    ("rooms: capacity is positive",
     "SELECT count(*) FROM warehouse.rooms WHERE capacity <= 0"),

    ("reservations: reservation_id is unique",
     "SELECT count(*) FROM (SELECT reservation_id FROM warehouse.reservations GROUP BY reservation_id HAVING count(*) > 1) t"),
    ("reservations: guest_id references an existing guest",
     "SELECT count(*) FROM warehouse.reservations r LEFT JOIN warehouse.guests g ON r.guest_id = g.guest_id WHERE g.guest_id IS NULL"),
    ("reservations: property_id references an existing property",
     "SELECT count(*) FROM warehouse.reservations r LEFT JOIN warehouse.properties p ON r.property_id = p.property_id WHERE p.property_id IS NULL"),
    ("reservations: room_id references an existing room",
     "SELECT count(*) FROM warehouse.reservations r LEFT JOIN warehouse.rooms rm ON r.room_id = rm.room_id WHERE rm.room_id IS NULL"),
    ("reservations: booking_status is a known value",
     "SELECT count(*) FROM warehouse.reservations WHERE booking_status NOT IN ('confirmed','cancelled','checked_in','checked_out','no_show','pending')"),
    ("reservations: check_out_date is not before check_in_date",
     "SELECT count(*) FROM warehouse.reservations WHERE check_out_date IS NOT NULL AND check_out_date < check_in_date"),
    ("reservations: check_in_date and booking_date are not null",
     "SELECT count(*) FROM warehouse.reservations WHERE check_in_date IS NULL OR booking_date IS NULL"),

    ("payments: payment_id is unique",
     "SELECT count(*) FROM (SELECT payment_id FROM warehouse.payments GROUP BY payment_id HAVING count(*) > 1) t"),
    ("payments: reservation_id references an existing reservation",
     "SELECT count(*) FROM warehouse.payments p LEFT JOIN warehouse.reservations r ON p.reservation_id = r.reservation_id WHERE r.reservation_id IS NULL"),
    ("payments: payment_status is a known value",
     "SELECT count(*) FROM warehouse.payments WHERE payment_status NOT IN ('paid','failed','refunded','pending')"),
    ("payments: amount is not negative",
     "SELECT count(*) FROM warehouse.payments WHERE amount < 0"),
]


def run_checks() -> bool:
    engine = get_engine()
    all_passed = True

    with engine.connect() as conn:
        for description, sql in CHECKS:
            failing_count = conn.execute(text(sql)).scalar()
            status = "PASS" if failing_count == 0 else "FAIL"
            if failing_count != 0:
                all_passed = False
            logger.info(f"[{status}] {description} (failing rows: {failing_count})")

    return all_passed


if __name__ == "__main__":
    passed = run_checks()
    if not passed:
        logger.error("One or more data quality checks failed.")
        sys.exit(1)
    logger.info("All data quality checks passed.")
