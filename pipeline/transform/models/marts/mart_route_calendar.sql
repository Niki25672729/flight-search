{{ config(materialized='view') }}

-- "Which date should I fly?" / "Where should I go?" — 1 row / (origin, destination,
-- departure_date), cheapest bookable price ACROSS airlines. A view: source is already cheap
-- (newest partition only). airline is NOT part of the grain — it's an attribute of whichever
-- flight won, so a route/date with multiple carriers shows one row (the cheapest), not one per
-- airline.
with base as (
    select
        origin_iata,
        destination_iata,
        airline,
        flight_number,
        date(departure_time) as departure_date,
        price_eur as min_price_eur
    from {{ ref('stg_flights_latest_state') }}
    qualify row_number() over (
        partition by origin_iata, destination_iata, date(departure_time)
        order by price_eur asc
    ) = 1
)

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
    b.flight_number,
    b.departure_date,
    b.min_price_eur
from base b
join {{ ref('seed_airports') }} o on o.airport_iata = b.origin_iata
join {{ ref('seed_airports') }} d on d.airport_iata = b.destination_iata
