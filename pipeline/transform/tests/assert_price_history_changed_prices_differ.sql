-- Encodes the actual contract of flight_price_history (ARCHITECTURE_DASHBOARD.md: "only when new or
-- price-changed"): a row with is_new_flight = false must have a price that's actually different
-- from prior_price_eur. If this ever fires, the silver diff logic (pipeline/processing/silver.py)
-- has started writing no-op "changes", not a gold-layer bug — but gold is exactly where it'd
-- otherwise go unnoticed, since nothing downstream re-derives prior_price_eur to cross-check it.
select *
from {{ ref('stg_flight_price_history') }}
where is_new_flight = false
  and price_eur = prior_price_eur
