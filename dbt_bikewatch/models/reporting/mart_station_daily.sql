{{ config(materialized='table', schema='analytics', tags=['reporting'],
          indexes=[{'columns': ['station_id', 'reporting_date'], 'unique': true}]) }}
-- Aggregate underlying samples directly, preserving weighted denominators.
{{ bw_reporting_aggregate('day') }}
