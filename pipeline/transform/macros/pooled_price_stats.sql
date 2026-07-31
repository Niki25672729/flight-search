{% macro pooled_avg_price(sum_price_col, sample_count_col) %}
{{ sum_price_col }} / {{ sample_count_col }}
{% endmacro %}


{% macro pooled_stddev_price(sum_price_col, sum_sq_price_col, sample_count_col) %}
sqrt(greatest({{ sum_sq_price_col }} / {{ sample_count_col }} - power({{ pooled_avg_price(sum_price_col, sample_count_col) }}, 2), 0))
{% endmacro %}
