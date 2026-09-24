# Technical Decisions

This document distinguishes what the case study **required** from the
**engineering decisions** made to satisfy those requirements, and covers
the points the assessment brief asks a final technical document to address.

## What the case study required vs. what was decided

**Required by the brief:** generate synthetic guests/properties/rooms/
reservations/payments data per the data dictionary; ingest it; transform
and standardize it; run data-quality checks; load into PostgreSQL;
represent the data model; orchestrate with Airflow; use dbt for
transformation/testing; provide a reproducible Docker environment; test
key components; document everything.

**Left to engineering judgment (and the decisions made here):**
- *Two-schema (`raw`/`warehouse`) design* rather than loading directly
  into constrained tables — see `architecture.md` for the rationale
  (isolates "did ingestion work" from "was the data good").
- *Quarantine-with-reason rather than drop-and-log* for bad records
  (`warehouse.rejected_records`) — chosen so rejected volume and cause
  are queryable, not just visible in logs that rotate away.
- *Two independent quality-checking mechanisms* (Python `validate.py` +
  dbt schema tests) instead of relying on dbt alone, so a bug specific to
  one test implementation doesn't become an undetected gap.
- *Splitting transformation (pure pandas, no DB) from load (DB writes)*
  into separate modules specifically so the cleaning logic could be unit
  tested without a live Postgres instance.
- *Which data-quality issues to inject and at what rates* — chosen to
  cover every category the brief's "Context Behind the Issue" section
  names, at rates realistic enough to be non-trivial without dominating
  the dataset (full rationale in `data_generation.md`).
- *dbt's role* — used here as a documented staging/marts layer plus a
  second testing mechanism on top of already-cleaned data, rather than
  re-implementing the cleaning logic in SQL. This was a judgment call:
  the cleaning logic (fuzzy status/date normalization) is inherently more
  natural to express in Python than in SQL, so Python owns it and dbt
  owns modeling + testing on the result.

## How the source data was generated

Covered in full in `data_generation.md`. In short: Faker + seeded
randomness produces plausible entities and relationships, then a
second pass deliberately injects the specific categories of messiness
(status/date format inconsistency, duplicates, missing values, invalid
references) the case study calls out, at documented rates.

## How data-quality problems were handled

Covered in full in `data_quality.md`. In short: caught and quarantined
(not silently dropped or coerced) at transform time, re-verified at the
database level post-load as a hard pipeline gate, and re-verified again
by dbt tests — three layers, each checking the same invariants a
different way.

## How relationships were maintained

Foreign keys are validated explicitly in `transform.py` against the set
of valid parent IDs *already produced earlier in the same pipeline run*
(e.g. reservations are checked against the guest/property/room IDs that
survived the guests/properties/rooms transforms), so referential
integrity is enforced relative to what actually made it into the
warehouse — not just the shape of the raw file. The warehouse schema
additionally enforces this at the database level via `REFERENCES`
constraints as a last line of defense.

## How the pipeline handles failures

See `architecture.md` §"Failure handling" — per-task retries for
transient failures, a hard data-quality gate before the dbt stage,
and isolated/attributable Airflow tasks rather than one monolithic
script.

## How the pipeline could be extended for larger data volumes

- Move from full truncate/reload to incremental/CDC loading keyed on
  `booking_date`/an updated-at watermark.
- Move the pandas-based transformation logic into chunked processing or
  push more of it into SQL/dbt models directly (dbt can express most of
  the standardization as SQL `CASE`/`regexp` logic; Python was chosen
  here for testability and because the messiness patterns are easier to
  express and unit-test as pandas functions at this scale).
- Wire MinIO in as the actual landing zone ahead of ingestion (currently
  provisioned in `docker-compose.yml` per the required tech stack but not
  yet in the DAG's data path — see `architecture.md` limitations).
- Separate the Airflow metadata database from the warehouse database
  (currently share one Postgres instance for local-dev simplicity).
- Partition `warehouse.reservations`/`payments` by date range once
  volume justifies it.

## Limitations and assumptions

See `architecture.md` §"Limitations, Assumptions & Extending the
Pipeline" for the complete list (full-refresh loading, MinIO not yet in
the DAG's data path, shared Postgres instance, no secrets manager, and
pandas' in-memory processing model).
