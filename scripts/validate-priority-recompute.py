"""Use real request features/saved predictions to prove a priority-weight change changes the chosen task."""
import copy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'planner'))
from maharashtra import store
from maharashtra.pipeline import ARTIFACTS
from engine import generate,validate
cfg=dict(zone='Z1',corridor='Mumbai-Kalyan',start_date='2026-11-18',horizon=7)
d=store.dataset(cfg);risk=store.dataset(dict(cfg,priority_weights=dict(asset=0,safety=0,urgency=0,failure=100,overdue=0,operational=0)))
normal=[t for t in d['tasks'] if t['safety_action']=='NORMAL' and t['department']!='TRC']
pair=next((a,b) for a in normal for b in normal if a['id']!=b['id'] and a['department']==b['department'] and a['priority']>b['priority'] and a['risk']['30']<b['risk']['30'])
ids={t['id'] for t in pair};results=[]
for data in [d,risk]:
    data['tasks']=[t for t in data['tasks'] if t['id'] in ids]
    for t in data['tasks']:t['duration']=202
    # A clearly synthetic test closure leaves room for only one job. No production timetable is changed.
    data['movements'].append(dict(section=data['sections'][0]['id'],start=360,end=7*1440,service_id='SYN-TEST-SINGLE-SLOT',kind='Freight',provenance='SYNTHETIC test fixture'))
    p=generate(data,dict(cfg,incidents=[]));assert validate(data,p)==[]
    selected={tid for b in p['blocks'] for tid in b['tasks']};assert len(selected)==1
    results.append(dict(selected=list(selected),priorities={t['id']:t['priority'] for t in data['tasks']},solver=p['solver_status']))
assert results[0]['selected']!=results[1]['selected']
report=json.loads((ARTIFACTS/'controller-validation.json').read_text());report['changes']['priority_competition']=dict(passed=True,baseline=results[0],reweighted=results[1],fixture='Two genuine model-scored requests, equal 202-minute buffered durations, one synthetic available slot; weights changed from default to 100% predicted 30-day risk.')
(ARTIFACTS/'controller-validation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report['changes']['priority_competition'],indent=2))
