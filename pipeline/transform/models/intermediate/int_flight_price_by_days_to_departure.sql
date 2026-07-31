{{
    config(
        materialized='incremental',
        unique_key=['origin_iata', 'destination_iata', 'airline', 'departure_weekday', 'days_bucket'],
        incremental_strategy='merge',
    )
}}

-- Running sum/sum_sq/count of observed prices per (route, weekday, days-to-departure bucket), merged incrementally instead of rescanning full history.
-- last_updated_run_date guards against double-counting on an Airflow retry of the same run_date — not backfill/out-of-order-safe.
-- flight_key isn't selected — nothing downstream in this model needs it, only the derived
-- weekday/bucket dimensions and price. First build only reads the source directly, unfiltered,
-- for the one-time full-history bootstrap; every later build reads just today's run_date slice
-- via stg_flights_latest_state.
with todays_observations as (
    select
        origin_iata,
        destination_iata,
        airline,
        date(departure_time) as departure_date,
        price_eur,
        scrape_date,
        date_diff(date(departure_time), scrape_date, day) as days_to_departure
    from
    {% if is_incremental() %}
        {{ ref('stg_flights_latest_state') }}
    {% else %}
        {{ source('silver', 'flights_latest_state_external') }}
    where scrape_date >= '2000-01-01'  -- require_partition_filter=true needs a filter here even though this bootstrap wants all history — see bigquery.tf
    {% endif %}
),

bucketed as (
    select
        origin_iata,
        destination_iata,
        airline,
        format_date('%A', departure_date) as departure_weekday,
        case
            when days_to_departure <= 5 then 0
            else 6 + 5 * div(days_to_departure - 6, 5)
        end as bucket_start,
        price_eur
    from todays_observations
    where days_to_departure between 0 and 95
),

todays_contribution as (
    select
        origin_iata,
        destination_iata,
        airline,
        departure_weekday,
        case
            when bucket_start = 0 then '0~5'
            else format('%d~%d', bucket_start, bucket_start + 4)
        end as days_bucket,
        bucket_start,
        sum(price_eur) as sum_price_eur,
        sum(price_eur * price_eur) as sum_sq_price_eur,
        count(*) as sample_count,
        '{{ var("run_date") }}' as run_date
    from bucketed
    group by origin_iata, destination_iata, airline, departure_weekday, days_bucket, bucket_start
)

{% if is_incremental() %}
select
    c.origin_iata,
    c.destination_iata,
    c.airline,
    c.departure_weekday,
    c.days_bucket,
    c.bucket_start,
    coalesce(t.sum_price_eur, 0) + c.sum_price_eur as sum_price_eur,
    coalesce(t.sum_sq_price_eur, 0) + c.sum_sq_price_eur as sum_sq_price_eur,
    coalesce(t.sample_count, 0) + c.sample_count as sample_count,
    c.run_date as last_updated_run_date
from todays_contribution c
left join {{ this }} t
    on t.origin_iata = c.origin_iata
    and t.destination_iata = c.destination_iata
    and t.airline = c.airline
    and t.departure_weekday = c.departure_weekday
    and t.days_bucket = c.days_bucket
where t.last_updated_run_date is null or t.last_updated_run_date < c.run_date
{% else %}
select
    origin_iata,
    destination_iata,
    airline,
    departure_weekday,
    days_bucket,
    bucket_start,
    sum_price_eur,
    sum_sq_price_eur,
    sample_count,
    run_date as last_updated_run_date
from todays_contribution
{% endif %}
