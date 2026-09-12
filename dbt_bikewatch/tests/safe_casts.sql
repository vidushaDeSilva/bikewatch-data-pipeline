with samples(payload, expected_integer) as (
    values
        ('{"v": 12}'::jsonb, 12::bigint),
        ('{"v": -1}'::jsonb, -1::bigint),
        ('{"v": "12"}'::jsonb, null::bigint),
        ('{"v": 1.5}'::jsonb, null::bigint),
        ('{"v": 9223372036854775808}'::jsonb, null::bigint),
        ('{"v": true}'::jsonb, null::bigint),
        ('{"v": null}'::jsonb, null::bigint),
        ('{}'::jsonb, null::bigint)
),

checked as (
    select
        *,
        {{ bw_json_number('payload', 'v') }} as actual_integer
    from samples
)

select 'integer_conversion' as failed_check
from checked
where actual_integer is distinct from expected_integer

union all

select 'floating_point_overflow'
where {{
    bw_json_number(
        "'{\"v\": 1e400}'::jsonb",
        'v',
        'double precision'
    )
}} is not null

union all

select 'unknown_epoch'
where {{ bw_epoch_timestamp('0::bigint') }} is not null

union all

select 'epoch_range'
where {{
    bw_epoch_timestamp('253402300800::bigint')
}} is not null