with extracted as (
    select
        {{ bw_provenance() }},

        {{ bw_json_text('payload', 'station_id') }}
            as payload_station_id,

        btrim({{ bw_json_text('payload', 'name') }})
            as station_name,

        {{ bw_json_number('payload', 'lat', 'double precision') }}
            as latitude,

        {{ bw_json_number('payload', 'lon', 'double precision') }}
            as longitude,

        {{ bw_json_number('payload', 'capacity') }}
            as capacity

    from {{ source('bikewatch_raw', 'station_metadata') }}
)

select
    *,

    (
        payload_station_id is distinct from station_id
        or station_name is null
        or latitude is null
        or latitude not between -90 and 90
        or longitude is null
        or longitude not between -180 and 180
        or capacity is null
        or capacity < 0
        or source_version <> '1.1'
    ) as has_invalid_structure,

    (
        feed_last_updated > collected_at
            + {{ var('allowed_clock_skew_seconds') }}
                * interval '1 second'
        or collected_at < collection_bucket
    ) as has_time_anomaly

from extracted