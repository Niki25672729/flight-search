{% set discount_ratio = 0.60 %}
{% set near_low_ratio = 1.10 %}

-- depends_on: {{ ref('stg_flight_price_history') }}
{{
    config(
        materialized='incremental',
        incremental_strategy='insert_overwrite',
        post_hook=[
            "delete from {{ this }} where departure_date < current_date()",
        ],
    )
}}

-- "Deals worth acting on" — ACTIVE discounts, republished whole every build
-- (incremental_strategy='insert_overwrite'): a flight_key absent from the result is dropped
-- automatically, no delete-on-rebound DML needed.
--
-- Steps:
-- 1. Merge today's stg_flight_price_history events with yesterday's mart_discounts (first build
--    has no prior state, so it reads stg_flights_latest_state directly instead).
-- 2. Join int_flight_price_baseline for the pooled (route, weekday, month, year) avg/stddev.
-- 3. Check whether it passes BOTH:
--    price_eur <= discount_ratio * avg
--    price_eur <= avg - 1 stddev
-- 4. Join mart_route_historical_low + seed_airports to enrich the surviving rows.
--
-- discount_rank orders freshest-discount country pair first, that pair's freshest route first
-- within it, route's rows contiguous (country_pair_latest_dropped_on desc, route_latest_dropped_on
-- desc, dropped_on desc) — computed with window functions in the select itself, not a post-hook:
-- insert_overwrite already means this select's result IS the whole table, so it can see every row
-- it needs for the ranking without reading anything back afterward. Same reasoning applies to
-- days_at_low (date_diff against current_date() needs no post-hook either). The two grouping keys
-- behind the rank are intermediate-only, dropped before the final output.

with {% if is_incremental() %} todays_events {% else %} flights {% endif %} as (
    select
        flight_key,
        origin_iata,
        destination_iata,
        airline,
        flight_number,
        date(departure_time) as departure_date,
        price_eur,
{% if is_incremental() %}
        scrape_date
    from {{ ref('stg_flight_price_history') }}
{% else %}
        date(scraped_at) as dropped_on
    from {{ ref('stg_flights_latest_state') }}
    where seats_left is null or seats_left > 0
{% endif %}
),

{% if is_incremental() %}
flights as (
    select
        coalesce(e.flight_key, existing.flight_key) as flight_key,
        coalesce(e.origin_iata, existing.origin_iata) as origin_iata,
        coalesce(e.destination_iata, existing.destination_iata) as destination_iata,
        coalesce(e.airline, existing.airline) as airline,
        coalesce(e.flight_number, existing.flight_number) as flight_number,
        coalesce(e.departure_date, existing.departure_date) as departure_date,
        coalesce(e.price_eur, existing.price_eur) as price_eur,
        coalesce(existing.dropped_on, e.scrape_date) as dropped_on
    from todays_events e
    full outer join {{ this }} existing on existing.flight_key = e.flight_key
),
{% endif %}

candidate_events as (
    select
        f.flight_key,
        f.origin_iata,
        f.destination_iata,
        f.airline,
        f.flight_number,
        f.departure_date,
        f.price_eur,
        f.dropped_on,
        {{ pooled_avg_price('b.sum_price_eur', 'b.sample_count') }} as avg_price_eur,
        {{ pooled_stddev_price('b.sum_price_eur', 'b.sum_sq_price_eur', 'b.sample_count') }} as stddev_price_eur
    from flights f
    join {{ ref('int_flight_price_baseline') }} b
        on b.origin_iata = f.origin_iata
        and b.destination_iata = f.destination_iata
        and b.airline = f.airline
        and b.departure_weekday = format_date('%A', f.departure_date)
        and b.departure_month = extract(month from f.departure_date)
        and b.departure_year = extract(year from f.departure_date)
    where b.sample_count >= 3
),

qualifying_events as (
    select *
    from candidate_events
    where price_eur <= {{ discount_ratio }} * avg_price_eur
      and price_eur <= avg_price_eur - stddev_price_eur
)

enriched as (
    select
        n.origin_iata,
        o.city as origin_city,
        o.country as origin_country,
        {{ location_label('o.city', 'o.country', 'n.origin_iata') }} as origin_location,
        n.destination_iata,
        d.city as destination_city,
        d.country as destination_country,
        {{ location_label('d.city', 'd.country', 'n.destination_iata') }} as destination_location,
        n.airline,
        n.flight_number,
        n.departure_date,
        n.price_eur,
        round(n.avg_price_eur, 2) as baseline_price_eur,
        round((n.price_eur - n.avg_price_eur) / n.avg_price_eur, 2) as price_drop_ratio,
        n.dropped_on,
        date_diff(current_date(), n.dropped_on, day) as days_at_low,
        coalesce(n.price_eur <= {{ near_low_ratio }} * l.historical_low_price_eur, false) as is_near_historical_low,
        max(n.dropped_on) over (partition by n.origin_iata, n.destination_iata) as route_latest_dropped_on,
        max(n.dropped_on) over (partition by o.country, d.country) as country_pair_latest_dropped_on
    from qualifying_events n
    join {{ ref('mart_route_historical_low') }} l
        on l.origin_iata = n.origin_iata
        and l.destination_iata = n.destination_iata
    join {{ ref('seed_airports') }} o on o.airport_iata = n.origin_iata
    join {{ ref('seed_airports') }} d on d.airport_iata = n.destination_iata
)

select
    * except(route_latest_dropped_on, country_pair_latest_dropped_on),
    rank() over (
        order by country_pair_latest_dropped_on desc, route_latest_dropped_on desc, dropped_on desc
    ) as discount_rank
from enriched
