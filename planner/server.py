"""Loopback-only compute service. Application authorization stays in the app API."""
import json
import os
import secrets
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from scenario import build_scenario, describe
from engine import generate, validate, metrics, selected_candidate_slot
import operations
import freight_model
import hashlib
import delay as delay_mod
import hypergraph as hypergraph_mod
import twin as twin_mod
from urllib.parse import urlparse, parse_qs
from maharashtra import store as mh

DATA = None
def legacy_data():
    global DATA
    if DATA is None: DATA=build_scenario()
    return DATA

def selected_data(config):
    if 'zone' not in config:return legacy_data()
    data=mh.dataset({**{'resource_overrides':{}},**{k:config[k] for k in ['zone','corridor','section','start_date','horizon','traffic_mode','resource_overrides','batch_offset','priority_weights','task_overrides','planning_start','planning_end'] if k in config}})
    delays=config.get('delays') or []
    if config.get('disruptions_enabled') is False:
        delays=[]  # simulated disruptions disabled by configuration
    if delays:
        # Controller-entered delay simulation: shift the selected timetable
        # movements, rebuild candidate windows from them and bind the plan
        # fingerprint to the delayed inputs, so freshness checks reproduce
        # exactly this scenario. ML features are untouched by design.
        shifted,applied,knock_on=delay_mod.simulate_network(data['movements'],delays)
        data=dict(data);data['movements']=shifted;data['traffic']=shifted
        data['candidate_windows']=delay_mod.candidate_windows(data,shifted)
        data['delay_applied']=applied
        data['delay_knock_on']=knock_on
        data['delay_service_ids']=sorted({a['service_id'] for a in applied}|{k['service_id'] for k in knock_on})
        data['fingerprint']=hashlib.sha256((data['fingerprint']+json.dumps(delays,sort_keys=True)).encode()).hexdigest()
    changes=config.get('twin_changes')
    if changes:
        data,applied=twin_mod.apply_plan_changes(data,changes)
        data['fingerprint']=twin_mod.changes_fingerprint(data['fingerprint'],changes)
        data['twin_applied']=applied
        data['candidate_windows']=delay_mod.candidate_windows(data,data['movements'])
    # Hypergraph clustering: section-day task groups that can share one
    # candidate window. solve() bundles these first, under the same
    # compatibility rules as its greedy pass; CP-SAT remains the verifier.
    data=dict(data);data['suggested_groups']=hypergraph_mod.suggested_groups(data)
    return data
LOCK = threading.Lock()
def bound_fingerprint(dataset_fingerprint, config):
    # A plan's freshness fingerprint binds the dataset fingerprint AND the
    # solver configuration stored in its (normalized) plan config, so
    # changing solver settings marks older plans stale. Legacy plans carry
    # the raw dataset fingerprint; freshness accepts either form.
    keys={k:(config or {}).get(k) for k in ('solver_time_limit','solver_workers','solver_seed','strategy','disruptions_enabled')}
    return hashlib.sha256((dataset_fingerprint+json.dumps(keys,sort_keys=True,default=str)).encode()).hexdigest()

def clustering_report(data, config, blocks):
    """Hypergraph clustering as CP-SAT sees it.

    Returns the suggested section-day groups, the bundles engine.bundle()
    actually forms from them for this scope (the same call solve() makes,
    so this preview cannot drift from the solver), per-section counts for
    the chart, and — when the current plan's blocks are supplied — how
    many clusters the plan realized as joint blocks.
    """
    import engine as engine_mod
    groups=data.get('suggested_groups') or []
    cluster_sets=[set(g['task_ids']) for g in groups]
    tasks=engine_mod.effective_tasks(data, {**config, 'incidents': config.get('incidents') or []})
    eligible=[t for t in tasks if t.get('safety_action')!='STOP_AND_REVIEW' and t['release_day']<config['horizon']]
    bundles=engine_mod.bundle(eligible, True, cluster_sets)
    def is_cluster(group):
        return len(group)>=2 and any({t['id'] for t in group}<=cs for cs in cluster_sets)
    cluster_bundles=[g for g in bundles if is_cluster(g)]
    section_names={s['id']:s['name'] for s in data.get('sections') or []}
    per_section={}
    for g in groups:
        row=per_section.setdefault(g['section'], dict(section=g['section'], name=section_names.get(g['section'], g['section']), suggested=0, cluster_bundles=0))
        row['suggested']+=1
    for g in cluster_bundles:
        row=per_section.setdefault(g[0]['section'], dict(section=g[0]['section'], name=section_names.get(g[0]['section'], g[0]['section']), suggested=0, cluster_bundles=0))
        row['cluster_bundles']+=1
    realized=None
    if blocks:
        joint=[b for b in blocks if len(b.get('tasks') or [])>=2]
        realized=dict(plan_blocks=len(blocks), joint_blocks=len(joint),
                      cluster_flagged=sum(1 for b in blocks if b.get('cluster')),
                      cluster_shaped_blocks=sum(1 for b in joint if any(set(b['tasks'])<=set(g['task_ids']) for g in groups)))
    return dict(groups=groups,
                summary=dict(suggested_groups=len(groups),
                             tasks_in_clusters=len(set().union(*cluster_sets)) if cluster_sets else 0,
                             eligible_tasks=len(eligible), bundles=len(bundles),
                             cluster_bundles=len(cluster_bundles),
                             clustered_tasks=sum(len(g) for g in cluster_bundles)),
                per_section=sorted(per_section.values(), key=lambda r: r['section']),
                cluster_bundle_tasks=[[t['id'] for t in g] for g in cluster_bundles],
                realized=realized)

def busy(message):
    # Concurrency contract, stated explicitly: the local planner runs ONE
    # planning/model job at a time. A concurrent request is REJECTED with
    # 409 PLANNING_BUSY; it is never queued, and multi-user concurrent
    # planning is not supported or claimed.
    return {'error':message,'code':'PLANNING_BUSY','busy':True,'behaviour':'rejected_not_queued','retry':'Wait for the current run to finish, then retry.'}
TOKEN = os.environ.get('RAILBLOCK_ENGINE_TOKEN')
if not TOKEN:
    raise RuntimeError('Start with npm run dev so the app and planner share a local service token.')


class Handler(BaseHTTPRequestHandler):
    def respond(self, value, status=200):
        payload = json.dumps(value, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path=='/health':
            return self.respond({'ready': True})
        if not secrets.compare_digest(self.headers.get('X-Engine-Token', ''), TOKEN):
            return self.respond({'error': 'App authentication required.'}, 403)
        parsed=urlparse(self.path)
        params={k:v[-1] for k,v in parse_qs(parsed.query).items()}
        try:
            if parsed.path=='/legacy-dataset':
                return self.respond(describe(legacy_data()))
            if parsed.path=='/dataset':
                data=mh.dataset(params)
                data=dict(data);data['suggested_groups']=hypergraph_mod.suggested_groups(data)
                return self.respond(data)
            if parsed.path=='/zone-summary':
                return self.respond(mh.zone_summary())
            if parsed.path=='/assets':
                return self.respond(mh.asset_query(params,int(params.get('limit',25)),int(params.get('offset',0))))
            if parsed.path=='/requests':
                rows,total,batch_total=mh.query(params,int(params.get('limit',15)),int(params.get('offset',0)),params.get('status','all')=='Pending',params.get('priority_weights'),int(params.get('batch_offset',0)))
                return self.respond(dict(tasks=rows,total=total,batch_total=batch_total))
        except (ValueError, TypeError, OSError) as e:
            return self.respond({'error':str(e)},400)
        return self.respond({'error': 'Not found'}, 404)

    def do_POST(self):
        if not secrets.compare_digest(self.headers.get('X-Engine-Token', ''), TOKEN):
            return self.respond({'error': 'App authentication required.'}, 403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0<length<3_000_000:
                return self.respond({'error': 'Invalid request size.'}, 413)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise ValueError('JSON body must be an object.')
            if self.path=='/plan-freshness':
                plan=body['plan']
                if not plan.get('config',{}).get('zone') or not plan.get('input_fingerprint'):
                    return self.respond({'fresh':False})
                current=selected_data(plan['config'])
                stored=str(plan['input_fingerprint'])
                fresh=secrets.compare_digest(stored,current['fingerprint']) or secrets.compare_digest(stored,bound_fingerprint(current['fingerprint'],plan['config']))
                return self.respond({'fresh':fresh})
            if self.path=='/maintenance/infer':
                # The deployed duration model uses engineered history
                # features, so a stored observation can only be reproduced
                # when its identity is supplied: asset_id (int or
                # 'SYN-A-00123') + observation_id rebuild the asset's true
                # lag features. Without them infer() falls back to a
                # no-history row and the duration differs from the stored
                # prediction (the 166 vs 161 test failure).
                aid=body.get('asset_id');oid=body.get('observation_id')
                if isinstance(aid,str):aid=int(aid.split('-')[-1])
                return self.respond(mh.infer(body['features'],aid,oid))
            if self.path=='/model-comparison':
                return self.respond(mh.model_comparison())
            if self.path=='/ml/status':
                return self.respond(freight_model.status())
            if self.path in ['/ml/train','/ml/activate']:
                if not LOCK.acquire(blocking=False):
                    return self.respond(busy('A model or planning run is already in progress.'),409)
                try:
                    if self.path=='/ml/train':
                        return self.respond(freight_model.train(body['csv'],body['source'],body['sections']))
                    return self.respond(freight_model.activate(body['id']))
                finally:
                    LOCK.release()
            if self.path=='/operations/generate':
                if not LOCK.acquire(blocking=False):
                    return self.respond(busy('A planning run is already in progress.'),409)
                try:
                    data=body['input'];forecast,model_info=freight_model.forecasts(data)
                    data['forecasts']=forecast
                    result=operations.generate(data)
                    result.update(ml=model_info,forecast_snapshot=forecast)
                    return self.respond(result)
                finally:
                    LOCK.release()
            if self.path=='/twin/asset':
                return self.respond(twin_mod.asset_detail(body.get('asset_id')))
            if self.path=='/twin/infer':
                return self.respond(twin_mod.whatif(body.get('asset_id'),body.get('overrides') or {}))
            if self.path=='/twin/network':
                config={k:v for k,v in (body.get('config') or {}).items() if k!='delays'}
                data=selected_data(config)
                return self.respond(twin_mod.network_state(data,body.get('delays') or []))
            if self.path=='/twin/replan':
                if not LOCK.acquire(blocking=False):
                    return self.respond(busy('A planning run is already in progress. Please retry after it finishes.'), 409)
                try:
                    config={**body['config'],'twin_changes':body.get('changes')}
                    if not config['twin_changes']:
                        raise ValueError('Provide at least one twin change for the replan.')
                    if config.get('disruptions_enabled') is False:
                        raise ValueError('Simulated disruptions are disabled in this configuration; twin changes cannot be replanned.')
                    data=selected_data(config)
                    plan=generate(data,config,body.get('previous'),body.get('preferences'))
                    plan.update(input_fingerprint=bound_fingerprint(data['fingerprint'],plan['config']),dataset_snapshot=data,
                                allocation_status='CONDITIONAL/SIMULATED',
                                deferred_outside_batch=data['counts']['selected_pending']-len(data['tasks']),
                                twin_changes=data['twin_applied'],scenario_source='DIGITAL_TWIN')
                    return self.respond(plan)
                finally:
                    LOCK.release()
            if self.path=='/delay/analyze':
                config=body['config']; delays=config.get('delays') or []
                before=selected_data({k:v for k,v in config.items() if k!='delays'})
                after=selected_data(config) if delays else before
                return self.respond(delay_mod.analyze(before,after,delays,body.get('blocks') or []))
            if self.path=='/clustering':
                config=body['config']
                data=selected_data(config)
                return self.respond(clustering_report(data,config,body.get('blocks') or []))
            if self.path=='/operations/validate':
                return self.respond({'errors':operations.validate(body['input'],body['blocks'])})
            if self.path=='/generate':
                if not LOCK.acquire(blocking=False):
                    return self.respond(busy('A planning run is already in progress. Please retry after it finishes.'), 409)
                try:
                    data=selected_data(body['config'])
                    previous=body.get('previous')
                    if previous and previous.get('input_fingerprint') and previous['input_fingerprint'] not in (data['fingerprint'], bound_fingerprint(data['fingerprint'], previous.get('config') or {})):
                        raise ValueError('Inputs/model changed; generate a fresh plan rather than retain stale blocks.')
                    plan=generate(data, body['config'], previous, body.get('preferences'))
                    if data['version'].startswith('mh-'):
                        plan.update(input_fingerprint=bound_fingerprint(data['fingerprint'], plan['config']),dataset_snapshot=data,allocation_status='CONDITIONAL/SIMULATED',deferred_outside_batch=data['counts']['selected_pending']-len(data['tasks']))
                    if data.get('twin_applied'):
                        plan.update(twin_changes=data['twin_applied'],scenario_source='DIGITAL_TWIN')
                    return self.respond(plan)
                finally:
                    LOCK.release()
            if self.path=='/validate':
                data=selected_data(body['plan']['config'])
                if body['plan'].get('input_fingerprint') and body['plan']['input_fingerprint'] not in (data['fingerprint'], bound_fingerprint(data['fingerprint'], body['plan'].get('config') or {})):
                    raise ValueError('Inputs/model changed; regenerate before review.')
                errors = validate(data, body['plan'])
                if not errors:
                    for block in body['plan']['blocks']:
                        slot=selected_candidate_slot(data,block,body['plan']['config'])
                        if slot is not None:block['candidate_slot']=slot
                return self.respond(dict(errors=errors, blocks=body['plan']['blocks'], metrics=metrics(data, body['plan']['blocks'], body['plan']['config']['horizon'], body['plan']['config'])))
            return self.respond({'error': 'Not found'}, 404)
        except (ValueError, KeyError, TypeError) as e:
            return self.respond({'error': str(e)}, 400)
        except Exception:
            traceback.print_exc()
            return self.respond({'error': 'The local planner failed. See its terminal log.'}, 500)


if __name__=='__main__':
    host = os.environ.get('RAILBLOCK_BIND_HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '8008'))
    with ThreadingHTTPServer((host, port), Handler) as server:
        print(f'RailBlock planning engine ready on {host}:{port}', flush=True)
        server.serve_forever()
