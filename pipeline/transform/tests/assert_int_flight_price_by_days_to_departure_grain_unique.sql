-- int_flight_price_by_days_to_departure's stated grain is (origin, destination, airline,
-- departure_weekday, days_bucket) — same single-column-test limitation as the other
-- composite-grain tests.
select
    origin_iata,
    destination_iata,
    airline,
    departure_weekday,
    days_bucket,
    count(*) as row_count
from {{ ref('int_flight_price_by_days_to_departure') }}
group by origin_iata, destination_iata, airline, departure_weekday, days_bucket
having count(*) > 1
