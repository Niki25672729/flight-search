{{ config(
    materialized='incremental',
    unique_key=['origin_iata', 'destination_iata'],
    full_refresh=false,
    on_schema_change='append_new_columns',
) }}

-- "All-time low reference" — (origin, destination) grain, ROLLING MIN merged each build against
-- this table's own prior rows. airline/flight_number are attributes of whichever flight set the
-- record, not part of the grain.
--
-- STATEFUL: full_refresh is disabled — a forced rebuild could only re-derive from surviving
-- history, silently truncating "all-time". Only the first build seeds from full history.
--
-- todays_candidate reads stg_flight_price_history, not a daily rescan — filtered to new flights
-- or price drops, since a price increase can never set a new record low.
--
-- Carries days_to_departure/departure_weekday/departure_weekday_order as booking-window advice.
-- Price ties prefer the later departure date — more lead time to plan the trip — then fall back
-- to earliest scrape_date.
with todays_candidate as (
    select
        origin_iata,
        destination_iata,
        airline,
        flight_number,
        price_eur as historical_low_price_eur,
        scrape_date,
        date(departure_time) as departure_date
    from {{ ref('stg_flight_price_history') }}
    where prior_price_eur is null or price_eur < prior_price_eur
    qualify row_number() over (
        partition by origin_iata, destination_iata
        order by price_eur asc, date(departure_time) desc
    ) = 1
),

{% if is_incremental() %}
prior_lows as (
    select origin_iata, destination_iata, airline, flight_number,
        historical_low_price_eur, scrape_date, departure_date
    from {{ this }}
),
{% else %}
-- First build only: seeds from the full price-change log directly — the only consumer that
-- ever needed full history, so no separate fact table is maintained for it.
prior_lows as (
    select
        origin_iata,
        destination_iata,
        airline,
        flight_number,
        price_eur as historical_low_price_eur,
        scrape_date,
        date(departure_time) as departure_date
    from {{ source('silver', 'flight_price_history_external') }}
    where scrape_date >= '2000-01-01'  -- require_partition_filter=true needs a filter here even though this bootstrap wants all history — see bigquery.tf
    qualify row_number() over (
        partition by origin_iata, destination_iata
        order by price_eur asc, date(departure_time) desc, scrape_date asc
    ) = 1
),
{% endif %}

winner as (
    select *
    from (
        select * from prior_lows
        union all
        select * from todays_candidate
    )
    qualify row_number() over (
        partition by origin_iata, destination_iata
        order by historical_low_price_eur asc, departure_date desc, scrape_date asc
    ) = 1
)

select
    w.origin_iata,
    o.city as origin_city,
    o.country as origin_country,
    {{ location_label('o.city', 'o.country', 'w.origin_iata') }} as origin_location,
    w.destination_iata,
    d.city as destination_city,
    d.country as destination_country,
    {{ location_label('d.city', 'd.country', 'w.destination_iata') }} as destination_location,
    w.airline,
    w.flight_number,
    w.historical_low_price_eur,
    w.scrape_date,
    w.departure_date,
    format_date('%A', w.departure_date) as departure_weekday,
    cast(format_date('%u', w.departure_date) as int64) as departure_weekday_order,
    date_diff(w.departure_date, w.scrape_date, day) as days_to_departure
from winner w
join {{ ref('seed_airports') }} o on o.airport_iata = w.origin_iata
join {{ ref('seed_airports') }} d on d.airport_iata = w.destination_iata
