{{ config(materialized='table', tags=['reporting']) }}
{% set periods = var('coverage_tracking_periods', []) %}
{% if periods %}
select * from (
    values
    {% for p in periods %}
        (
            {{ bw_reporting_literal(p['station_id']) }}::text,
            {{ bw_reporting_literal(p['start_bucket']) }}::timestamptz,
            {% if p.get('end_bucket') %}
                {{ bw_reporting_literal(p['end_bucket']) }}::timestamptz
            {% else %} null::timestamptz {% endif %}
        ){% if not loop.last %},{% endif %}
    {% endfor %}
) as periods(station_id, start_bucket, end_bucket)
{% else %}
select null::text as station_id, null::timestamptz as start_bucket,
       null::timestamptz as end_bucket
where false
{% endif %}
