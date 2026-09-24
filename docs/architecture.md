# Architecture

## Why this architecture

The case study frames the problem as **multi-source integration**, not
analytics: guest, property, room, reservation, and payment data would, in
reality, come from separate systems (a booking engine, a PMS, a CRM, a
payment gateway) with different conventions. The pipeline is built around
that premise, with each stage owning one responsibility:

1. **Generation** (`data/generation/generate_data.py`) stands in for "the
   source systems" and deliberately produces the kind of mess a real
   multi-system environment would produce (see `data_generation.md`).
2. **Ingestion** (`src/ingestion/ingest.py`) is a "dumb" bronze loader — it
   copies the raw CSVs into `raw.*` Postgres tables as text, verbatim,
   with no cleaning. This matters: if a transformation bug is discovered
   later, the pipeline can be re-run from an unmodified copy of what the
   source actually sent, instead of from already-lossy cleaned data.
3. **Transformation** (`src/transformation/transform.py`) is pure pandas
   with no database dependency, which is what makes it unit-testable
   (`tests/test_transform.py`) independent of a running Postgres instance.
   It standardizes column casing, whitespace, status vocabularies, and
   date formats; drops exact duplicates; and separates valid records from
   invalid ones rather than silently coercing or dropping bad data.
4. **Load** (`src/transformation/load.py`) writes the transformed,
   already-validated-shape data into the constrained `warehouse.*` tables
   (PK/FK/CHECK constraints — see `sql/schema/schema.sql`), in
   dependency order, and writes every quarantined record to
   `warehouse.rejected_records`.
5. **Validation** (`src/validation/validate.py`) is a second,
   database-level quality gate that runs *after* load — it checks the
   state of the warehouse itself (uniqueness, referential integrity,
   controlled vocabularies, date logic) rather than trusting that the
   Python transformation caught everything. It's a deliberate hard gate
   in the DAG: if it fails, dbt never runs and bad data is never
   presented as "delivered."
6. **dbt** (`dbt/`) sits on top of the already-clean warehouse tables. Its
   job here is less about further cleaning and more about (a) documented,
   testable staging views that give the warehouse a stable modeling
   interface, (b) analytical marts (`fct_reservations`, `dim_guests`,
   `agg_property_performance`) built with `ref()`/lineage, and (c) a second,
   declarative layer of schema tests (`uniqueness`, `not_null`,
   `relationships`, `accepted_values`) that double-check the same
   invariants the Python validator checks, using a different mechanism —
   so a bug in one layer's logic is unlikely to also be a bug in the other's.
7. **Airflow** (`airflow/dags/grandvista_pipeline_dag.py`) orchestrates all
   of the above as discrete, retryable tasks with explicit dependencies,
   rather than one monolithic script, so a failure at any stage is visible
   and isolated in the Airflow UI.

## Why two Postgres schemas (`raw` and `warehouse`) instead of one

Loading straight into a constrained warehouse table would mean a single bad
row (wrong type, broken FK) aborts the whole batch load, or — worse — gets
silently coerced. Keeping `raw` as an unconstrained, purely textual mirror
of the source means ingestion can never fail on data-quality grounds (it's
just copying strings), and all quality enforcement happens explicitly and
visibly in the transformation/validation stages, where failures are
individually attributable to a row and a reason.

## Why the rejected-records table

A pipeline that drops bad rows with no trace is not auditable — nobody can
answer "how much of the source data did we actually lose, and why?" every
rejected row is preserved (`warehouse.rejected_records`, as JSONB) with a
human-readable rejection reason, so:
- volume of rejected data is visible and can be alerted on,
- specific rows can be traced back to their rejection cause for debugging
  or for pushing feedback to a (hypothetical) upstream source system.

## Failure handling

- Each Airflow task uses `retries: 2` with a 3-minute delay (transient
  issues like a momentarily-unavailable Postgres connection self-heal).
- `run_data_quality_checks` is a hard gate — a non-zero exit fails the task
  and the DAG does not proceed to dbt, so bad data never reaches the
  documented/tested marts layer silently.
- Ingestion, transformation, and load are separated into distinct tasks so
  a failure in one is immediately attributable rather than buried inside a
  single "run everything" task.
- Nothing is retried indefinitely; after retries are exhausted the task
  (and DAG run) is marked failed and surfaces in the Airflow UI, rather
  than the pipeline reporting false success.

## Limitations, Assumptions & Extending the Pipeline

This is an assessment-scale project; a few things are intentionally
simplified and would be addressed differently at production scale:

- **Full-refresh loads, not incremental.** Every run truncates and
  reloads `raw.*` and `warehouse.*`. At real volume this would become a
  CDC/incremental model (e.g. `booking_date`-based watermarking, or
  upserts keyed on the natural IDs) rather than full truncation.
- **MinIO is provisioned but not yet wired into the DAG as a landing
  zone.** The case study calls for MinIO as S3-compatible raw storage;
  the natural extension is to have `generate_synthetic_data` (or a
  preceding "source drop") land files in MinIO first, and have
  `ingest_raw_data` read from there instead of the local filesystem —
  this keeps ingestion decoupled from wherever files are dropped.
- **Single Postgres instance hosts both Airflow's metadata DB and the
  warehouse.** Fine for local/assessment use; in production these would
  be separate instances so warehouse load and Airflow's own scheduling
  don't compete for the same database resources.
- **Assumes the `properties`/`rooms` reference data is small and mostly
  static.** These are loaded via full truncate/reload same as the
  transactional tables. At scale they'd be handled as slowly-changing
  dimensions instead.
- **No secrets manager.** Credentials come from `.env` (gitignored) for
  local reproducibility. Production would use a secrets manager (AWS
  Secrets Manager, Vault, Airflow Connections with a backend) instead of
  plain environment variables.
- **Scaling to larger volumes:** the transformation stage is pandas-based
  (in-memory), which is fine at this data's scale but would need to move
  to chunked processing or a distributed engine (e.g. Spark, or pushing
  the standardization logic into SQL/dbt models directly) if source
  volumes grew by orders of magnitude.
