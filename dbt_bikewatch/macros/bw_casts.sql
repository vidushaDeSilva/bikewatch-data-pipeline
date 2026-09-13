{# 
  Extracts a non-empty text value from a JSONB document.

  Returns:
  - the text value if the JSON field exists, is a string, and is not blank
  - NULL otherwise
#}
{% macro bw_json_text(document, key) -%}

case

    when jsonb_typeof(({{ document }}) -> '{{ key }}') = 'string'

         and btrim(({{ document }}) ->> '{{ key }}') <> ''

    then ({{ document }}) ->> '{{ key }}'

end

{%- endmacro %}


{#
  Extracts a numeric value from a JSONB document and safely converts it
  to the requested PostgreSQL data type.

  Default output type:
  - bigint

  Returns:
  - the converted number if the JSON field is numeric and valid for the
    requested PostgreSQL type
  - NULL if the field is missing, not numeric, or cannot be safely cast

  Example:
  bw_json_number('payload', 'capacity')
  bw_json_number('payload', 'lat', 'double precision')
#}
{% macro bw_json_number(document, key, data_type='bigint') -%}

cast(

    case

        when jsonb_typeof(({{ document }}) -> '{{ key }}') = 'number'

             and pg_input_is_valid(

                 ({{ document }}) ->> '{{ key }}',

                 '{{ data_type }}'

             )

        then ({{ document }}) ->> '{{ key }}'

        else null

    end

    as {{ data_type }}

)

{%- endmacro %}


{#
  Converts a Unix epoch value into a PostgreSQL timestamp.

  Only accepts epoch values between:
  1 and 253402300799

  Returns:
  - a timestamp for a valid epoch value
  - NULL for 0, NULL, negative values, or values outside the accepted range

  In BikeWatch, epoch 0 is treated as an unknown timestamp.
#}
{% macro bw_epoch_timestamp(expression) -%}

to_timestamp(

    cast(

        case

            when ({{ expression }}) between 1 and 253402300799

            then ({{ expression }})

            else null

        end

        as double precision

    )

)

{%- endmacro %}


{#
  Returns the common provenance/lineage columns used by BikeWatch
  staging models.

  These fields allow a transformed row to be traced back to:
  - the station
  - collection time bucket
  - collection timestamp
  - upstream feed timestamp
  - source endpoint
  - GBFS version
  - collector run
#}
{% macro bw_provenance() -%}

station_id,

collection_bucket,

collected_at,

feed_last_updated,

source_url,

source_version,

run_id

{%- endmacro %}