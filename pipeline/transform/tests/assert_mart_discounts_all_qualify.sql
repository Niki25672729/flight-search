-- Every surviving row must still actually pass the discount criteria against today's baseline —
-- if this fires, either mart_discounts' own filter has a bug, or its rebound-delete post-hook
-- isn't correctly removing one.
select
    m.flight_key,
    m.price_eur,
    m.baseline_price_eur
from {{ ref('mart_discounts') }} m
join {{ ref('int_flight_price_baseline') }} b
    on b.origin_iata = m.origin_iata
    and b.destination_iata = m.destination_iata
    and b.airline = m.airline
    and b.departure_weekday = format_date('%A', m.departure_date)
    and b.departure_month = extract(month from m.departure_date)
    and b.departure_year = extract(year from m.departure_date)
where not (
    m.price_eur <= {{ var('discount_ratio') }} * ({{ pooled_avg_price('b.sum_price_eur', 'b.sample_count') }})
    and m.price_eur <= ({{ pooled_avg_price('b.sum_price_eur', 'b.sample_count') }})
        - {{ pooled_stddev_price('b.sum_price_eur', 'b.sum_sq_price_eur', 'b.sample_count') }}
)
