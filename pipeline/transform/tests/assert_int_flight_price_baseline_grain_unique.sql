-- int_flight_price_baseline's stated grain is (origin, destination, airline, departure_weekday,
-- departure_month, departure_year) — same single-column-test limitation as the other
-- composite-grain tests.
select
    origin_iata,
    destination_iata,
    airline,
    departure_weekday,
    departure_month,
    departure_year,
    count(*) as row_count
from {{ ref('int_flight_price_baseline') }}
group by origin_iata, destination_iata, airline, departure_weekday, departure_month, departure_year
having count(*) > 1
