{{ config(materialized='table', schema='analytics', tags=['reporting'],
          indexes=[{'columns': ['station_id', 'hour_start_utc'], 'unique': true}]) }}
{{ bw_reporting_aggregate('hour') }}
