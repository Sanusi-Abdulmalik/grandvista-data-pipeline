select
    room_id,
    property_id,
    room_type,
    capacity
from {{ source('warehouse', 'rooms') }}
