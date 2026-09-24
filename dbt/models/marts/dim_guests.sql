-- Grain: one row per guest, enriched with booking activity.

with guests as (
    select * from {{ ref('stg_guests') }}
),

guest_activity as (
    select
        guest_id,
        count(*) as total_reservations,
        count(*) filter (where booking_status = 'cancelled') as cancelled_reservations,
        count(*) filter (where booking_status = 'no_show') as no_show_reservations,
        min(check_in_date) as first_check_in_date,
        max(check_in_date) as most_recent_check_in_date
    from {{ ref('stg_reservations') }}
    group by guest_id
)

select
    g.guest_id,
    g.first_name,
    g.last_name,
    g.email,
    g.phone,
    g.country,
    coalesce(a.total_reservations, 0) as total_reservations,
    coalesce(a.cancelled_reservations, 0) as cancelled_reservations,
    coalesce(a.no_show_reservations, 0) as no_show_reservations,
    a.first_check_in_date,
    a.most_recent_check_in_date
from guests g
left join guest_activity a on g.guest_id = a.guest_id
