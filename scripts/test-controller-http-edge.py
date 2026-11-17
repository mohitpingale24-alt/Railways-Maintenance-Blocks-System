"""Real-HTTP edge checks for the Controller planner endpoint.

Fills the gaps the in-process suite cannot cover: anonymous and
non-Controller authorization, malformed POST bodies, wrong-department
inputs and missing fields, exercised against the REAL running app on
http://localhost:3000 (start it first with `npm run dev`).

Hard checks assert the status the code is written to return, including
malformed JSON to /api/app, which must return 400 (a JSON syntax error
is a client error; only genuine internal errors may return 500).
One observational check reports what happened:
  * the monthly generate reports which solver branch actually fired
    (ROLLING_FEASIBLE cannot be forced through the HTTP API - see the
    notes at the bottom of this file).
PLANNING_BUSY propagation is verified with genuine concurrency, not a
test control: while the monthly generate holds the planner lock, a
second generate must be rejected with 409. The probe is timing-guarded
(see the monthly section) so a solver that finishes early degrades the
probe to a warning instead of a false failure.

Only records created by this run are removed afterwards, using the
same local-D1 cleanup as scripts/test-control.py.
"""
import datetime
import http.cookiejar
import json
import pathlib
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid

BASE = 'http://localhost:3000'
ROOT = pathlib.Path(__file__).resolve().parents[1]
PASSWORD = 'RailOps@123'  # demo password already used by scripts/test-control.py
created_jobs = []
created_twins = []
created_plans = []
checks = 0
warnings = []


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def raw(self, path, data=None, content_type='application/json', origin=BASE, method=None):
        headers = {}
        if content_type:
            headers['Content-Type'] = content_type
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=260) as r:
                body = r.read().decode()
                return r.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                return e.code, (json.loads(body) if body else {})
            except json.JSONDecodeError:
                return e.code, {'unparsed': body[:200]}

    def call(self, body=None, path='/api/app'):
        data = json.dumps(body).encode() if body is not None else None
        return self.raw(path, data=data, method='POST' if body is not None else 'GET')

    def get(self, path):
        return self.raw(path, method='GET')

    def login(self, account_id):
        status, d = self.call(dict(action='login', id=account_id, password=PASSWORD))
        assert status == 200, (account_id, status, d)


def check(label, condition, detail=''):
    global checks
    assert condition, f'{label} {detail}'
    checks += 1
    print('PASS', label, flush=True)


def warn(label, detail):
    warnings.append(f'{label}: {detail}')
    print('WARN', label, '-', detail, flush=True)


try:
    anon = Client()
    controller = Client(); controller.login('CONTROL-01')
    head = Client(); head.login('HEAD-TRK')
    sig_head = Client(); sig_head.login('HEAD-SIG')
    _, head_data = head.call()
    worker_id = next(w['worker_id'] for w in head_data['workers']
                     if w['availability_status'] == 'Available')
    worker = Client(); worker.login(worker_id)
    _, sig_data = sig_head.call()
    sig_worker_id = next(w['worker_id'] for w in sig_data['workers']
                         if w['availability_status'] == 'Available')

    day = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=4)).date().isoformat()
    weekly = dict(zone='Z1', corridor='Churchgate-Virar', section='MH-SEC-02',
                  traffic_mode='reference', horizon=7, start_date=day, incidents=[])

    # --- GET /api/planner: authorization ---------------------------------
    check('Anon GET planner -> 403', anon.get('/api/planner')[0] == 403)
    check('Head GET planner -> 403', head.get('/api/planner')[0] == 403)
    check('Worker GET planner -> 403', worker.get('/api/planner')[0] == 403)

    # --- GET /api/planner: invalid inputs are 400, never 503 -------------
    check('Unknown zone -> 400', controller.get('/api/planner?zone=Z9')[0] == 400)
    check('Invalid start_date -> 400', controller.get('/api/planner?start_date=not-a-date')[0] == 400)
    check('Invalid horizon value -> 400', controller.get('/api/planner?horizon=abc')[0] == 400)
    check('Unsupported horizon 9 -> 400', controller.get('/api/planner?horizon=9')[0] == 400)
    check('Unknown corridor for zone -> 400',
          controller.get('/api/planner?zone=Z2&corridor=Churchgate-Virar')[0] == 400)
    check('Section outside zone -> 400',
          controller.get('/api/planner?zone=Z1&section=MH-SEC-04')[0] == 400)
    check('Wrong department on requests -> 400',
          controller.get('/api/planner?resource=requests&department=XXX')[0] == 400)
    check('Wrong department on assets -> 400',
          controller.get('/api/planner?resource=assets&department=XXX')[0] == 400)
    check('Valid department on requests -> 200',
          controller.get('/api/planner?resource=requests&department=TRK')[0] == 200)
    check('Unknown plan id -> 404', controller.get('/api/planner?id=PLAN-DOESNOTEXIST')[0] == 404)

    # --- POST /api/planner: authorization --------------------------------
    check('Anon POST generate -> 403',
          anon.call(dict(action='generate', config=weekly), '/api/planner')[0] == 403)
    check('Head POST generate -> 403',
          head.call(dict(action='generate', config=weekly), '/api/planner')[0] == 403)
    check('Worker POST accept -> 403',
          worker.call(dict(action='accept', plan_id='PLAN-X', revision=1, reason='x'), '/api/planner')[0] == 403)
    check('Cross-origin POST -> 403',
          controller.raw('/api/planner', data=json.dumps(dict(action='accept')).encode(),
                         origin='http://evil.example', method='POST')[0] == 403)

    # --- POST /api/planner: malformed bodies ------------------------------
    check('Non-JSON content type -> 415',
          controller.raw('/api/planner', data=b'{}', content_type='text/plain', method='POST')[0] == 415)
    check('Malformed JSON -> 400',
          controller.raw('/api/planner', data=b'{not json', method='POST')[0] == 400)
    check('JSON null body -> 400',
          controller.raw('/api/planner', data=b'null', method='POST')[0] == 400)
    check('JSON array body -> 400',
          controller.raw('/api/planner', data=b'[]', method='POST')[0] == 400)
    check('Missing action -> 400', controller.call({}, '/api/planner')[0] == 400)
    check('Unknown action -> 400', controller.call(dict(action='bogus'), '/api/planner')[0] == 400)

    # --- POST /api/planner: missing / invalid generate fields -------------
    check('Generate without config -> 400',
          controller.call(dict(action='generate'), '/api/planner')[0] == 400)
    check('Generate with empty config -> 400',
          controller.call(dict(action='generate', config={}), '/api/planner')[0] == 400)
    check('Generate with blank zone -> 400',
          controller.call(dict(action='generate', config=dict(weekly, zone='  ')), '/api/planner')[0] == 400)
    check('Generate with non-string zone -> 400',
          controller.call(dict(action='generate', config=dict(weekly, zone=123)), '/api/planner')[0] == 400)
    check('Generate with unknown zone -> 400',
          controller.call(dict(action='generate', config=dict(weekly, zone='Z9')), '/api/planner')[0] == 400)
    check('Generate with horizon 9 -> 400',
          controller.call(dict(action='generate', config=dict(weekly, horizon=9)), '/api/planner')[0] == 400)
    check('Generate with impossible date -> 400',
          controller.call(dict(action='generate', config=dict(weekly, start_date='2026-13-40')), '/api/planner')[0] == 400)
    check('Generate with batch_offset 100 -> 400',
          controller.call(dict(action='generate', config=dict(weekly, batch_offset=100)), '/api/planner')[0] == 400)

    # --- POST /api/planner: infer validation ------------------------------
    check('Infer without features -> 400',
          controller.call(dict(action='infer'), '/api/planner')[0] == 400)
    check('Infer with 2 features -> 400',
          controller.call(dict(action='infer', features=[1, 2]), '/api/planner')[0] == 400)
    check('Infer with text features -> 400',
          controller.call(dict(action='infer', features=['a'] * 9), '/api/planner')[0] == 400)
    check('Infer with out-of-range feature -> 400',
          controller.call(dict(action='infer', features=[10, 100, 5, 1.5, 0.5, 0.5, 2, 1.0, 1]), '/api/planner')[0] == 400)
    status, inferred = controller.call(
        dict(action='infer', features=[10, 100, 5, 0.5, 0.5, 0.5, 2, 1.0, 1]), '/api/planner')
    check('Infer with 9 valid features -> 200', status == 200 and set(inferred.get('risk', {})) == {'7', '30', '90'},
          str(inferred)[:200])

    # --- POST /api/planner: review actions on a real plan ------------------
    status, plan = controller.call(dict(action='generate', config=weekly), '/api/planner')
    assert status == 200, plan
    created_plans.append(plan['id'])
    block_id = plan['blocks'][0]['id'] if plan['blocks'] else None
    check('Accept unknown plan -> 404',
          controller.call(dict(action='accept', plan_id='PLAN-DOESNOTEXIST', revision=1, reason='x'), '/api/planner')[0] == 404)
    check('Accept without reason -> 400',
          controller.call(dict(action='accept', plan_id=plan['id'], revision=1), '/api/planner')[0] == 400)
    check('Accept without revision -> 409',
          controller.call(dict(action='accept', plan_id=plan['id'], reason='x'), '/api/planner')[0] == 409)
    if block_id:
        check('Modify without start -> 400',
              controller.call(dict(action='modify', plan_id=plan['id'], revision=1, block_id=block_id, reason='x'), '/api/planner')[0] == 400)
        check('Modify unknown block -> 404',
              controller.call(dict(action='modify', plan_id=plan['id'], revision=1, block_id='BLOCK-X', start=0, reason='x'), '/api/planner')[0] == 404)
        check('Reject unknown block -> 404',
              controller.call(dict(action='reject_block', plan_id=plan['id'], revision=1, block_id='BLOCK-X', reason='x'), '/api/planner')[0] == 404)
    status, accepted = controller.call(
        dict(action='accept', plan_id=plan['id'], revision=1, reason='HTTP edge review'), '/api/planner')
    check('Accept with exact revision -> 200', status == 200 and accepted['revision'] == 2, str(accepted)[:200])
    check('Stale accept after review -> 409',
          controller.call(dict(action='accept', plan_id=plan['id'], revision=1, reason='stale'), '/api/planner')[0] == 409)

    # --- /api/app: missing fields and wrong department ----------------------
    check('Anon POST /api/app -> 401', anon.call(dict(action='create'))[0] == 401)
    check('Login without password -> 400',
          Client().call(dict(action='login', id='CONTROL-01'))[0] == 400)
    check('Login with wrong password -> 401',
          Client().call(dict(action='login', id='CONTROL-01', password='wrong'))[0] == 401)
    check('Head create with missing fields -> 400',
          head.call(dict(action='create', title='QA edge'))[0] == 400)
    status, job = head.call(dict(action='create', title='QA edge ' + uuid.uuid4().hex,
                                 description='HTTP edge verification', station='TNA', location='QA',
                                 priority='Medium', required_crew=1,
                                 start_at=day + 'T08:00+05:30', end_at=day + 'T09:00+05:30'))
    assert status == 200, job
    created_jobs.append(job['id'])
    check('Assign worker from another department -> 403',
          head.call(dict(action='assign', job_id=job['id'], worker_ids=[sig_worker_id],
                         start_at=day + 'T08:00+05:30', end_at=day + 'T09:00+05:30'))[0] == 403)
    check('Malformed JSON to /api/app -> 400',
          controller.raw('/api/app', data=b'{not json', method='POST')[0] == 400)
    check('Empty body to /api/app -> 400',
          controller.raw('/api/app', data=b'', method='POST')[0] == 400)

    # --- Delay analysis + delayed replan over HTTP --------------------------
    delay_sid = f'REF-17412-{day}-MH-SEC-04'
    delay_base = dict(zone='Z2', corridor='all', section='MH-SEC-04', start_date=day,
                      horizon=7, traffic_mode='reference')
    delay_cfg = dict(delay_base, delays=[{'service_id': delay_sid, 'minutes': 30}])
    status, analysis = controller.call(dict(action='delay_analyze', config=delay_cfg,
                                            blocks=[]), '/api/planner')
    check('Delay analyze -> 200', status == 200, str(analysis)[:160])
    if status == 200:
        check('Delay analyze reports the manual-simulation source',
              analysis.get('source') == 'MANUAL_SIMULATION')
        trains = analysis.get('trains') or []
        check('Delay analyze returns the revised train timing',
              len(trains) == 1 and trains[0]['revised_start'] - trains[0]['original_start'] == 30
              and trains[0]['revised_end'] - trains[0]['original_end'] == 30, str(trains)[:160])
        day0_before = [g for g in analysis['gaps_before']['MH-SEC-04'] if g['day'] == 0]
        day0_after = [g for g in analysis['gaps_after']['MH-SEC-04'] if g['day'] == 0]
        check('Delay analyze recomputes day-0 gaps (88 -> 58 longest)',
              max(g['minutes'] for g in day0_before) == 88 and max(g['minutes'] for g in day0_after) == 58,
              f"before={[(g['start'], g['end']) for g in day0_before]} after={[(g['start'], g['end']) for g in day0_after]}")
        check('Delay analyze returns affected-block verdicts',
              isinstance(analysis.get('affected_blocks'), list) and analysis.get('blocks_checked') == 0)
    check('Delay analyze with unknown movement -> 400',
          controller.call(dict(action='delay_analyze',
                               config=dict(delay_base, delays=[{'service_id': 'REF-00000-1900-01-01-MH-SEC-04', 'minutes': 10}])),
                          '/api/planner')[0] == 400)
    status, dplan = controller.call(dict(action='generate', config=delay_cfg), '/api/planner')
    check('Delayed generate -> 200 revised draft', status == 200, str(dplan)[:160])
    if status == 200:
        created_plans.append(dplan['id'])
        check('Delayed plan keeps delays in config and a solver status',
              (dplan['config'].get('delays') or [{}])[0].get('minutes') == 30
              and dplan.get('solver_status') in ('OPTIMAL', 'FEASIBLE', 'ROLLING_FEASIBLE'),
              str(dplan.get('solver_status')))

    # --- Railway Digital Twin over HTTP ------------------------------------
    # Asset detail must come from the lifecycle DB, what-if predictions from
    # the saved models, and scenario persistence must round-trip. The twin
    # replan runs the real CP-SAT engine on the weekly scope when the picked
    # asset's task is inside the current batch; otherwise it degrades to a
    # warning (batch composition depends on scope), never a false failure.
    status, assets_page = controller.get('/api/planner?resource=assets&zone=Z2&section=MH-SEC-04&limit=5')
    check('Twin asset register -> 200', status == 200 and bool((assets_page or {}).get('assets')),
          str(assets_page)[:160])
    twin_asset_id = (assets_page.get('assets') or [{}])[0].get('id') if status == 200 else None
    if twin_asset_id:
        status, detail = controller.call(dict(action='twin_asset', asset_id=twin_asset_id), '/api/planner')
        check('Twin asset detail -> 200 with stored history',
              status == 200 and detail.get('inspection_count', 0) > 0
              and len(detail.get('features') or []) == 9 and detail.get('baseline'),
              str(detail)[:160])
        if status == 200:
            check('Twin baseline inference reproduces stored risks',
                  detail['baseline'].get('reproduces_stored') is True,
                  str(detail['baseline'].get('reproduces_stored')))
        check('Twin asset unknown id -> 400',
              controller.call(dict(action='twin_asset', asset_id='SYN-A-99999'), '/api/planner')[0] == 400)
        status, whatif_body = controller.call(dict(action='twin_infer', asset_id=twin_asset_id,
                                              overrides={'fault': True}), '/api/planner')
        whatif = whatif_body if status == 200 else None
        check('Twin what-if (fault) -> 200 recomputed predictions',
              whatif is not None and 0 <= whatif['after']['risk']['30'] <= 1
              and whatif['after']['priority'] == round(sum(whatif['after']['components'].values()), 1),
              str(whatif_body)[:160])
        check('Twin what-if unknown input -> 400',
              controller.call(dict(action='twin_infer', asset_id=twin_asset_id,
                                   overrides={'sensor_feed': 1}), '/api/planner')[0] == 400)
        status, saved_twin = controller.call(dict(action='twin_save', scenario=dict(
            name='Edge-suite twin scenario', asset_id=twin_asset_id, overrides={'fault': True},
            delays=[], input_fingerprint='edge-suite', model_sha256='edge-suite')), '/api/planner')
        check('Twin scenario save -> 200 with id', status == 200 and str(saved_twin.get('id', '')).startswith('TWIN-'),
              str(saved_twin)[:160])
        if status == 200:
            status, opened = controller.call(dict(action='twin_open', scenario_id=saved_twin['id']), '/api/planner')
            check('Twin scenario reopen round-trips inputs',
                  status == 200 and opened['scenario'].get('asset_id') == twin_asset_id
                  and opened['scenario'].get('overrides', {}).get('fault') is True,
                  str(opened)[:160])
            created_twins.append(saved_twin['id'])
        # Twin replan: needs the asset's task inside the current batch.
        status, dataset_page = controller.get('/api/planner?zone=Z2&corridor=all&section=all&start_date=' + day + '&horizon=7&traffic_mode=reference')
        batch_task = None
        if status == 200:
            batch_task = next((t for t in (dataset_page.get('dataset') or {}).get('tasks', [])
                               if t.get('asset_id') == twin_asset_id), None)
        if batch_task and whatif:
            change = dict(task_id=batch_task['id'], risk=whatif['after']['risk'],
                          predicted_duration=whatif['after']['predicted_duration'],
                          duration_buffer=whatif['after']['duration_buffer'])
            status, twin_plan = controller.call(dict(action='twin_replan', config=delay_base,
                                                     changes=[change]), '/api/planner')
            check('Twin replan -> 200 draft from CP-SAT', status == 200
                  and twin_plan.get('scenario_source') == 'DIGITAL_TWIN'
                  and twin_plan.get('solver_status') in ('OPTIMAL', 'FEASIBLE', 'ROLLING_FEASIBLE'),
                  str(twin_plan)[:160])
            if status == 200:
                created_plans.append(twin_plan['id'])
                check('Twin plan records the applied change',
                      (twin_plan.get('twin_changes') or [{}])[0].get('task_id') == batch_task['id'])
        else:
            print('  WARN  Twin replan (asset path) skipped: asset task not in the current batch (or what-if unavailable)')

    # Deterministic twin replan: the task comes from the batch itself
    # (first by id), so this check can never skip for batch composition.
    status, batch_page = controller.get('/api/planner?zone=Z2&corridor=all&section=all&start_date=' + day + '&horizon=7&traffic_mode=reference')
    batch_tasks = sorted(((batch_page or {}).get('dataset') or {}).get('tasks', []), key=lambda t: t['id'])
    check('Batch exposes eligible tasks for the twin replan', status == 200 and bool(batch_tasks), str(status))
    if batch_tasks:
        bt = batch_tasks[0]
        det_change = dict(task_id=bt['id'], overrides={'fault': True})
        det_config = dict(delay_base, section='all', solver_workers=1, solver_seed=26092, solver_time_limit=8)
        status, det_plan = controller.call(dict(action='twin_replan', config=det_config, changes=[det_change]), '/api/planner')
        check('Twin replan (deterministic, in-batch) -> 200 audited draft',
              status == 200 and det_plan.get('scenario_source') == 'DIGITAL_TWIN'
              and det_plan.get('validation', {}).get('passed') is True
              and det_plan.get('solver_status') in ('OPTIMAL', 'FEASIBLE', 'ROLLING_FEASIBLE'),
              str(det_plan)[:160])
        if status == 200:
            created_plans.append(det_plan['id'])
            check('Twin plan stores the solver configuration',
                  det_plan['config'].get('solver_workers') == 1 and det_plan['config'].get('solver_seed') == 26092)
            status, det_plan2 = controller.call(dict(action='twin_replan', config=det_config, changes=[det_change],
                                                     previous_plan_id=det_plan['id']), '/api/planner')
            check('Twin replan prefer-existing -> 200 with change report',
                  status == 200 and det_plan2.get('parent_id') == det_plan['id']
                  and isinstance(det_plan2.get('changes'), list) and bool(det_plan2['changes']),
                  str(det_plan2)[:160])
            if status == 200:
                created_plans.append(det_plan2['id'])
    # Solver configuration is validated server-side, never trusted raw.
    check('Solver workers out of range -> 400',
          controller.call(dict(action='generate', config=dict(delay_base, solver_workers=99)), '/api/planner')[0] == 400)
    check('Solver time limit out of range -> 400',
          controller.call(dict(action='generate', config=dict(delay_base, solver_time_limit=99)), '/api/planner')[0] == 400)
    # Delay propagation over HTTP: 17617 runs SEC-11 -> SEC-12 -> SEC-13 (Z3).
    prop_id = 'REF-17617-' + day + '-MH-SEC-11'
    prop_config = dict(zone='Z3', corridor='all', section='all', start_date=day, horizon=7, traffic_mode='reference')
    status, prop = controller.call(dict(action='delay_analyze', config=dict(prop_config, delays=[dict(service_id=prop_id, minutes=45, propagate=True)]), blocks=[]), '/api/planner')
    propagated = [t for t in (prop or {}).get('trains', []) if t.get('via') == 'propagated']
    check('Propagating delay -> downstream sections shifted',
          status == 200 and any(t.get('section') == 'MH-SEC-12' for t in propagated)
          and 'MH-SEC-12' in (prop or {}).get('sections', []), str(prop)[:200])
    status, solo = controller.call(dict(action='delay_analyze', config=dict(prop_config, delays=[dict(service_id=prop_id, minutes=45)]), blocks=[]), '/api/planner')
    check('Section-only delay -> exactly one movement shifts',
          status == 200 and len((solo or {}).get('trains', [])) == 1, str(solo)[:160])

    # --- Monthly generate + PLANNING_BUSY propagation ----------------------
    # No production test control is added or needed: a monthly generate
    # holds the planner's single lock for its whole solve (two 14-second
    # solver passes in the observed runs), so a second generate issued
    # while it runs exercises the real busy path. The rejected request
    # creates nothing. Timing guard: if the monthly solve is no longer
    # running when the probe fires, the probe degrades to a warning.
    monthly = dict(weekly, horizon=30)
    monthly_result = {}

    def run_monthly():
        monthly_result['value'] = controller.call(dict(action='generate', config=monthly), '/api/planner')

    probe = Client(); probe.login('CONTROL-01')
    thread = threading.Thread(target=run_monthly)
    thread.start()
    time.sleep(5)
    if thread.is_alive():
        busy_status, busy_body = probe.call(dict(action='generate', config=weekly), '/api/planner')
        if busy_status == 200:  # the probe won the lock race; clean up its plan
            created_plans.append(busy_body['id'])
            warn('PLANNING_BUSY probe', 'probe acquired the lock before the monthly run; '
                                         'busy path not exercised this run')
        else:
            check('Concurrent generate while planning -> 409',
                  busy_status == 409 and 'already in progress' in str(busy_body.get('error', '')),
                  f'{busy_status} {str(busy_body)[:160]}')
    else:
        warn('PLANNING_BUSY probe', 'monthly generate finished before the probe fired; '
                                    'busy path not exercised this run')
    thread.join()
    status, mplan = monthly_result['value']
    assert status == 200, mplan
    created_plans.append(mplan['id'])
    solver_status = mplan.get('solver_status')
    check('Monthly generate -> feasible status',
          solver_status in ('OPTIMAL', 'FEASIBLE', 'ROLLING_FEASIBLE'), str(solver_status))
    if solver_status == 'ROLLING_FEASIBLE':
        stages = mplan.get('diagnostics', {}).get('stages', [])
        check('ROLLING_FEASIBLE over HTTP carries audited stages',
              bool(stages) and all(s['status'] in ('OPTIMAL', 'FEASIBLE') for s in stages))
        print('ROLLING: the rolling fallback fired through the real Controller HTTP API.')
    else:
        print(f'ROLLING: monthly HTTP generate returned {solver_status}; the rolling fallback '
              'did not fire because the global solve established a schedule. It cannot be '
              'forced through the API - no request field selects the solver path.')

    print(f'\n{checks} HTTP edge checks passed, {len(warnings)} warning(s).', flush=True)
finally:
    for path in (ROOT / '.wrangler/state/v3/d1/miniflare-D1DatabaseObject').glob('*.sqlite'):
        con = sqlite3.connect(path)
        if con.execute("SELECT name FROM sqlite_master WHERE name='planner_plans'").fetchone():
            for pid in created_plans:
                con.execute('DELETE FROM planner_feedback WHERE plan_id=?', (pid,))
                con.execute('DELETE FROM planner_plans WHERE id=?', (pid,))
            for jid in created_jobs:
                con.execute('DELETE FROM assignments WHERE job_id=?', (jid,))
                con.execute('DELETE FROM jobs WHERE id=?', (jid,))
            if con.execute("SELECT name FROM sqlite_master WHERE name='twin_scenarios'").fetchone():
                for tid in created_twins:
                    con.execute('DELETE FROM twin_scenarios WHERE id=?', (tid,))
            con.commit()
        con.close()
    print('Removed this run\u2019s test jobs and plans; existing user records preserved.', flush=True)

# Notes on ROLLING_FEASIBLE, from tracing engine.generate (planner/engine.py):
# the rolling fallback runs only when the global monthly solve returns
# UNKNOWN, the horizon is 30 and no blocks are frozen. planner/server.py
# /generate calls that same engine.generate, so the HTTP path and the
# in-process tests in planner/test_rolling.py execute identical code;
# the difference is only that a test can patch solve() to return UNKNOWN
# and an HTTP client cannot. A monthly Maharashtra generate normally
# returns FEASIBLE at its time limit, so expect the observational branch
# above to report FEASIBLE in most runs.
#
# Notes on PLANNING_BUSY: planner/server.py holds one lock around
# /generate and answers a concurrent request with 409 PLANNING_BUSY.
# app/api/planner/route.ts preserves that engine status on POST (its
# GET-style EngineRequestError handling), so the client sees 409 with
# the engine's "already in progress" message; the engine's `code`
# field itself is not forwarded by the route. Verification needs no
# test-only control: real concurrency (a monthly generate in flight)
# is the trigger, and the rejected request has no side effects. The
# one limitation is timing - the probe must fire while the first solve
# still holds the lock - which is why the suite guards it with
# thread.is_alive() and a lock-race fallback warning instead of
# asserting blindly.
