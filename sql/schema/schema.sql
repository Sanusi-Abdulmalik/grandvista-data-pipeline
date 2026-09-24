-- =============================================================================
-- GrandVista Hospitality Group — Centralized Warehouse Schema
-- Target: PostgreSQL 15+
--
-- This schema represents the standardized (post-transformation) warehouse
-- model. Raw/source data is never loaded directly into these tables — it
-- lands first in the `raw` schema (see below) and is promoted here only
-- after passing transformation + data-quality checks.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS warehouse;

-- -----------------------------------------------------------------------------
-- RAW SCHEMA
-- Mirrors the source CSV structure as text columns. No constraints beyond
-- NOT NULL on the natural key — this layer preserves the source data exactly
-- as ingested (including messy statuses, mixed date formats as text, etc.)
-- so the pipeline always has an unmodified copy to re-process from.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw.guests (
    guest_id        TEXT,
    first_name      TEXT,
    last_name       TEXT,
    email           TEXT,
    phone           TEXT,
    country         TEXT,
    _ingested_at    TIMESTAMP DEFAULT now(),
    _source_file    TEXT
);

CREATE TABLE IF NOT EXISTS raw.properties (
    property_id     TEXT,
    property_name   TEXT,
    city            TEXT,
    country         TEXT,
    _ingested_at    TIMESTAMP DEFAULT now(),
    _source_file    TEXT
);

CREATE TABLE IF NOT EXISTS raw.rooms (
    room_id         TEXT,
    property_id     TEXT,
    room_type       TEXT,
    capacity        TEXT,
    _ingested_at    TIMESTAMP DEFAULT now(),
    _source_file    TEXT
);

CREATE TABLE IF NOT EXISTS raw.reservations (
    reservation_id  TEXT,
    guest_id        TEXT,
    property_id     TEXT,
    room_id         TEXT,
    check_in_date   TEXT,
    check_out_date  TEXT,
    booking_status  TEXT,
    booking_date    TEXT,
    _ingested_at    TIMESTAMP DEFAULT now(),
    _source_file    TEXT
);

CREATE TABLE IF NOT EXISTS raw.payments (
    payment_id      TEXT,
    reservation_id  TEXT,
    payment_date    TEXT,
    payment_method  TEXT,
    amount          TEXT,
    payment_status  TEXT,
    _ingested_at    TIMESTAMP DEFAULT now(),
    _source_file    TEXT
);

-- -----------------------------------------------------------------------------
-- WAREHOUSE SCHEMA (standardized, constrained)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS warehouse.guests (
    guest_id        INTEGER PRIMARY KEY,
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    email           VARCHAR(255),
    phone           VARCHAR(50),
    country         VARCHAR(100),
    loaded_at       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS warehouse.properties (
    property_id     INTEGER PRIMARY KEY,
    property_name   VARCHAR(255) NOT NULL,
    city            VARCHAR(100),
    country         VARCHAR(100),
    loaded_at       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS warehouse.rooms (
    room_id         INTEGER PRIMARY KEY,
    property_id     INTEGER NOT NULL REFERENCES warehouse.properties(property_id),
    room_type       VARCHAR(50) NOT NULL,
    capacity        SMALLINT NOT NULL CHECK (capacity > 0),
    loaded_at       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS warehouse.reservations (
    reservation_id  VARCHAR(30) PRIMARY KEY,
    guest_id        INTEGER NOT NULL REFERENCES warehouse.guests(guest_id),
    property_id     INTEGER NOT NULL REFERENCES warehouse.properties(property_id),
    room_id         INTEGER NOT NULL REFERENCES warehouse.rooms(room_id),
    check_in_date   DATE NOT NULL,
    check_out_date  DATE,
    booking_status  VARCHAR(20) NOT NULL CHECK (
        booking_status IN ('confirmed','cancelled','checked_in','checked_out','no_show','pending')
    ),
    booking_date    TIMESTAMP NOT NULL,
    loaded_at       TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT chk_checkout_after_checkin CHECK (check_out_date IS NULL OR check_out_date >= check_in_date)
);

CREATE TABLE IF NOT EXISTS warehouse.payments (
    payment_id      VARCHAR(30) PRIMARY KEY,
    reservation_id  VARCHAR(30) NOT NULL REFERENCES warehouse.reservations(reservation_id),
    payment_date    TIMESTAMP,
    payment_method  VARCHAR(30) NOT NULL,
    amount          DECIMAL(10,2) CHECK (amount >= 0),
    payment_status  VARCHAR(20) NOT NULL CHECK (
        payment_status IN ('paid','failed','refunded','pending')
    ),
    loaded_at       TIMESTAMP NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Indexes for common access patterns
-- -----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_reservations_guest_id ON warehouse.reservations(guest_id);
CREATE INDEX IF NOT EXISTS idx_reservations_property_id ON warehouse.reservations(property_id);
CREATE INDEX IF NOT EXISTS idx_reservations_room_id ON warehouse.reservations(room_id);
CREATE INDEX IF NOT EXISTS idx_reservations_status ON warehouse.reservations(booking_status);
CREATE INDEX IF NOT EXISTS idx_payments_reservation_id ON warehouse.payments(reservation_id);
CREATE INDEX IF NOT EXISTS idx_rooms_property_id ON warehouse.rooms(property_id);

-- -----------------------------------------------------------------------------
-- Quarantine table for records that fail validation during load.
-- Every rejected row is kept (not silently dropped) with the reason, so the
-- pipeline is auditable rather than lossy.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS warehouse.rejected_records (
    id              SERIAL PRIMARY KEY,
    source_table    VARCHAR(50) NOT NULL,
    raw_record      JSONB NOT NULL,
    rejection_reason TEXT NOT NULL,
    rejected_at     TIMESTAMP NOT NULL DEFAULT now()
);

-- =============================================================================
-- PIPELINE PRIVILEGES
-- The pipeline connects as `grandvista` but the schema is owned by `airflow`
-- (the Postgres superuser). Grant the pipeline user access to both schemas.
-- =============================================================================

GRANT USAGE ON SCHEMA raw, warehouse TO grandvista;

-- Existing tables
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON ALL TABLES IN SCHEMA raw TO grandvista;

GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON ALL TABLES IN SCHEMA warehouse TO grandvista;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA warehouse TO grandvista;

-- Future tables created by the current role (airflow, the superuser)
ALTER DEFAULT PRIVILEGES IN SCHEMA raw
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO grandvista;

ALTER DEFAULT PRIVILEGES IN SCHEMA warehouse
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO grandvista;

ALTER DEFAULT PRIVILEGES IN SCHEMA warehouse
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO grandvista;

ALTER SEQUENCE warehouse.rejected_records_id_seq OWNER TO grandvista;
ALTER TABLE warehouse.rejected_records OWNER TO grandvista;