select
    property_id,
    property_name,
    city,
    country
from {{ source('warehouse', 'properties') }}
