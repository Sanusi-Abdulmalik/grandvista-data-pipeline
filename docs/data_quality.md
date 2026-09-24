# Data Quality

Data quality is enforced at three layers, deliberately redundant so a bug
in one layer's logic doesn't become a silent hole:

1. **Transformation-time quarantine** (`src/transformation/transform.py`) —
   rows are checked and separated *while* being cleaned.
2. **Post-load Python validation** (`src/validation/validate.py`) — checks
   the state of the warehouse tables after loading, as a hard Airflow gate.
3. **dbt schema tests** (`dbt/models/**/schema.yml`) — declarative tests
   run against the staging/mart models as part of `dbt test`.

## What's checked, and where a failing row ends up

| Check | Table(s) | Layer(s) | On failure |
|---|---|---|---|
| Primary key present & unique | all | transform, validate, dbt (`unique`, `not_null`) | row quarantined at transform time (dup rows dropped, keep-first); DAG fails if any duplicate reaches the warehouse |
| Required fields not null (`first_name`, `check_in_date`, `booking_date`, `amount`, ...) | all | transform, validate, dbt (`not_null`) | row quarantined at transform time |
| Foreign key references an existing parent row | `rooms→properties`, `reservations→{guests,properties,rooms}`, `payments→reservations` | transform, validate, dbt (`relationships`) | row quarantined at transform time |
| Controlled vocabulary membership | `reservations.booking_status`, `payments.payment_status` | transform, validate, dbt (`accepted_values`) | any raw value not in the known mapping (see `transform.py::BOOKING_STATUS_MAP` / `PAYMENT_STATUS_MAP`) is normalized to `"unknown"` and then quarantined |
| Date logic (`check_out_date >= check_in_date`) | `reservations` | transform, validate | row quarantined at transform time |
| Non-negative payment amount | `payments` | transform | row quarantined at transform time |
| Exact-duplicate records | `guests`, `reservations`, `payments` | transform (`drop_duplicates` on the natural key) | duplicate dropped, first occurrence kept |

## Why quarantine instead of drop-and-forget

Every rejected row is written to `warehouse.rejected_records`:

```sql
CREATE TABLE warehouse.rejected_records (
    id                SERIAL PRIMARY KEY,
    source_table      VARCHAR(50) NOT NULL,
    raw_record        JSONB NOT NULL,
    rejection_reason  TEXT NOT NULL,
    rejected_at       TIMESTAMP NOT NULL DEFAULT now()
);
```

This makes the cleaning process auditable: you can answer "how many
records did we reject, from which table, and why?" with a single query
(`SELECT source_table, rejection_reason, count(*) FROM
warehouse.rejected_records GROUP BY 1, 2 ORDER BY 3 DESC`), rather than
having to trust that the pipeline "did the right thing" with no evidence.

## What happens when a check fails

- **Transform-time checks** never fail the pipeline — they route the
  offending row to the rejected list and let the rest of the batch
  proceed, since one bad reservation record shouldn't block 8,000 good
  ones.
- **`run_data_quality_checks` (post-load, database-level)** is a hard
  gate in the Airflow DAG. If any check in `src/validation/validate.py`
  finds failing rows *in the warehouse itself* (which would indicate a
  bug in the transformation logic, not just messy source data — since
  transformation should have already caught these), the task exits
  non-zero, the DAG run is marked failed, and `dbt_run`/`dbt_test` never
  execute. Bad data is never presented as "delivered."
- **`dbt test`** runs last and would also fail the DAG on any violation.
  It's intentionally redundant with the Python validator: they check
  overlapping invariants using different mechanisms (raw SQL counts vs.
  dbt's generic test macros), so a logic bug specific to one
  implementation is unlikely to also exist in the other.
