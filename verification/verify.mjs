// Install @electric-sql/pglite in this directory; no connection to Neon.
import { PGlite } from '@electric-sql/pglite';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const root=path.dirname(fileURLToPath(import.meta.url));
const data=JSON.parse(fs.readFileSync(path.join(root,'generated/rendered.json'),'utf8'));
const db=new PGlite();
await db.exec(fs.readFileSync(path.join(root,'fixtures.sql'),'utf8'));
const relation=name=>(name.startsWith('fct_')||name.startsWith('mart_')?'analytics.':'staging.')+name;
for(const name of data.order){
  try{ await db.exec(`create table ${relation(name)} as ${data.models[name]}`); }
  catch(e){throw new Error(`${name}: ${e.message}`);}
}
let gateCount=0;
for(const [name,sql] of Object.entries(data.tests)){
  const {rows}=await db.query(sql);
  if(name==='reporting_quality_warnings'){
    assert(rows.length>0,'Expected warnings for deliberately missing/stale fixture data');
  }else{assert.equal(rows.length,0,`${name}: ${JSON.stringify(rows)}`);gateCount++;}
}
const one=async sql=>(await db.query(sql)).rows[0];
const checks=[];
async function check(name,fn){await fn();checks.push(name);}
await check('Metadata joins preserve historical capacity and do not multiply rows', async()=>{
  const {rows}=await db.query("select capacity from analytics.fct_station_observation where station_id='A' order by collection_bucket");
  assert.deepEqual(rows.map(r=>Number(r.capacity)),[30,30,35,35,35]);
});
await check('Hourly sample mean and empty fraction use rental-eligible samples',async()=>{
  const r=await one("select * from analytics.mart_station_hourly where station_id='A' order by hour_start_utc limit 1");
  assert.equal(Number(r.avg_bikes_available),3);assert.equal(Number(r.empty_sample_fraction),0.5);assert.equal(Number(r.collection_coverage),1);
});
await check('Daily means weight samples rather than hourly means',async()=>{
  const r=await one("select * from analytics.mart_station_daily where station_id='A'");
  assert.equal(Number(r.avg_bikes_available),6.4);assert.equal(Number(r.invalid_structure_count),1);
  assert.equal(Number(r.observation_count),6);assert.equal(Number(r.expected_observation_count),8);
  assert.equal(Number(r.collection_coverage),0.75);assert.equal(Number(r.usable_coverage),0.625);
});
await check('All-missing hours remain visible with NULL means and zero coverage',async()=>{
  const r=await one("select * from analytics.mart_station_hourly where station_id='NO_DATA'");
  assert.equal(Number(r.expected_observation_count),4);assert.equal(Number(r.observation_count),0);
  assert.equal(r.avg_bikes_available,null);assert.equal(Number(r.collection_coverage),0);
});
await check('Latest invalid observation falls back visibly to older trusted data',async()=>{
  const r=await one("select * from analytics.mart_station_current where station_id='A'");
  assert.equal(r.using_older_trusted_observation,true);assert.equal(Number(r.bikes_available),20);
  assert.equal(r.latest_quality_reason,'invalid_structure');assert(r.freshness_valid_until<r.reporting_as_of);
});
await check('Missing metadata preserves availability and NULL capacity',async()=>{
  const r=await one("select * from analytics.fct_station_observation where station_id='NO_META'");
  assert.equal(r.has_metadata,false);assert.equal(r.capacity,null);assert.equal(r.is_rental_eligible,true);
});
await check('Closed stations remain valid but are outside service denominators',async()=>{
  const r=await one("select * from analytics.mart_station_daily where station_id='CLOSED'");
  assert.equal(Number(r.availability_sample_count),1);assert.equal(Number(r.rental_sample_count),0);
  assert.equal(r.avg_bikes_available,null);assert.equal(r.empty_sample_fraction,null);
});
await check('Unknown, stale station/feed, and future clock records are ineligible',async()=>{
  const {rows}=await db.query("select is_availability_eligible from analytics.fct_station_observation where station_id in ('STALE','UNKNOWN','FEED_STALE','FUTURE_CLOCK')");
  assert.equal(rows.length,4);assert(rows.every(r=>r.is_availability_eligible===false));
  const r=await one("select * from analytics.mart_station_current where station_id='UNKNOWN'");
  assert.equal(r.has_trusted_observation,false);assert.equal(r.bikes_available,null);
});
await check('New York reporting date crosses midnight at the correct UTC instant',async()=>{
  const {rows}=await db.query("select reporting_date::text, avg_bikes_available from analytics.mart_station_daily where station_id='LOCAL_DATE' order by reporting_date");
  assert.deepEqual(rows.map(r=>r.reporting_date),['2026-09-13','2026-09-14']);
  assert.deepEqual(rows.map(r=>Number(r.avg_bikes_available)),[2,6]);
});
await check('DST days contain 92 or 100 due quarter-hour buckets',async()=>{
  const spring=await one("select expected_observation_count from analytics.mart_station_daily where station_id='DST_SPRING'");
  const fall=await one("select expected_observation_count from analytics.mart_station_daily where station_id='DST_FALL'");
  assert.equal(Number(spring.expected_observation_count),92);assert.equal(Number(fall.expected_observation_count),100);
});
await check('Open bucket and future observations do not create false missing counts',async()=>{
  const r=await one("select * from analytics.mart_station_hourly where station_id='PARTIAL'");
  assert.equal(Number(r.expected_observation_count),0);assert.equal(Number(r.observation_count),1);assert.equal(r.collection_coverage,null);
  assert.equal(Number((await one("select count(*) n from staging.int_bw_observation_enriched where station_id='AFTER_CUTOFF'")).n),0);
});
await check('Disjoint tracking periods do not fill inactive gaps',async()=>{
  assert.equal(Number((await one("select count(*) n from staging.int_bw_expected_buckets where station_id='REJOIN'")).n),4);
});
await check('No configured tracking schedule produces an empty typed relation',async()=>{
  const {rows}=await db.query(data.manual_expected_sql);assert.equal(rows.length,0);
});
async function mutation(name,sql,test){
  await db.exec('begin');
  try{await db.exec(sql);const {rows}=await db.query(data.tests[test]);assert(rows.length>0,name);}
  finally{await db.exec('rollback');}
  checks.push(name);
}
await mutation('Gate rejects duplicate fact grains',"insert into analytics.fct_station_observation select * from analytics.fct_station_observation limit 1",'reporting_grains');
await mutation('Gate rejects future metadata association',"update analytics.fct_station_observation set metadata_collected_at=collected_at+interval '1 day' where station_id='A'",'reporting_fact_integrity');
await mutation('Gate rejects impossible coverage',"update analytics.mart_station_hourly set collection_coverage=2 where station_id='A'",'reporting_mart_ranges');
await mutation('Gate detects silently removed samples',"delete from staging.int_bw_reporting_samples where station_id='NO_META'",'reporting_sample_reconciliation');
await mutation('Gate rejects overlapping tracking intervals',"insert into staging.int_bw_tracking_periods select * from staging.int_bw_tracking_periods where station_id='A'",'reporting_tracking_periods');
await mutation('Relationship gate rejects orphan collector runs',"update analytics.fct_station_observation set run_id='00000000-0000-0000-0000-000000000099' where station_id='A'",'relationships_fct_station_observation_run_id');
await mutation('Gate rejects an incorrect daily mean',"update analytics.mart_station_daily set avg_bikes_available=11.5 where station_id='A'",'reporting_mart_reconciliation');
const version=(await one('select version() v')).v;
const report={engine:version,gateCount,behaviorChecks:checks.length,checks,result:'PASS'};
fs.writeFileSync(path.join(root,'validation-results.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
await db.close();
