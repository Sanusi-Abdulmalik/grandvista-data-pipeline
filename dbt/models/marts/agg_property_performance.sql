-- Grain: one row per property. Reservation and revenue summary used for
-- cross-property comparison.

with reservations as (
    select * from {{ ref('fct_reservations') }}
),

property_agg as (
    select
        property_id,
        count(*) as total_reservations,
        count(*) filter (where booking_status in ('checked_in', 'checked_out')) as completed_stays,
        count(*) filter (where booking_status = 'cancelled') as cancelled_reservations,
        count(*) filter (where booking_status = 'no_show') as no_show_reservations,
        sum(amount_paid) as total_revenue,
        avg(nights_booked) as avg_nights_booked
    from reservations
    group by property_id
)

select
    p.property_id,
    p.property_name,
    p.city,
    p.country,
    (select count(*) from {{ ref('stg_rooms') }} rm where rm.property_id = p.property_id) as room_count,
    coalesce(a.total_reservations, 0) as total_reservations,
    coalesce(a.completed_stays, 0) as completed_stays,
    coalesce(a.cancelled_reservations, 0) as cancelled_reservations,
    coalesce(a.no_show_reservations, 0) as no_show_reservations,
    coalesce(a.total_revenue, 0) as total_revenue,
    round(a.avg_nights_booked::numeric, 2) as avg_nights_booked
from {{ ref('stg_properties') }} p
left join property_agg a on p.property_id = a.property_id
