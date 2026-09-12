with extracted as (
    select
        {{ bw_provenance() }},

        {{ bw_json_text('payload', 'station_id') }}
            as payload_station_id,

        {{ bw_json_number('payload', 'num_bikes_available') }}
            as bikes_available,

        {{ bw_json_number('payload', 'num_docks_available') }}
            as docks_available,

        {{ bw_json_number('payload', 'num_bikes_disabled') }}
            as bikes_disabled,

        {{ bw_json_number('payload', 'num_docks_disabled') }}
            as docks_disabled,

        {{ bw_json_number('payload', 'is_installed') }}
            as installed_code,

        {{ bw_json_number('payload', 'is_renting') }}
            as renting_code,

        {{ bw_json_number('payload', 'is_returning') }}
            as returning_code,

        {{ bw_json_number('payload', 'last_reported') }}
            as last_reported_epoch,

        coalesce(
            payload -> 'num_bikes_disabled' <> 'null'::jsonb,
            false
        ) as bikes_disabled_provided,

        coalesce(
            payload -> 'num_docks_disabled' <> 'null'::jsonb,
            false
        ) as docks_disabled_provided

    from {{ source('bikewatch_raw', 'station_observation') }}
),

typed as (
    select
        *,

        {{ bw_epoch_timestamp('last_reported_epoch') }}
            as station_reported_at,

        case
            when installed_code in (0, 1)
            then installed_code = 1
        end as is_installed,

        case
            when renting_code in (0, 1)
            then renting_code = 1
        end as is_renting,

        case
            when returning_code in (0, 1)
            then returning_code = 1
        end as is_returning

    from extracted
)

select
    *,

    extract(epoch from collected_at - station_reported_at)
        as station_age_seconds,

    extract(epoch from collected_at - feed_last_updated)
        as feed_age_seconds,

    (
        payload_station_id is distinct from station_id
        or bikes_available is null
        or bikes_available < 0
        or docks_available is null
        or docks_available < 0

        or (
            bikes_disabled_provided
            and (bikes_disabled is null or bikes_disabled < 0)
        )

        or (
            docks_disabled_provided
            and (docks_disabled is null or docks_disabled < 0)
        )

        or is_installed is null
        or is_renting is null
        or is_returning is null

        or last_reported_epoch is null
        or last_reported_epoch not between 0 and 253402300799

        or source_version <> '1.1'
    ) as has_invalid_structure,

    coalesce(
        last_reported_epoch = 0,
        false
    ) as is_station_time_unknown,

    (
        feed_last_updated > collected_at
            + {{ var('allowed_clock_skew_seconds') }}
                * interval '1 second'

        or coalesce(
            station_reported_at > collected_at
                + {{ var('allowed_clock_skew_seconds') }}
                    * interval '1 second',
            false
        )

        or collected_at < collection_bucket
    ) as has_time_anomaly,

    coalesce(
        collected_at - station_reported_at
            > {{ var('station_stale_after_seconds') }}
                * interval '1 second',
        false
    ) as was_station_stale_at_collection

from typed