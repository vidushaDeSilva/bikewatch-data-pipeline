"""Render the add-on against deterministic fixtures; no Neon credentials used."""
import json
from datetime import datetime, timezone
from pathlib import Path
import jinja2
import yaml
from pglast import parse_sql

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / 'generated'
OUT.mkdir(exist_ok=True)
PERIODS = [
    {'station_id': 'A', 'start_bucket': '2026-09-13T10:00:00Z', 'end_bucket': '2026-09-13T12:00:00Z'},
    {'station_id': 'NO_DATA', 'start_bucket': '2026-09-13T10:00:00Z', 'end_bucket': '2026-09-13T11:00:00Z'},
    {'station_id': 'DST_SPRING', 'start_bucket': '2026-03-08T05:00:00Z', 'end_bucket': '2026-03-09T04:00:00Z'},
    {'station_id': 'DST_FALL', 'start_bucket': '2026-11-01T04:00:00Z', 'end_bucket': '2026-11-02T05:00:00Z'},
    {'station_id': 'PARTIAL', 'start_bucket': '2026-11-02T12:00:00Z', 'end_bucket': None},
    {'station_id': 'REJOIN', 'start_bucket': '2026-09-13T10:00:00Z', 'end_bucket': '2026-09-13T10:30:00Z'},
    {'station_id': 'REJOIN', 'start_bucket': '2026-09-13T11:00:00Z', 'end_bucket': '2026-09-13T11:30:00Z'},
]
VARS = {'reporting_as_of': '2026-11-02T12:07:00Z', 'coverage_tracking_periods': PERIODS}
schema = yaml.safe_load((ROOT/'dbt_bikewatch/models/reporting/reporting_schema.yml').read_text())
order = ['int_bw_tracking_periods', 'int_bw_expected_buckets', 'int_bw_observation_enriched',
         'fct_station_observation', 'int_bw_reporting_samples', 'mart_station_current',
         'mart_station_hourly', 'mart_station_daily']
def ref(name):
    return ('analytics.' if name.startswith(('fct_', 'mart_')) else 'staging.') + name
env = jinja2.Environment(undefined=jinja2.StrictUndefined)
ctx = {'ref':ref, 'config':lambda **kwargs:'', 'var':lambda key,default=None:VARS.get(key,default),
       'run_started_at':datetime(2026,11,2,12,7,tzinfo=timezone.utc)}
macros = (ROOT/'dbt_bikewatch/macros/bw_reporting.sql').read_text()
def render(sql):
    rendered = env.from_string(macros+'\n'+sql).render(**ctx).strip()
    parse_sql(rendered)
    return rendered
models = {name:render((ROOT/'dbt_bikewatch/models/reporting'/f'{name}.sql').read_text()) for name in order}
tests = {p.stem:render(p.read_text()) for p in sorted((ROOT/'dbt_bikewatch/tests').glob('*.sql'))}
for model in schema['models']:
    for column in model.get('columns',[]):
        for t in column.get('data_tests',[]):
            name = t if isinstance(t,str) else next(iter(t))
            c, table = column['name'],ref(model['name'])
            if name == 'not_null': sql=f'select * from {table} where {c} is null'
            elif name == 'unique': sql=f'select {c} from {table} where {c} is not null group by {c} having count(*)>1'
            elif name == 'relationships':
                args=t[name]['arguments']; parent=args['to'].split("'")[1]
                sql=f'select * from {table} child where {c} is not null and not exists (select 1 from {ref(parent)} parent where parent.{args["field"]}=child.{c})'
            elif name == 'accepted_values':
                values=', '.join("'"+v+"'" for v in t[name]['arguments']['values'])
                sql=f'select * from {table} where {c} not in ({values})'
            else: raise ValueError(name)
            parse_sql(sql)
            tests[f'{name}_{model["name"]}_{c}']=sql

payload = {'order':order, 'models':models, 'tests':tests,
           'manual_expected_sql':None}
VARS['coverage_tracking_periods']=[]
payload['manual_expected_sql']=render((ROOT/'dbt_bikewatch/models/reporting/int_bw_tracking_periods.sql').read_text())
(OUT/'rendered.json').write_text(json.dumps(payload,indent=2))
print(f'Parsed {len(models)} rendered model queries and {len(tests)} test queries.')
