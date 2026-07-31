{% macro location_label(city_col, country_col, iata_col) %}
concat({{ city_col }}, ', ', {{ country_col }}, ' (', {{ iata_col }}, ')')
{% endmacro %}
