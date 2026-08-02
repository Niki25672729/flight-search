-- depends_on: {{ ref('stg_flights_latest_state') }}
{{
    config(
        materialized='incremental',
        unique_key=['origin_iata', 'destination_iata', 'airline', 'departure_weekday', 'departure_month', 'departure_year'],
        incremental_strategy='merge',
        post_hook="delete from {{ this }} where departure_year < extract(year from current_date()) - 2",
    )
}}

-- Running sum/sum_sq/count of observed prices per (route, weekday, departure month+year), merged incrementally instead of rescanning full history.
-- last_updated_run_date guards against double-counting on an Airflow retry of the same run_date — not backfill/out-of-order-safe.
with todays_observations as (
    select
        origin_iata,
        destination_iata,
        airline,
        date(departure_time) as departure_date,
        price_eur
    from
    {% if is_incremental() %}
        {{ ref('stg_flights_latest_state') }}
    {% else %}
        {{ source('silver', 'flights_latest_state_external') }}
    where scrape_date >= '2000-01-01'  -- require_partition_filter=true needs a filter here even though this bootstrap wants all history — see bigquery.tf
    {% endif %}
),

todays_contribution as (
    select
        origin_iata,
        destination_iata,
        airline,
        format_date('%A', departure_date) as departure_weekday,
        extract(month from departure_date) as departure_month,
        extract(year from departure_date) as departure_year,
        sum(price_eur) as sum_price_eur,
        sum(price_eur * price_eur) as sum_sq_price_eur,
        count(*) as sample_count,
        '{{ var("run_date") }}' as run_date
    from todays_observations
    group by origin_iata, destination_iata, airline, departure_weekday, departure_month, departure_year
)

{% if is_incremental() %}
select
    c.origin_iata,
    c.destination_iata,
    c.airline,
    c.departure_weekday,
    c.departure_month,
    c.departure_year,
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
    and t.departure_month = c.departure_month
    and t.departure_year = c.departure_year
where t.last_updated_run_date is null or t.last_updated_run_date < c.run_date
{% else %}
select
    origin_iata,
    destination_iata,
    airline,
    departure_weekday,
    departure_month,
    departure_year,
    sum_price_eur,
    sum_sq_price_eur,
    sample_count,
    run_date as last_updated_run_date
from todays_contribution
{% endif %}
