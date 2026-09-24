-- Grain: one row per reservation. Central fact table joining guest,
-- property and room dimensions, with paid-amount aggregated from the
-- (possibly multi-row) payments table.

with reservations as (
    select * from {{ ref('stg_reservations') }}
),

payments_agg as (
    select
        reservation_id,
        sum(case when payment_status = 'paid' then amount else 0 end) as amount_paid,
        count(*) as payment_record_count,
        max(payment_date) as last_payment_date
    from {{ ref('stg_payments') }}
    group by reservation_id
)

select
    r.reservation_id,
    r.guest_id,
    r.property_id,
    r.room_id,
    r.check_in_date,
    r.check_out_date,
    r.nights_booked,
    r.booking_status,
    r.booking_date,
    coalesce(p.amount_paid, 0) as amount_paid,
    coalesce(p.payment_record_count, 0) as payment_record_count,
    p.last_payment_date
from reservations r
left join payments_agg p on r.reservation_id = p.reservation_id
