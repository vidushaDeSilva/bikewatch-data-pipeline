create schema staging;
create schema analytics;
set timezone='UTC';
create table staging.stg_pipeline_run(run_id uuid primary key);
insert into staging.stg_pipeline_run values ('00000000-0000-0000-0000-000000000001');

create table staging.stg_station_metadata(
    station_id text, collection_bucket timestamptz, collected_at timestamptz,
    run_id uuid default '00000000-0000-0000-0000-000000000001',
    station_name text, latitude double precision default 40.7,
    longitude double precision default -74, capacity bigint,
    has_invalid_structure boolean default false, has_time_anomaly boolean default false
);
insert into staging.stg_station_metadata(station_id,collection_bucket,collected_at,station_name,capacity)
values
 ('A','2026-09-12 00:00Z','2026-09-12 09:00Z','A before',30),
 ('A','2026-09-13 00:00Z','2026-09-13 10:20Z','A after',35),
 ('A','2026-09-14 00:00Z','2026-09-14 01:00Z','A future',99);

create table staging.stg_station_observation(
    station_id text, collection_bucket timestamptz, collected_at timestamptz,
    feed_last_updated timestamptz, source_url text default 'fixture://station_status',
    source_version text default '1.1',
    run_id uuid default '00000000-0000-0000-0000-000000000001',
    bikes_available bigint, docks_available bigint,
    is_installed boolean default true, is_renting boolean default true,
    is_returning boolean default true, station_reported_at timestamptz,
    station_age_seconds numeric, feed_age_seconds numeric,
    has_invalid_structure boolean default false, has_time_anomaly boolean default false
);
insert into staging.stg_station_observation(station_id,collection_bucket,collected_at,
    bikes_available,docks_available)
values
 ('A','2026-09-13 10:00Z','2026-09-13 10:01Z',0,10),
 ('A','2026-09-13 10:15Z','2026-09-13 10:16Z',0,10),
 ('A','2026-09-13 10:30Z','2026-09-13 10:31Z',4,6),
 ('A','2026-09-13 10:45Z','2026-09-13 10:46Z',8,2),
 ('A','2026-09-13 11:00Z','2026-09-13 11:01Z',20,0),
 ('A','2026-09-13 11:15Z','2026-09-13 11:16Z',-1,9),
 ('NO_META','2026-09-13 10:00Z','2026-09-13 10:01Z',2,8),
 ('CLOSED','2026-09-13 10:00Z','2026-09-13 10:01Z',0,0),
 ('STALE','2026-09-13 10:00Z','2026-09-13 10:01Z',3,7),
 ('UNKNOWN','2026-09-13 10:00Z','2026-09-13 10:01Z',3,7),
 ('FEED_STALE','2026-09-13 10:00Z','2026-09-13 10:01Z',3,7),
 ('FUTURE_CLOCK','2026-09-13 10:00Z','2026-09-13 10:01Z',3,7),
 ('LOCAL_DATE','2026-09-14 03:45Z','2026-09-14 03:46Z',2,8),
 ('LOCAL_DATE','2026-09-14 04:00Z','2026-09-14 04:01Z',6,4),
 ('PARTIAL','2026-11-02 12:00Z','2026-11-02 12:01Z',3,7),
 ('AFTER_CUTOFF','2026-11-02 12:15Z','2026-11-02 12:16Z',3,7);
update staging.stg_station_observation
set station_reported_at=collected_at - interval '1 minute',
    feed_last_updated=collected_at - interval '30 seconds';
update staging.stg_station_observation set has_invalid_structure=true where bikes_available<0;
update staging.stg_station_observation set is_renting=false, is_returning=false where station_id='CLOSED';
update staging.stg_station_observation set station_reported_at=collected_at-interval '1 hour' where station_id='STALE';
update staging.stg_station_observation set station_reported_at=null where station_id='UNKNOWN';
update staging.stg_station_observation set feed_last_updated=collected_at-interval '1 hour' where station_id='FEED_STALE';
update staging.stg_station_observation set has_time_anomaly=true, station_reported_at=collected_at+interval '10 minutes' where station_id='FUTURE_CLOCK';
update staging.stg_station_observation set
    station_age_seconds=extract(epoch from collected_at-station_reported_at),
    feed_age_seconds=extract(epoch from collected_at-feed_last_updated);
