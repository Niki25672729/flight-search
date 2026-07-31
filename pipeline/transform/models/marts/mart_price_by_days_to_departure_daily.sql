{{ config(materialized='view') }}

-- "How far ahead should I book?" — avg_price_eur is a cheap sum/count division over
-- int_flight_price_by_days_to_departure's incrementally maintained totals, not a raw rescan.
-- Bucketing/dropped-column rationale lives on that table's own header comment.
select
    b.origin_iata,
    o.city as origin_city,
    o.country as origin_country,
    {{ location_label('o.city', 'o.country', 'b.origin_iata') }} as origin_location,
    b.destination_iata,
    d.city as destination_city,
    d.country as destination_country,
    {{ location_label('d.city', 'd.country', 'b.destination_iata') }} as destination_location,
    b.airline,
    b.departure_weekday,
    b.days_bucket,
    b.bucket_start,
    round({{ pooled_avg_price('b.sum_price_eur', 'b.sample_count') }}, 2) as avg_price_eur,
    b.sample_count
from {{ ref('int_flight_price_by_days_to_departure') }} b
join {{ ref('seed_airports') }} o on o.airport_iata = b.origin_iata
join {{ ref('seed_airports') }} d on d.airport_iata = b.destination_iata
