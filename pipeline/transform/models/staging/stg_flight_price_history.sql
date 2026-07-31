-- Narrowed to the run_date partition only — mart_discounts only ever wants today's new/changed
-- rows. Full history is read directly from the source instead (see mart_route_historical_low's
-- first-build branch), bypassing this model entirely — kept out of this staging model so it
-- doesn't have to serve two different consumption shapes at once. run_date (not max(scrape_date))
-- for the same fail-loud reason as stg_flights_latest_state.
select
    flight_key,
    origin_iata,
    destination_iata,
    airline,
    flight_number,
    departure_time,
    scrape_date,
    price_eur,
    prior_price_eur,
    is_new_flight
from {{ source('silver', 'flight_price_history_external') }}
where scrape_date = parse_date('%Y%m%d', '{{ var("run_date") }}')
