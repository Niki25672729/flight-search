-- Cross-fact business invariant: a route's all-time low (mart_route_historical_low, sourced from
-- the full price-change log) can never exceed today's cheapest bookable price for that route
-- (stg_flights_latest_state) — today's price is itself a value that must already have been
-- recorded in the price-change log the first time it appeared. A violation means the two facts
-- have drifted out of sync with each other, not a business reality.
select
    l.origin_iata,
    l.destination_iata,
    l.historical_low_price_eur,
    c.cheapest_current_price_eur
from {{ ref('mart_route_historical_low') }} l
join (
    select origin_iata, destination_iata, min(price_eur) as cheapest_current_price_eur
    from {{ ref('stg_flights_latest_state') }}
    where seats_left is null or seats_left > 0
    group by origin_iata, destination_iata
) c
    on l.origin_iata = c.origin_iata
    and l.destination_iata = c.destination_iata
where l.historical_low_price_eur > c.cheapest_current_price_eur
