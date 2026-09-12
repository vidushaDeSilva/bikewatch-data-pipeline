with failures as (
    select
        'metadata_structure' as check_name,
        station_id as record_id
    from {{ ref('stg_station_metadata') }}
    where has_invalid_structure

    union all

    select 'observation_structure', station_id
    from {{ ref('stg_station_observation') }}
    where has_invalid_structure

    union all

    select 'run_structure_or_counts', run_id::text
    from {{ ref('stg_pipeline_run') }}
    where has_invalid_structure or has_count_mismatch

    {% for model in [
        'stg_station_metadata',
        'stg_station_observation'
    ] %}

    union all

    select '{{ model }}_key', station_id
    from {{ ref(model) }}
    group by station_id, collection_bucket
    having count(*) > 1
        or station_id is null
        or btrim(station_id) = ''
        or collection_bucket is null

    {% endfor %}

    {% for model, src, table in [
        ('stg_station_metadata', 'bikewatch_raw', 'station_metadata'),
        ('stg_station_observation', 'bikewatch_raw', 'station_observation'),
        ('stg_pipeline_run', 'bikewatch_ops', 'pipeline_run')
    ] %}

    union all

    select '{{ model }}_row_count', null::text
    where
        (select count(*) from {{ ref(model) }})
        <>
        (select count(*) from {{ source(src, table) }})

    {% endfor %}
)

select * from failures