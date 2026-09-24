-- Thin passthrough over the already-standardized warehouse.guests table.
-- Exists primarily to give dbt lineage/documentation ownership of the
-- guest entity and a stable interface for downstream marts.

select
    guest_id,
    first_name,
    last_name,
    email,
    phone,
    country
from {{ source('warehouse', 'guests') }}
