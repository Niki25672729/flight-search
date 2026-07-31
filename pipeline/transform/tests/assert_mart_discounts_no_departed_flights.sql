-- Verifies post-hook 1 actually removed departed flights — a surviving row here means the
-- post-hook silently stopped running or its condition drifted from the model's own semantics.
select *
from {{ ref('mart_discounts') }}
where departure_date < current_date()
