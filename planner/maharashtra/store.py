"""Bounded reads and inference adapters for the existing controller contracts."""
import copy
import hashlib
import json
import math
from functools import lru_cache
import numpy as np
from .network import network
from .timetable import movements,coverage,catalogue
WEIGHTS=dict(asset=15,safety=25,urgency=15,failure=25,overdue=10,operational=10)

def priority(features,p30,weights=None):
    weights=WEIGHTS if weights is None else weights
    if not isinstance(weights,dict):raise ValueError('Priority weights must be a six-factor object.')
    if set(weights)!=set(WEIGHTS) or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in weights.values()) or abs(sum(weights.values())-100)>1e-8:raise ValueError('Six nonnegative weights must sum to 100.')
    age,defects,overdue,load,condition,weather,last,measurement,dep=map(float,features)
    severity=min(5,max(1,math.ceil(max(0,measurement)/1.2)))
    raw=dict(asset=condition,safety=severity,urgency=last,failure=float(p30),overdue=overdue,operational=load)
    normalized=dict(asset=min(1,max(0,condition)),safety=severity/5,urgency=min(1,max(0,last/180)),failure=min(1,max(0,float(p30))),overdue=min(1,max(0,overdue/120)),operational=min(1,max(0,load)))
    components={k:round(normalized[k]*weights[k],1) for k in WEIGHTS}
    details={k:dict(original=raw[k],normalized=normalized[k],weight=weights[k],points=components[k],explanation={'asset':'Measured simulated deterioration (0–1)','safety':'Inspection severity class / 5; project simulation rule','urgency':'Days since intervention / 180, capped','failure':'Saved model 30-day event probability','overdue':'Days past simulated 120-day service interval / 120, capped','operational':'Simulated traffic exposure (0–1)'}[k]) for k in WEIGHTS}
    return components,details

@lru_cache(maxsize=1)
def artifact_model(stamp):
    from .pipeline import ARTIFACTS
    import joblib
    return joblib.load(ARTIFACTS/'models.joblib')

def report():
    from .pipeline import connect,ARTIFACTS
    if not (ARTIFACTS/'simulation.sqlite').exists():raise ValueError('Build Maharashtra data first: npm run data:maharashtra')
    with connect() as db:r=json.loads(db.execute("SELECT value FROM metadata WHERE key='report'").fetchone()[0])
    if hashlib.sha256((ARTIFACTS/'models.joblib').read_bytes()).hexdigest()!=r['model_sha256']:raise ValueError('Model artifact digest mismatch; rebuild the simulation.')
    return r

def model_comparison():
    from .pipeline import ARTIFACTS
    path=ARTIFACTS/'model-comparison.json'
    if not path.exists():return dict(available=False,reason='No model-comparison artifact exists yet. It is written by the lifecycle training pipeline; retrain with npm run data:maharashtra to generate it.')
    artifact=json.loads(path.read_text());deployed=None;match=False
    try:
        deployed=report()['model_sha256'];match=artifact.get('model_sha256')==deployed
    except Exception:pass
    return dict(available=True,artifact_model_matches_deployed=match,deployed_model_sha256=deployed,artifact=artifact)

def selection(params):
    graph=network();zone=params.get('zone','Z1');corridor=params.get('corridor','all');section=params.get('section','all')
    if zone not in {z['id'] for z in graph['zones']}:raise ValueError('Unknown project zone.')
    sections=[s for s in graph['sections'] if s['zone']==zone]
    if corridor!='all':
        if not any(c['id']==corridor and c['zone']==zone for c in graph['corridors']):raise ValueError('Corridor does not belong to this zone.')
        sections=[s for s in sections if corridor in s['corridors']]
    if section!='all':
        if section not in {s['id'] for s in sections}:raise ValueError('Section does not belong to this selection.')
        sections=[s for s in sections if s['id']==section]
    return graph,sections

def query(params,limit=15,offset=0,pending=False,weights=None,batch_offset=0):
    from .pipeline import connect
    graph,sections=selection(params);ids=[s['id'] for s in sections]
    dep=params.get('department','all');search=str(params.get('search','')).strip()[:100]
    if dep not in ['all','TRK','SIG','TRC']:raise ValueError('Unknown department.')
    where='r.zone=? AND r.section IN ('+','.join('?' for _ in ids)+')';args=[params.get('zone','Z1')]+list(ids)
    if pending:where+=" AND r.status='Pending'"
    scope_where,scope_args=where,args.copy()
    if dep!='all':where+=' AND r.department=?';args.append(dep)
    if search:
        where+=" AND (('SYN-MH-' || printf('%07d',r.id)) LIKE ? OR ('SYN-A-' || printf('%05d',r.asset_id)) LIKE ? OR r.department LIKE ? OR (CASE r.department WHEN 'TRK' THEN CASE (r.asset_id/3)%3 WHEN 0 THEN 'Track inspection' WHEN 1 THEN 'Deep screening' ELSE 'Rail welding' END WHEN 'SIG' THEN CASE (r.asset_id/3)%3 WHEN 0 THEN 'Signal inspection' WHEN 1 THEN 'Track circuit testing' ELSE 'Point machine repair' END ELSE CASE (r.asset_id/3)%3 WHEN 0 THEN 'OHE inspection' WHEN 1 THEN 'OHE isolation work' ELSE 'Electrical inspection' END END) LIKE ?)";args.extend(['%'+search+'%']*4)
    batch_offset=max(0,int(batch_offset));full_scope=params.get('full_scope')=='1'
    if weights is not None and batch_offset%300:raise ValueError('Planning batches start at a nonnegative multiple of 300.')
    if isinstance(weights,str):
        try:weights=json.loads(weights)
        except json.JSONDecodeError as e:raise ValueError('Priority weights must be a valid six-factor object.') from e
    if weights is not None:priority([0]*9,0,weights)
    limit=min(500,max(1,int(limit)));offset=max(0,int(offset))
    with connect() as db:
        total=db.execute('SELECT COUNT(*) FROM requests r WHERE '+where,args).fetchone()[0]
        select='SELECT r.*,o.features,o.day,(SELECT COUNT(*) FROM completions c WHERE c.asset_id=r.asset_id AND c.day<=o.day) AS maintenance_count,a.latitude,a.longitude,a.fraction FROM requests r JOIN observations o ON r.observation_id=o.id JOIN assets a ON r.asset_id=a.id WHERE '
        if weights is not None and full_scope:
            f=lambda index:f"CAST(json_extract(o.features,'$[{index}]') AS REAL)"
            clamp=lambda expr:f'MIN(1.0,MAX(0.0,{expr}))'
            measurement=f'MAX(0.0,{f(7)})';scaled=measurement+'/1.2'
            severity=f'MIN(5.0,MAX(1.0,CAST(({measurement}+1.199999999)/1.2 AS INTEGER)))'
            factors={'asset':clamp(f(4)),'safety':f'({severity}/5.0)','urgency':clamp(f(6)+'/180.0'),'failure':clamp('r.p30'),'overdue':clamp(f(2)+'/120.0'),'operational':clamp(f(3))}
            terms=['ROUND(('+factors[k]+')*?,1)' for k in WEIGHTS]
            score_args=[weights[k] for k in WEIGHTS]
            rows=db.execute(select+where+' ORDER BY ('+'+'.join(terms)+') DESC,r.id LIMIT ? OFFSET ?',args+score_args+[limit,offset]).fetchall()
        elif weights is None:
            rows=db.execute(select+where+' ORDER BY r.priority DESC,r.id LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
        else:
            rows=db.execute(select+scope_where+' ORDER BY r.priority DESC,r.id LIMIT 300 OFFSET ?',scope_args+[batch_offset]).fetchall()
    items=[task(dict(row),graph) for row in rows]
    batch_total=total if weights is None or full_scope else 0
    if weights is not None:
        if not full_scope:
            if dep!='all':items=[item for item in items if item['department']==dep]
            if search:
                needle=search.lower();items=[item for item in items if needle in f"{item['id']} {item['asset_id']} {item['department']} {item['task_type']}".lower()]
        for item in items:
            item['components'],item['score_details']=priority(item['observation_features'],item['risk']['30'],weights)
            item['priority']=round(sum(item['components'].values()),1)
        if not full_scope:
            items.sort(key=lambda t:(-t['priority'],t['id']))
            batch_total=len(items)
            items=items[offset:offset+limit]
    return items,total,batch_total

def asset_query(params,limit=25,offset=0):
    """Paginate the saved asset registry independently from the active 300-task batch."""
    from .pipeline import connect
    report()
    graph,sections=selection(params);ids=[s['id'] for s in sections]
    dep=params.get('department','all');search=str(params.get('search','')).strip()[:100]
    if dep not in ['all','TRK','SIG','TRC']:raise ValueError('Unknown department.')
    where='a.zone=? AND a.section IN ('+','.join('?' for _ in ids)+')';args=[params.get('zone','Z1')]+list(ids)
    if dep!='all':where+=' AND a.department=?';args.append(dep)
    if search:where+=" AND ('SYN-A-' || printf('%05d',a.id)) LIKE ?";args.append('%'+search+'%')
    with connect() as db:
        total=int(db.execute('SELECT COUNT(*) FROM assets a WHERE '+where,args).fetchone()[0])
        rows=db.execute('''SELECT a.id AS asset_id,a.department,a.section,a.zone,a.latitude,a.longitude,a.fraction,
          r.p7,r.p30,r.p90,o.features,
          (SELECT COUNT(*) FROM requests q WHERE q.asset_id=a.id) AS request_count,
          (SELECT COUNT(*) FROM requests q WHERE q.asset_id=a.id AND q.status='Pending') AS pending_requests
          FROM assets a
          JOIN requests r ON r.id=(SELECT MAX(q.id) FROM requests q WHERE q.asset_id=a.id)
          JOIN observations o ON o.id=r.observation_id
          WHERE '''+where+' ORDER BY a.section,a.department,a.id LIMIT ? OFFSET ?',args+[min(100,max(1,int(limit))),max(0,int(offset))]).fetchall()
    section_map={s['id']:s for s in graph['sections']}
    assets=[]
    for row in rows:
        section=section_map[row['section']]
        features=json.loads(row['features'])
        km=round(row['fraction']*(section['km_end']-.5),2)
        assets.append(dict(id=f'SYN-A-{row["asset_id"]:05}',asset_type={'TRK':'Track asset','SIG':'Signalling asset','TRC':'OHE/electrical asset'}[row['department']],department=row['department'],section=row['section'],zone=row['zone'],km=km,latitude=row['latitude'],longitude=row['longitude'],criticality=float(features[4]),risk={'7':row['p7'],'30':row['p30'],'90':row['p90']},request_count=row['request_count'],pending_requests=row['pending_requests'],provenance='SYNTHETIC endpoint interpolation'))
    return dict(assets=assets,total=total)

def _zone_summary_uncached():
    from .pipeline import connect
    graph=network();counts={z['id']:dict(id=z['id'],name=z['name'],requests=0,pending=0,departments={d:0 for d in ('TRK','SIG','TRC')}) for z in graph['zones']}
    with connect() as db:
        for row in db.execute('SELECT zone,department,status,COUNT(*) AS n FROM requests GROUP BY zone,department,status'):
            item=counts.get(row['zone'])
            if item is None:continue
            n=int(row['n']);item['requests']+=n
            if row['status']=='Pending':
                item['pending']+=n;item['departments'][row['department']]+=n
    return dict(zones=list(counts.values()),coverage=coverage(),provenance='Live counts from the local synthetic request database')

@lru_cache(maxsize=4)
def _zone_summary_cached(stamp):
    return _zone_summary_uncached()

def zone_summary():
    """Small zone/department counts for the Controller's zone switcher.

    The aggregation scans the 500,000-row request table, and the counts can
    only change when the simulation database is rebuilt, so the result is
    cached against the database file stamp (mtime + size) instead of being
    recomputed on every request. A rebuild changes the stamp and therefore
    the counts served. Callers get a copy so the cached value cannot be
    mutated.
    """
    from .pipeline import ARTIFACTS
    try:
        stat=(ARTIFACTS/'simulation.sqlite').stat()
    except OSError:
        return _zone_summary_uncached()
    return copy.deepcopy(_zone_summary_cached((stat.st_mtime_ns,stat.st_size)))

def task(row,graph):
    f=json.loads(row['features']);components,details=priority(f,row['p30']);severity=math.ceil(max(0,f[7])/1.2);severity=min(5,max(1,severity));sec=next(s for s in graph['sections'] if s['id']==row['section'])
    dep=row['department'];kinds={'TRK':['Track inspection','Deep screening','Rail welding'],'SIG':['Signal inspection','Track circuit testing','Point machine repair'],'TRC':['OHE inspection','OHE isolation work','Electrical inspection']};kind=kinds[dep][(row['asset_id']//3)%3]
    km=round(row['fraction']*(sec['km_end']-.5),2)
    return dict(id=f'SYN-MH-{row["id"]:07}',asset_id=f'SYN-A-{row["asset_id"]:05}',department=dep,section=row['section'],zone=row['zone'],corridors=sec['corridors'],line='Up' if row['asset_id']%2 else 'Down',km_start=km,km_end=round(km+.3,2),latitude=row['latitude'],longitude=row['longitude'],position_provenance='SYNTHETIC endpoint interpolation',task_type=kind,duration=int(row['duration']+math.ceil(row['buffer'])),predicted_duration=row['duration'],duration_buffer=round(row['buffer'],1),release_day=0,due_day=max(0,min(29,int((1-f[4])*14))),severity=severity,safety_action='STOP_AND_REVIEW' if severity==5 else 'NORMAL',safety_criticality=severity/5,asset_criticality=float(f[4]),overdue_days=int(f[2]),crew=2+row['asset_id']%3,machine={'Deep screening':'tamper','Rail welding':'welder','OHE isolation work':'tower'}.get(kind),power_required=dep=='TRC',separation_confirmed=False,dependencies=[],components=components,score_details=details,priority=round(sum(components.values()),1),risk={'7':row['p7'],'30':row['p30'],'90':row['p90']},traffic_impact=float(f[3]),status=row['status'],provenance='SYNTHETIC lifecycle maintenance request',observation_day=row['day'],historical_maintenance_count=row['maintenance_count'],observation_features=f,inspection_history=dict(observation_id=row['observation_id'],day=row['day'],measurement_mm=f[7],condition=f[4]),synthetic_due_day=row['day']+max(0,min(29,int((1-f[4])*14))))

def _engineered_input(asset_id,observation_id,current):
    # Rebuild the training-time history features for one stored observation from
    # the asset's own earlier inspections (identical lag/padding rule as training).
    from .pipeline import connect,engineer_row
    with connect() as db:
        total=db.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
        if type(asset_id) is not int or type(observation_id) is not int or not 0<=asset_id<total or observation_id<0 or observation_id%total!=asset_id or db.execute('SELECT 1 FROM observations WHERE id=?',(observation_id,)).fetchone() is None:
            raise ValueError('Observation does not belong to this stored asset.')
        # Observation ids are the 0-based flat index month*assets+asset (build()).
        month=observation_id//total
        ids={0:asset_id,1:(max(month-1,0))*total+asset_id,2:(max(month-2,0))*total+asset_id,3:(max(month-3,0))*total+asset_id}
        found={row[0]:json.loads(row[1]) for row in db.execute('SELECT id,features FROM observations WHERE id IN (?,?,?,?)',tuple(ids.values()))}
    return engineer_row(current,found.get(ids[1]),found.get(ids[2]),found.get(ids[3]),found.get(ids[0]))[None,:]

def infer(features,asset_id=None,observation_id=None):
    from .pipeline import ARTIFACTS,FEATURES,engineer_row
    r=report();x=np.asarray(features,dtype=float)
    if x.shape!=(len(FEATURES),) or not np.isfinite(x).all():raise ValueError('Provide all nine finite observation features in documented order.')
    if any(x[i]<0 for i in range(len(x))) or any(x[i]>1 for i in [3,4,5]) or x[8] not in [0,1,2]:raise ValueError('Features outside the documented range.')
    models=artifact_model((ARTIFACTS/'models.joblib').stat().st_mtime_ns)
    def model_input(fset):
        if fset=='poly':
            from .pipeline import poly_row
            return poly_row(x)[None,:]
        if fset!='engineered':return x[None,:]
        if asset_id is not None and observation_id is not None:return _engineered_input(int(asset_id),int(observation_id),x)
        return engineer_row(x)[None,:]
    risk_features=models.get('risk_features',['base']*3)
    p=np.maximum.accumulate([m.predict_proba(model_input(risk_features[j]))[0,1] for j,m in enumerate(models['risk'])]);c,d=priority(x,p[1]);duration=max(15,float(models['duration'].predict(model_input(models.get('duration_features','base')))[0]))
    return dict(risk=dict(zip(['7','30','90'],map(float,p))),predicted_duration=duration,duration_buffer=models['buffer'],components=c,score_details=d,priority=round(sum(c.values()),1),model_sha256=r['model_sha256'],provenance='SYNTHETIC model inference')

def update_task(t, change, weights):
    """One server-owned inference/scoring path for planning and twin scenarios."""
    if not isinstance(change,dict) or set(change)-{'features','crew','required_duration'}:raise ValueError('Unknown task override field.')
    if 'features' in change:
        prediction=infer(change['features'],int(t['asset_id'].split('-')[-1]),t['inspection_history']['observation_id']);t.update(risk=prediction['risk'],predicted_duration=math.ceil(prediction['predicted_duration']),duration_buffer=prediction['duration_buffer'],observation_features=change['features'])
        t['severity']=min(5,max(1,math.ceil(max(0,change['features'][7])/1.2)))
        t['safety_action']='STOP_AND_REVIEW' if t['severity']==5 else 'NORMAL'
        t['due_day']=max(0,min(29,int((1-change['features'][4])*14)))
        t['overdue_days']=int(change['features'][2])
        t['asset_criticality']=float(change['features'][4])
        t['safety_criticality']=t['severity']/5
        t['traffic_impact']=float(change['features'][3])
    if 'crew' in change:
        if type(change['crew']) is not int or not 1<=change['crew']<=100:raise ValueError('Crew demand must be an integer from 1 to 100.')
        t['crew']=change['crew']
    work=change.get('required_duration',t.get('required_duration_override') or t['predicted_duration'])
    if type(work) is not int or not 15<=work<=1440:raise ValueError('Required duration must be 15–1440 whole minutes.')
    t['required_duration_override']=change.get('required_duration',t.get('required_duration_override'))
    t['duration']=max(work,math.ceil(t['predicted_duration']))+math.ceil(t['duration_buffer'])
    t['components'],t['score_details']=priority(t['observation_features'],t['risk']['30'],weights)
    t['priority']=round(sum(t['components'].values()),1)

def dataset(params):
    from datetime import date, timedelta
    from .pipeline import ARTIFACTS
    def decoded(value,default):
        if value is None:return default
        if isinstance(value,str):
            try:return json.loads(value)
            except json.JSONDecodeError as e:raise ValueError('Planning inputs must be valid JSON.') from e
        return value
    try:
        planning_start=int(params.get('planning_start',0));planning_end=int(params.get('planning_end',360))
    except (TypeError,ValueError):
        raise ValueError('Planning period bounds must be whole minutes of day.')
    if not 0<=planning_start<planning_end<=1440:raise ValueError('Planning period must satisfy 00:00 <= start < end <= 24:00. The legacy synthetic scenario is 0-360; a full-day scenario is 0-1440.')
    planning_label=f'{planning_start//60:02d}:{planning_start%60:02d}\u2013{planning_end//60:02d}:{planning_end%60:02d}'
    params=dict(zone=params.get('zone','Z1'),corridor=params.get('corridor','all'),section=params.get('section','all'),start_date=params.get('start_date',date.today().isoformat()),horizon=int(params.get('horizon',7)),traffic_mode=params.get('traffic_mode','reference'),resource_overrides=decoded(params.get('resource_overrides'),{}),batch_offset=int(params.get('batch_offset',0)),priority_weights=decoded(params.get('priority_weights'),WEIGHTS),task_overrides=decoded(params.get('task_overrides'),{}),planning_start=planning_start,planning_end=planning_end)
    r=report();graph,sections=selection(params)
    offset=params['batch_offset']
    if offset<0 or offset%300:raise ValueError('Planning batches start at a nonnegative multiple of 300.')
    tasks,total,_=query(params,300,offset,pending=True)
    if offset and offset>=total:raise ValueError('No pending requests in this batch; choose an earlier batch.')
    weights=params['priority_weights'];priority([0]*9,0,weights)
    overrides=params['task_overrides']
    if not isinstance(overrides,dict) or len(overrides)>300 or any(k not in {t['id'] for t in tasks} for k in overrides):raise ValueError('Task overrides must refer to this selected planning batch.')
    for t in tasks:
        update_task(t,overrides.get(t['id'],{}),weights)
    tasks.sort(key=lambda t:(-t['priority'],t['id']))
    start=params.get('start_date',date.today().isoformat());date.fromisoformat(start);horizon=int(params.get('horizon',7))
    if horizon not in [7,30]:raise ValueError('Choose 7 or 30 days.')
    mode=params.get('traffic_mode','reference');traffic=[m for m in movements(start,horizon,mode) if m['section'] in {s['id'] for s in sections}]
    # Work-feasibility floor per section: the smallest block this batch
    # would need there (task duration already includes the uncertainty
    # buffer; a block adds the engine's 10-minute setup). Candidate gaps
    # below this floor are timetable gaps only, never feasible work slots.
    min_block_by_section={}
    for t in tasks:
        need=t['duration']+10
        if t['section'] not in min_block_by_section or need<min_block_by_section[t['section']]:min_block_by_section[t['section']]=need
    candidates=[]
    for sec in sections:
        for day in range(horizon):
            day_start=day*1440
            gaps=[(day_start+planning_start,day_start+planning_end)]
            section_movements=[m for m in traffic if m['section']==sec['id'] and m['start']-5<day_start+planning_end and m['end']+5>day_start+planning_start]
            for movement in section_movements:
                if movement['section']!=sec['id']:continue
                a,b=movement['start']-5,movement['end']+5
                gaps=[piece for lo,hi in gaps for piece in ([(lo,hi)] if b<=lo or a>=hi else [(lo,min(hi,a)),(max(lo,b),hi)]) if piece[1]>piece[0]]
            for index,(a,b) in enumerate(gaps,1):
                boundary=[m for m in section_movements if abs((m['end']+5)-a)<=1 or abs((m['start']-5)-b)<=1]
                candidates.append(dict(
                    id=f"CAND-{sec['id']}-{day+1:02}-{index:02}",section=sec['id'],
                    date=(date.fromisoformat(start)+timedelta(days=day)).isoformat(),day=day,
                    start=a,end=b,available_duration=b-a,conflicting_trains=[],
                    min_block_minutes=min_block_by_section.get(sec['id']),
                    work_feasible=(b-a>=min_block_by_section[sec['id']] if sec['id'] in min_block_by_section else None),
                    boundary_trains=sorted({str(m.get('train_no') or m.get('service_id')) for m in boundary}),
                    eligibility_conditions=[
                        f'Inside the synthetic {planning_label} candidate planning period selected in the Controller.',
                        'Timetable-derived gap only: unlisted traffic, freight, specials and restrictions are unknown, so this period is not verified free track time.',
                        'Selected timetable movements plus 5-minute headways are excluded; this is not complete traffic coverage.',
                        'Request duration, model uncertainty buffer, setup, section/line, safety, department, crew, equipment and dependencies are checked by CP-SAT.',
                        'Candidate only; no official track possession or operating clearance is implied.'
                    ],
                    provenance='SIMULATED candidate gap derived from the selected timetable references'
                ))
    assets=[dict(id=t['asset_id'],asset_type={'TRK':'Track asset','SIG':'Signalling asset','TRC':'OHE/electrical asset'}[t['department']],department=t['department'],section=t['section'],km=t['km_start'],criticality=t['asset_criticality'],risk=t['risk'],latitude=t['latitude'],longitude=t['longitude'],provenance='SYNTHETIC') for t in tasks]
    assets=list({a['id']:a for a in assets}.values())
    # Measured window diagnostics per section: what the selected timetable
    # references actually leave in the selected planning period versus
    # what this batch's requests need (task duration already includes the
    # uncertainty buffer; a block additionally needs the engine's 10-minute
    # setup/handover). Zero feasible pairs is a measured outcome, not an error.
    window_diagnostics={}
    for sec in sections:
        sec_candidates=[c for c in candidates if c['section']==sec['id']]
        sec_tasks=[t for t in tasks if t['section']==sec['id']]
        pairs=sum(1 for t in sec_tasks for c in sec_candidates if c['available_duration']>=t['duration']+10 and t['release_day']<=c['day']<=t['due_day'])
        window_diagnostics[sec['id']]=dict(movements_considered=sum(1 for m in traffic if m['section']==sec['id']),initial_window_minutes_per_day=planning_end-planning_start,candidate_windows=len(sec_candidates),work_feasible_windows=sum(1 for c in sec_candidates if c.get('work_feasible')),longest_candidate_window_minutes=max((c['available_duration'] for c in sec_candidates),default=0),longest_work_feasible_window_minutes=max((c['available_duration'] for c in sec_candidates if c.get('work_feasible')),default=0),minimum_block_minutes=min((t['duration']+10 for t in sec_tasks),default=None),tasks_considered=len(sec_tasks),feasible_task_window_pairs=pairs)
    forecasts=[[[0.0]*24 for s in graph['sections']] for d in range(30)]
    models=dict(risk=dict(name='Validation-selected saved classifiers',training_rows=r['training_rows'],validation_rows=r['test_rows'],validation=r['risk'],features=r.get('feature_importance_features',r['features']),feature_importance=r.get('feature_importance',[])),traffic=dict(name='No observed freight model active',mae=None),duration=r['duration'],evaluation=r,provenance=r['provenance'])
    resources=dict(TRK=24,SIG=18,TRC=18,tamper=2,welder=3,tower=2)
    overrides=params.get('resource_overrides',{})
    if not isinstance(overrides,dict) or any(k not in resources or type(v) is not int or not 0<=v<=100 for k,v in overrides.items()):raise ValueError('Resource overrides must be integer capacities from 0 to 100 for known pools.')
    resources.update(overrides)
    fingerprint=hashlib.sha256(json.dumps(dict(model=r['model_sha256'],selection=params,geography=graph,resources=resources,tasks=[(t['id'],t['duration'],t['priority']) for t in tasks],traffic=traffic,timetable_catalogue=catalogue()),sort_keys=True).encode()).hexdigest()
    return dict(version='mh-lifecycle-v1',seed=r['seed'],zones=graph['zones'],corridors=graph['corridors'],stations=graph['stations'],sections=sections,all_sections=graph['sections'],assets=assets,tasks=tasks,defects=[],services=catalogue(),movements=traffic,traffic=traffic,candidate_windows=candidates,forecasts=forecasts,resources=resources,models=models,counts=dict(r['counts'],selected_pending=total,planning_batch=len(tasks),batch_offset=offset,batch_number=offset//300+1,batch_count=math.ceil(total/300),outside_batch=max(0,total-len(tasks)),sections=len(sections),stations=len(graph['stations']),tasks=total,defects=sum(t['severity']>=4 for t in tasks)),coverage=coverage(),selection=params,fingerprint=fingerprint,planning_period=dict(start_minutes=planning_start,end_minutes=planning_end,label=planning_label,legacy=planning_start==0 and planning_end==360,source='Controller-selected synthetic planning period. Candidate gaps are derived from the selected timetable references inside this period; they are conditional candidates, never verified free track time or an authorized possession.'),window_diagnostics=window_diagnostics,freight_status=dict(status='INACTIVE',observed_records=0,active_model=None,note='No observed freight dataset or active freight model exists in this workspace. Freight in scenarios is a simulated assumption, never an observed record; Maharashtra candidate windows come only from the selected passenger timetable references. The freight forecaster activates only after a controller uploads at least 42 complete observed days per section and a candidate beats its baseline gates.'),assumptions=['All maintenance/assets/outcomes and resource pools are SYNTHETIC academic simulation.', 'Only selected public train references with explicitly listed operating days are protected in the selected-date candidate schedule. Other timetable records remain source references only; all train traffic, freight, specials, restrictions and current service dates remain incomplete. Every allocation is CONDITIONAL/SIMULATED.', f'{planning_label} is the Controller-selected synthetic candidate planning period for this dataset (legacy scenario: 00:00–06:00), never an official possession. Both tracks are reserved. No dispatch or railway clearance. Candidate gaps are timetable-derived only; work-feasible gaps additionally fit the smallest selected request block (work + uncertainty buffer + 10-minute setup) on that section.', 'Each batch selects at most 300 pending requests in saved-score order. Next batch advances by 300; every remaining request stays visible in the queue. Alternative weights reorder only within the selected batch. Draft batches are independent alternatives, not a combined conflict-checked schedule or globally optimal plan.', 'Class 5 simulated inspection evidence is STOP_AND_REVIEW independently of priority and is withheld from routine allocation.', 'Predicted work duration includes a validation-residual 90th percentile buffer plus ten-minute setup. This is an estimate, not a guarantee.', 'Rounded station map references and straight interpolated synthetic asset locations are not surveyed track positions.'])
