{{ config(materialized='table', schema='analytics', tags=['reporting'],
          indexes=[{'columns': ['station_id', 'collection_bucket'], 'unique': true}]) }}
-- Valid structure is retained even when stale/closed; eligibility controls metrics.
select *
from {{ ref('int_bw_observation_enriched') }}
where is_structurally_valid
