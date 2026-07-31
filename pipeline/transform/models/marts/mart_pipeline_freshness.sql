-- One summary row for the dashboard's health strip
with latest as (
    select origin_iata, airline, departure_time
    from {{ ref('stg_flights_latest_state') }}
)

select
    parse_date('%Y%m%d', '{{ var("run_date") }}') as scrape_date,
    count(*) as flight_count,
    count(distinct origin_iata) as origins_seen,
    count(distinct airline) as airline_count,
    date_diff(max(date(departure_time)), parse_date('%Y%m%d', '{{ var("run_date") }}'), day) as window_days
from latest
