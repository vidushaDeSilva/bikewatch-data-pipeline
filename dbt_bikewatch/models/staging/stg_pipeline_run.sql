{% set feeds = [
    'station_information',
    'station_status'
] %}

{% set counters = [
    'received',
    'ignored',
    'valid',
    'accepted',
    'duplicate',
    'rejected',
    'missing'
] %}


{#
  Extract and normalize pipeline-run information.

  The pipeline_run table contains operational information about each
  collector execution. Some metrics are stored as nested JSONB, so this
  CTE converts them into normal SQL columns for easier validation and
  downstream analysis.
#}
with extracted as (

    select
        run_id,
        status as run_status,
        stage,
        started_at,
        finished_at,
        collection_bucket,
        tracked_station_ids,
        metrics,
        error_type,

        {#
          Count the number of stations the collector expected to process.

          Only calculate the count when tracked_station_ids is actually
          a JSON array. Invalid/missing structures remain NULL so they
          can be detected later by has_invalid_structure.
        #}
        case
            when jsonb_typeof(tracked_station_ids) = 'array'
                then jsonb_array_length(tracked_station_ids)
        end as tracked_station_count

        {% for feed in feeds %}

        {#
          Record whether a metrics object exists for the current feed.

          This allows us to distinguish:
          - metrics absent
          - metrics present with counters equal to zero
          - metrics present but malformed
        #}
        , coalesce(
            jsonb_typeof(metrics -> '{{ feed }}') = 'object',
            false
        ) as has_{{ feed }}_metrics

        {#
          Extract every expected counter from the current feed's
          metrics object.

          bw_json_number() safely returns a bigint when the JSON
          value is numeric and valid; otherwise it returns NULL.

          This generates columns such as:
          station_information_received
          station_information_accepted
          station_status_received
          station_status_missing
        #}
        {% for counter in counters %}

        , {{ bw_json_number(
            "metrics -> '" ~ feed ~ "'",
            counter
        ) }} as {{ feed }}_{{ counter }}

        {% endfor %}

        {% endfor %}

    from {{ source('bikewatch_ops', 'pipeline_run') }}
)


select
    *,

    {#
      Calculate how long the collector execution took.

      PostgreSQL timestamp subtraction produces an interval;
      EXTRACT(EPOCH ...) converts that interval to seconds.

      Unfinished runs naturally produce NULL because finished_at is NULL.
    #}
    extract(
        epoch from finished_at - started_at
    ) as duration_seconds,


    {#
      Flag malformed or structurally invalid pipeline-run records.

      A record is considered structurally invalid when:
      - metrics is not a JSON object
      - tracked_station_ids was not a valid array
      - tracked station count is outside the expected 1-20 range
      - run status is missing or unsupported
      - a feed key exists but its value is not a JSON object
      - a present feed contains missing, invalid, or negative counters
    #}
    (
        jsonb_typeof(metrics) is distinct from 'object'

        or tracked_station_count is null

        or tracked_station_count not between 1 and 20

        or run_status is null

        or run_status not in (
            'running',
            'succeeded',
            'partial',
            'failed'
        )

        {% for feed in feeds %}

        {#
          PostgreSQL's ? operator checks whether the JSON object
          contains the specified key.

          If the feed key exists but its value is not a JSON object,
          the metrics structure is malformed.
        #}
        or (
            metrics ? '{{ feed }}'
            and not has_{{ feed }}_metrics
        )

        {#
          If metrics for this feed exist, every expected counter must:
          - be successfully parsed
          - be non-NULL
          - be non-negative
        #}
        or (
            has_{{ feed }}_metrics
            and (
                false

                {% for counter in counters %}

                or {{ feed }}_{{ counter }} is null
                or {{ feed }}_{{ counter }} < 0

                {% endfor %}
            )
        )

        {% endfor %}

    ) as has_invalid_structure,


    {#
      Validate the accounting relationships between ingestion counters.

      For each feed we expect:

          valid = accepted + duplicate

      because every valid tracked record should either have been newly
      inserted or skipped because its storage key already existed.

      We also expect:

          received = ignored + rejected + valid

      because every received source entry should end up in exactly one
      of those categories.

      IS DISTINCT FROM is used for NULL-safe comparison.
    #}
    (
        false

        {% for feed in feeds %}

        or (
            has_{{ feed }}_metrics
            and (
                {{ feed }}_valid::numeric
                    is distinct from
                    {{ feed }}_accepted::numeric
                    + {{ feed }}_duplicate::numeric

                or

                {{ feed }}_received::numeric
                    is distinct from
                    {{ feed }}_ignored::numeric
                    + {{ feed }}_rejected::numeric
                    + {{ feed }}_valid::numeric
            )
        )

        {% endfor %}

    ) as has_count_mismatch,


    {#
      Flag source-data gaps.

      A run has source gaps when either feed reports:
      - one or more rejected records, or
      - one or more expected stations missing from the response.

      COALESCE converts an unknown/NULL comparison to FALSE.
    #}
    (
        false

        {% for feed in feeds %}

        or coalesce(
            {{ feed }}_rejected > 0
            or {{ feed }}_missing > 0,
            false
        )

        {% endfor %}

    ) as has_source_gaps,


    {#
      Convenience Boolean indicating whether the collector
      explicitly marked this execution as failed.
    #}
    coalesce(
        run_status = 'failed',
        false
    ) as is_failed_run,


    {#
      Flag impossible execution timing.

      A completed run should never finish before it started.
      Unfinished runs have finished_at = NULL and are not flagged here.
    #}
    coalesce(
        finished_at < started_at,
        false
    ) as has_time_anomaly

from extracted