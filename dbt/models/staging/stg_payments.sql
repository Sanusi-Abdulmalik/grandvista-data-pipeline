select
    payment_id,
    reservation_id,
    payment_date,
    payment_method,
    amount,
    payment_status
from {{ source('warehouse', 'payments') }}
