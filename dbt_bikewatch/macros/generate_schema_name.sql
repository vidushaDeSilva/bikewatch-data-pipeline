{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- if custom_schema_name is none -%}

        {{ target.schema }}

    {%- elif target.name == 'neon'
            and target.schema == 'staging'
            and node.package_name == 'bikewatch'
            and custom_schema_name | trim == 'analytics' -%}

        analytics_work

    {%- else -%}

        {{ target.schema }}_{{ custom_schema_name | trim }}

    {%- endif -%}

{%- endmacro %}