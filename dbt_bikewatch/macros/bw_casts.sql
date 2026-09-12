-- extract a non-empty JSON string
{% macro bw_json_text(document, key) -%}
case
    when jsonb_typeof(({{ document }}) -> '{{ key }}') = 'string'
         and btrim(({{ document }}) ->> '{{ key }}') <> ''
    then ({{ document }}) ->> '{{ key }}'
end
{%- endmacro %}


-- extract and cast a JSON number
{% macro bw_json_number(document, key, data_type='bigint') -%}
case
    when jsonb_typeof(({{ document }}) -> '{{ key }}') = 'number'
         and pg_input_is_valid(
             ({{ document }}) ->> '{{ key }}',
             '{{ data_type }}'
         )
    then (({{ document }}) ->> '{{ key }}')::{{ data_type }}
end
{%- endmacro %}


-- convert epoch seconds to timestamp
{% macro bw_epoch_timestamp(expression) -%}
case
    when ({{ expression }}) between 1 and 253402300799
    then to_timestamp(({{ expression }})::double precision)
end
{%- endmacro %}


-- Reuse standard lineage/provenance columns
{% macro bw_provenance() -%}
station_id,
collection_bucket,
collected_at,
feed_last_updated,
source_url,
source_version,
run_id
{%- endmacro %}