select
    reservation_id,
    guest_id,
    property_id,
    room_id,
    check_in_date,
    check_out_date,
    booking_status,
    booking_date,
    (check_out_date - check_in_date) as nights_booked
from {{ source('warehouse', 'reservations') }}
