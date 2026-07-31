-- Filtered to the run_date partition only, or every accumulated day gets unioned together and
-- flight_key stops being unique, silently breaking every fact/dim built on top. run_date (not
-- max(scrape_date)) so a run whose silver write hasn't landed yet returns zero rows and fails
-- loud downstream, instead of silently reading yesterday's partition as if it were today's.
-- Matches pipeline/processing/run_silver.py's --run-date CLI flag (src/config.py's DATE_FORMAT,
-- %Y%m%d) so both sides of the pipeline are driven by the same value.
select
    flight_key,
    origin_iata,
    destination_iata,
    destination_city,
    destination_country,
    airline,
    flight_number,
    departure_time,
    arrival_time,
    price_eur,
    currency,
    seats_left,
    scraped_at
from {{ source('silver', 'flights_latest_state_external') }}
where scrape_date = parse_date('%Y%m%d', '{{ var("run_date") }}')
