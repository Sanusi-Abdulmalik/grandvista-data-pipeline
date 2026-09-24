# Data Generation

Source data is generated programmatically by `data/generation/generate_data.py`
using Faker, Pandas, and NumPy, seeded (`--seed`, default `42`) for
reproducibility. It is **not** meant to be clean — it stands in for what
several independent, loosely-coordinated systems (a booking platform, a
property management system, a CRM, a payment gateway) would actually
produce.

## Record counts (default seed)

| Table | Rows generated | Notes |
|---|---|---|
| `guests` | ~1,236 | 1,200 base guests + ~3% re-entered as duplicate records under new `guest_id`s |
| `properties` | 15 | |
| `rooms` | ~690 | 20–80 rooms randomly assigned per property |
| `reservations` | ~8,120 | 8,000 base reservations + ~1.5% exact-duplicate rows |
| `payments` | ~9,400 | ~12% of reservations get 2–3 payment rows (deposit/balance/retry); ~1% of reservations get none |

## How relationships were maintained

- `rooms.property_id` is always drawn from the generated `properties` set
  (with a very small deliberately-invalid exception — see below).
- `reservations.room_id` is drawn from the rooms belonging to the chosen
  `reservations.property_id`, so room and property are consistent for the
  large majority of records.
- `payments.reservation_id` is drawn from the generated `reservations` set
  for all but a small, deliberately-invalid set of orphan rows.
- `check_out_date` is always generated after `check_in_date` for the
  "valid" path; a separate, small invalid-date scenario is not injected
  at generation time — date-logic errors instead arise naturally from the
  mixed-format parsing being imperfect, and are caught in transformation.

## How realistic values were generated

- Names, emails, phone numbers, cities, and countries come from Faker.
- Property names are templated (`GrandVista {city} {Hotel|Resort|Suites|Plaza}`)
  to look like a real multi-property brand naming convention.
- Payment amounts are randomized within a plausible per-stay range
  (45–1,800, arbitrary currency).
- Booking/payment dates are spread across a ~2.5 year window
  (Jan 2024 – Sep 2026) so the data has a realistic time distribution
  rather than clustering on one day.

## Data-quality issues intentionally introduced (and why each is realistic)

| Issue | How it's introduced | Why it's realistic |
|---|---|---|
| Inconsistent status vocabulary | `booking_status` and `payment_status` are each written using one of 4–6 spelling/casing variants per canonical state (e.g. `confirmed` / `Confirmed` / `CONFIRMED` / `conf` / `CNF`) | Different source systems (a legacy PMS vs. a modern booking API) almost never share an enum convention |
| Mixed date/timestamp formats | Dates are written in 5 different formats (`%Y-%m-%d`, `%d/%m/%Y`, `%m-%d-%Y`, `%d-%b-%Y`, `%Y/%m/%d`), timestamps in 3 | Region-specific systems (US vs. EU date conventions) and different export tools rarely agree on a format |
| Missing values | ~2–5% of optional fields (`email`, `phone`, `country`, occasional `check_out_date`/`payment_date`/`amount`) are blanked | Guest-entered or partially-integrated fields are routinely incomplete |
| Duplicate guest records | ~3% of guests are re-inserted under a new `guest_id` | A guest booking through two different channels (web vs. call center) often creates two CRM records rather than being deduplicated at entry |
| Duplicate reservation rows | ~1.5% of reservations appear twice, identical | Simulates a booking-platform webhook or batch export retry that resent the same record |
| Invalid foreign keys | ~0.5% of `reservations.room_id`, ~0.3% of `reservations.guest_id`, and 15 fixed `payments.reservation_id` rows reference IDs that don't exist | Represents late-arriving or out-of-order data (e.g. a payment webhook arriving before the booking record finishes syncing) |
| Whitespace padding | ~5% of guest names get leading/trailing whitespace | Common artifact of manual data entry or CSV export/import round-trips |
| Missing payment records | ~1% of reservations have zero payment rows | Represents a booking made but payment capture not yet completed, or a lost/dropped record |

The candidate does not claim any of this reproduces a real company's actual
data — it's designed to exercise every category of problem the case study's
"Context Behind the Issue" section calls out (different schemas, inconsistent
statuses, mixed date formats, inconsistent identifiers, duplicates, missing
fields, invalid references, staggered arrival times).
