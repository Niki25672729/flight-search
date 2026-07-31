-- mart_price_by_days_to_departure_daily's stated grain is (origin, destination, airline,
-- departure_weekday, days_bucket) — same single-column-test limitation as the other
-- composite-grain tests.
select
    origin_iata,
    destination_iata,
    airline,
    departure_weekday,
    days_bucket,
    count(*) as row_count
from {{ ref('mart_price_by_days_to_departure_daily') }}
group by origin_iata, destination_iata, airline, departure_weekday, days_bucket
having count(*) > 1
