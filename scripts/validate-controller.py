"""Final audit of actual artifacts, all zone/department slices and real solver recomputation."""
import copy,json,sys,hashlib,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'planner'))
from maharashtra import store
from maharashtra.timetable import catalogue,coverage
from maharashtra.pipeline import ARTIFACTS,connect,FEATURES
from engine import generate,validate,normalized_config
summary=store.zone_summary()
assert len(summary['zones'])==4 and sum(z['requests'] for z in summary['zones'])==500000
assert all(sum(z['departments'].values())==z['pending']>0 for z in summary['zones'])
known_day_services={s['train_no'] for s in catalogue() if s['service_days'] is not None}
assert all(m['train_no'] in known_day_services for m in store.dataset(dict(zone='Z1',start_date='2026-11-18'))['movements'])
out={'model_sha256':store.report()['model_sha256'],'zones':[],'matrix':[],'changes':{},'coverage':coverage(),'reference_count':len(catalogue()),'zone_summary':summary,'unknown_operating_days_excluded':True}
base=dict(start_date='2026-11-18',horizon=7,corridor='all',section='all',traffic_mode='reference')
for zone in ['Z1','Z2','Z3','Z4']:
    params=dict(base,zone=zone);data=store.dataset(params)
    assert data['fingerprint']==store.dataset(normalized_config(dict(params,incidents=[]),data))['fingerprint']
    for dep in ['TRK','SIG','TRC']:
        rows,total,_=store.query(dict(params,department=dep),15,pending=True)
        assert total>0 and rows and all(t['department']==dep and t['zone']==zone for t in rows)
        for t in rows:
            assert abs(t['priority']-sum(t['components'].values()))<1e-7
            prediction=store.infer(t['observation_features'])
            assert all(abs(prediction['risk'][h]-t['risk'][h])<1e-6 for h in ['7','30','90'])
        out['matrix'].append(dict(zone=zone,department=dep,pending=total,verified_requests=len(rows)))
    # Actual saved model-driven requests and actual reference movements. Reduced test batch is explicit.
    data['tasks']=([t for t in data['tasks'] if t['safety_action']=='NORMAL'][:24]+[t for t in data['tasks'] if t['safety_action']!='NORMAL'][:4])
    config=dict(params,incidents=[]);feasible=generate(data,config)
    assert feasible['blocks'] and validate(data,feasible)==[]
    assert all(b.get('candidate_slot') and b['candidate_slot']['available_duration']>=b['duration'] and not b['candidate_slot']['conflicting_trains'] for b in feasible['blocks'])
    blocked=copy.deepcopy(data)
    for section in blocked['sections']:blocked['movements'].append(dict(section=section['id'],start=0,end=7*1440,service_id='SYN-TEST-CLOSED',kind='Freight',provenance='SYNTHETIC full-horizon closure test'))
    infeasible=generate(blocked,config)
    assert not infeasible['blocks'] and len(infeasible['unscheduled'])==len(blocked['tasks'])
    assert validate(blocked,infeasible)==[]
    # Crew availability is changed without changing requests or movements.
    crew=copy.deepcopy(data);crew['resources'].update(TRK=0,SIG=0,TRC=0)
    no_crew=generate(crew,config);assert not no_crew['blocks']
    out['zones'].append(dict(zone=zone,requests_tested=len(data['tasks']),feasible=dict(status=feasible['solver_status'],scheduled=feasible['metrics']['tasks'],blocks=len(feasible['blocks']),shared=feasible['metrics']['joint_blocks'],sample_slot=feasible['blocks'][0]['candidate_slot'],audit_errors=validate(data,feasible)),infeasible=dict(status=infeasible['solver_status'],scheduled=0,deferred=len(infeasible['unscheduled']),condition='Synthetic full-horizon traffic closure; optional tasks must all defer'),crew_zero=dict(scheduled=len(no_crew['blocks']))))
    print(zone,out['zones'][-1],flush=True)
# Every subsequent batch must be reachable, disjoint, and collectively exhaustive.
for zone in ['Z1','Z2','Z3','Z4']:
    seen=set();expected=None
    offset=0
    while True:
        d=store.dataset(dict(base,zone=zone,batch_offset=offset));ids={t['id'] for t in d['tasks']};assert not seen&ids;seen|=ids
        expected=d['counts']['selected_pending']
        if offset+300>=expected:break
        offset+=300
    assert len(seen)==expected
    out['changes']['batches_'+zone]=dict(pending=expected,covered=len(seen),batches=offset//300+1)
# Actual API configuration inputs, not prefilled solver outputs.
cfg=dict(base,zone='Z1',corridor='Mumbai-Kalyan');d=store.dataset(cfg)
t=next(t for t in d['tasks'] if t['safety_action']=='NORMAL')
weights=dict(asset=0,safety=0,urgency=0,failure=100,overdue=0,operational=0)
weighted=store.dataset(dict(cfg,priority_weights=weights))
assert any(a['priority']!=next(b['priority'] for b in weighted['tasks'] if b['id']==a['id']) for a in d['tasks'])
changed=store.dataset(dict(cfg,task_overrides={t['id']:{'required_duration':1440,'crew':100}}))
ct=next(a for a in changed['tasks'] if a['id']==t['id']);assert ct['duration']>1440 and ct['crew']==100
assert len({d['fingerprint'],changed['fingerprint'],weighted['fingerprint']})==3
# Keep the same 12 requests to isolate changed inputs from batch size effects.
ids={a['id'] for a in d['tasks'] if a['safety_action']=='NORMAL'};chosen=list(sorted(ids))[:12];chosen[0]=t['id'];chosen=set(chosen)
for x in [d,changed,weighted]:x['tasks']=[a for a in x['tasks'] if a['id'] in chosen]
p=generate(d,dict(cfg,incidents=[]));p2=generate(changed,dict(cfg,incidents=[]));p3=generate(weighted,dict(cfg,incidents=[]))
assert all(validate(x,plan)==[] for x,plan in [(d,p),(changed,p2),(weighted,p3)])
assert t['id'] not in {tid for b in p2['blocks'] for tid in b['tasks']}
out['changes']['request_inputs']=dict(request=t['id'],baseline_scheduled=p['metrics']['tasks'],changed_scheduled=p2['metrics']['tasks'],weights_scheduled=p3['metrics']['tasks'],priority_before=t['priority'],priority_after=next(a['priority'] for a in weighted['tasks'] if a['id']==t['id']),duration_before=t['duration'],duration_after=ct['duration'],crew_before=t['crew'],crew_after=ct['crew'],solver_reexecuted=True)
other=store.dataset(dict(cfg,corridor='Churchgate-Virar'));assert {t['section'] for t in other['tasks']}!={t['section'] for t in d['tasks']}
out['changes']['corridor_recomputed']=True
out['counts']=store.report()['counts'];out['passed']=True
(ARTIFACTS/'controller-validation.json').write_text(json.dumps(out,indent=2));print('All controller artifact/solver audits passed.',flush=True)
