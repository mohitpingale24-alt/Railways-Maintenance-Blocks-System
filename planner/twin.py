"""Railway Digital Twin layer for the Maharashtra RailBlock planner.

The twin is a SCENARIO layer over the existing synthetic lifecycle store:

- Asset detail is read live from the simulation database (assets,
  observations, requests, completions) - nothing is copied or cached here.
- What-if inputs transform the asset's CURRENT nine observation features
  with documented deterministic rules (below), then the SAVED trained
  models recompute failure risk, duration and the six-factor Criticality
  Index through the exact production inference path (maharashtra.store.
  infer, including engineered history features rebuilt from the asset's
  own stored inspections). ML predictions and deterministic simulation
  rules are always labelled separately.
- A twin replan applies recomputed risk/duration values to an in-memory
  copy of the dataset and hands it to the unchanged CP-SAT engine. Stored
  asset records and approved plans are never modified by this module.

Deterministic lifecycle input rules (scenario assumptions, NOT trained
models - no post-repair recovery model exists in this project):
- elapsed_days: last_maintenance_days += days, and overdue_days follows
  the dataset identity overdue_days = max(0, last_maintenance_days - 120).
- fault preset: deterioration +0.35 (capped at 1), inspection_measurement
  +1.5, previous_defects +1.
- maintenance_completed: last_maintenance_days = 0, overdue_days = 0,
  deterioration x 0.35, inspection_measurement x 0.5. The transformed
  features are then scored by the trained models; the 0.35/0.5 factors
  are documented synthetic assumptions about the post-work inspection
  state, not learned recovery behaviour.
"""
import copy
import hashlib
import json
import math
from datetime import datetime, timezone

FEATURE_NAMES = ['age_years', 'previous_defects', 'overdue_days',
                 'traffic_load', 'deterioration', 'weather_stress',
                 'last_maintenance_days', 'inspection_measurement',
                 'department_index']
DEPARTMENT_TYPES = {'TRK': 'Track asset', 'SIG': 'Signalling asset',
                    'TRC': 'OHE/electrical asset'}
OVERDUE_THRESHOLD_DAYS = 120
FAULT_DETERIORATION_DELTA = 0.35
FAULT_MEASUREMENT_DELTA = 1.5
REPAIR_DETERIORATION_FACTOR = 0.35
REPAIR_MEASUREMENT_FACTOR = 0.5


def parse_asset_id(value):
    text = str(value or '').strip().upper()
    if text.startswith('SYN-A-'):
        text = text[6:]
    if not text.isdigit():
        raise ValueError('Asset id must look like SYN-A-00123.')
    return int(text)


def _latest_request(db, asset_index):
    return db.execute(
        'SELECT * FROM requests WHERE asset_id=? ORDER BY id DESC LIMIT 1',
        (asset_index,)).fetchone()


def asset_detail(asset_id):
    """Full digital-twin record for one asset, read from the lifecycle DB."""
    from maharashtra.pipeline import connect
    from maharashtra.store import infer, network
    index = parse_asset_id(asset_id)
    with connect() as db:
        asset = db.execute('SELECT * FROM assets WHERE id=?', (index,)).fetchone()
        if asset is None:
            raise ValueError(f'Unknown asset SYN-A-{index:05}.')
        total = int(db.execute('SELECT COUNT(*) FROM assets').fetchone()[0])
        max_obs = int(db.execute('SELECT COALESCE(MAX(id),-1) FROM observations').fetchone()[0])
        observations = []
        for obs_id in range(index, max_obs + 1, total):
            row = db.execute('SELECT id,day,features,y7,y30,y90 FROM observations WHERE id=?',
                             (obs_id,)).fetchone()
            if row is not None:
                f = json.loads(row['features'])
                observations.append(dict(
                    observation_id=row['id'], day=row['day'],
                    deterioration=f[4], inspection_measurement=f[7],
                    overdue_days=f[2], last_maintenance_days=f[6],
                    failure_within_7d=bool(row['y7']), failure_within_30d=bool(row['y30']),
                    failure_within_90d=bool(row['y90']), features=f))
        requests = [dict(id=f'SYN-MH-{r["id"]:07}', observation_id=r['observation_id'],
                          status=r['status'], risk={'7': r['p7'], '30': r['p30'], '90': r['p90']},
                          predicted_duration=r['duration'], duration_buffer=r['buffer'],
                          priority=r['priority'],
                          components=json.loads(r['components']) if r['components'] else {})
                    for r in db.execute('SELECT * FROM requests WHERE asset_id=? ORDER BY id', (index,))]
        completions = [dict(day=r['day'], duration=r['duration'], provenance=r['provenance'])
                       for r in db.execute('SELECT * FROM completions WHERE asset_id=? ORDER BY day', (index,))]
        latest = _latest_request(db, index)
        latest_obs = db.execute('SELECT id,day,features FROM observations WHERE id=?',
                                (latest['observation_id'],)).fetchone() if latest else None
    graph = network()
    section = next((s for s in graph['sections'] if s['id'] == asset['section']), None)
    features = json.loads(latest_obs['features']) if latest_obs else None
    baseline = None
    if latest is not None and features is not None:
        baseline = dict(
            risk={'7': latest['p7'], '30': latest['p30'], '90': latest['p90']},
            predicted_duration=latest['duration'], duration_buffer=latest['buffer'],
            priority=latest['priority'],
            components=json.loads(latest['components']) if latest['components'] else {},
            status=latest['status'], request_id=f'SYN-MH-{latest["id"]:07}',
            observation_id=latest['observation_id'], observation_day=latest_obs['day'])
        # Reproducibility audit: production inference on the stored features
        # must reproduce the stored baseline before any what-if is trusted.
        try:
            check = infer(features, index, latest['observation_id'])
            baseline['recomputed'] = check
            baseline['reproduces_stored'] = (all(
                abs(check['risk'][k] - baseline['risk'][k]) < 1e-6 for k in ('7', '30', '90'))
                and math.ceil(check['predicted_duration']) == baseline['predicted_duration'])
        except Exception as error:  # artifacts unavailable: report, never fake
            baseline['recomputed'] = None
            baseline['reproduces_stored'] = None
            baseline['recompute_error'] = str(error)
    km = None
    if section is not None:
        km = round(asset['fraction'] * (section['km_end'] - .5), 2)
    return dict(
        asset_id=f'SYN-A-{index:05}', asset_index=index,
        asset_type=DEPARTMENT_TYPES.get(asset['department'], asset['department']),
        department=asset['department'], section=asset['section'], zone=asset['zone'],
        section_name=section['name'] if section else asset['section'],
        km=km, latitude=asset['latitude'], longitude=asset['longitude'],
        position_provenance='SYNTHETIC endpoint interpolation (approximate, not surveyed)',
        features=features, feature_names=FEATURE_NAMES,
        baseline=baseline, observations=observations,
        inspection_count=len(observations), requests=requests,
        completions=completions,
        provenance='Live read from the synthetic lifecycle database; no records were modified.')


def apply_overrides(features, overrides):
    """Apply documented deterministic what-if rules to a feature vector.

    Returns (new_features, changes) where each change names the rule that
    produced it. These are scenario INPUT transformations; the failure
    risks themselves always come from the trained models afterwards.
    """
    if not isinstance(overrides, dict):
        raise ValueError('What-if inputs must be an object.')
    for key in ('fault', 'maintenance_completed'):
        if key in overrides and type(overrides[key]) is not bool:
            raise ValueError(f'{key} must be a boolean.')
    allowed = {'deterioration_delta', 'measurement_delta', 'elapsed_days',
               'fault', 'maintenance_completed'}
    unknown = set(overrides) - allowed
    if unknown:
        raise ValueError(f'Unknown what-if inputs: {", ".join(sorted(unknown))}.')
    new = [float(v) for v in features]
    if len(new) != 9:
        raise ValueError('Feature vector must have nine values.')
    changes = []

    def set_feature(idx, value, rule):
        if new[idx] != value:
            changes.append(dict(feature=FEATURE_NAMES[idx], before=new[idx],
                                after=value, rule=rule, kind='DETERMINISTIC_RULE'))
            new[idx] = value

    def number(key, low, high):
        value = overrides.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{key} must be a finite number.')
        if not low <= value <= high:
            raise ValueError(f'{key} must be between {low} and {high}.')
        return float(value)

    elapsed = number('elapsed_days', 0, 3650)
    if elapsed:
        set_feature(6, new[6] + elapsed, f'+{elapsed:g} elapsed days since maintenance (scenario input)')
        set_feature(2, max(0.0, new[6] - OVERDUE_THRESHOLD_DAYS),
                    'overdue_days = max(0, last_maintenance_days - 120) dataset identity')
    det_delta = number('deterioration_delta', -1, 1)
    if det_delta:
        set_feature(4, min(1.0, max(0.0, new[4] + det_delta)),
                    f'deterioration {det_delta:+g} (scenario input, clamped to 0-1)')
    meas_delta = number('measurement_delta', -20, 20)
    if meas_delta:
        set_feature(7, max(0.0, new[7] + meas_delta),
                    f'inspection measurement {meas_delta:+g} (scenario input)')
    if overrides.get('fault'):
        if overrides.get('fault') is not True:
            raise ValueError('fault must be true when provided.')
        set_feature(4, min(1.0, new[4] + FAULT_DETERIORATION_DELTA),
                    'simulated fault preset: deterioration +0.35')
        set_feature(7, new[7] + FAULT_MEASUREMENT_DELTA,
                    'simulated fault preset: inspection measurement +1.5')
        set_feature(1, new[1] + 1, 'simulated fault preset: previous defects +1')
    if overrides.get('maintenance_completed'):
        if overrides.get('maintenance_completed') is not True:
            raise ValueError('maintenance_completed must be true when provided.')
        set_feature(6, 0.0, 'simulated maintenance completed: days since maintenance reset to 0')
        set_feature(2, 0.0, 'simulated maintenance completed: overdue days cleared')
        set_feature(4, new[4] * REPAIR_DETERIORATION_FACTOR,
                    'documented synthetic lifecycle assumption: deterioration x 0.35 after completed work (not a trained recovery model)')
        set_feature(7, new[7] * REPAIR_MEASUREMENT_FACTOR,
                    'documented synthetic lifecycle assumption: inspection measurement x 0.5 after completed work (not a trained recovery model)')
    return new, changes


def whatif(asset_id, overrides):
    """Recompute one asset's ML predictions under a what-if scenario.

    Only this asset is re-inferred: its engineered history features are
    rebuilt from its own stored inspections with the transformed current
    row, through the production inference path. Stored records unchanged.
    """
    from maharashtra.pipeline import connect
    from maharashtra.store import infer
    index = parse_asset_id(asset_id)
    with connect() as db:
        latest = _latest_request(db, index)
        if latest is None:
            raise ValueError(f'Unknown asset SYN-A-{index:05}.')
        obs = db.execute('SELECT features FROM observations WHERE id=?',
                         (latest['observation_id'],)).fetchone()
    features = json.loads(obs['features'])
    new_features, changes = apply_overrides(features, overrides)
    result = infer(new_features, index, latest['observation_id'])
    before = dict(risk={'7': latest['p7'], '30': latest['p30'], '90': latest['p90']},
                  predicted_duration=latest['duration'], duration_buffer=latest['buffer'],
                  priority=latest['priority'],
                  components=json.loads(latest['components']) if latest['components'] else {})
    return dict(
        asset_id=f'SYN-A-{index:05}', request_id=f'SYN-MH-{latest["id"]:07}',
        observation_id=latest['observation_id'],
        before=before, after=result,
        features_before=features, features_after=new_features,
        feature_names=FEATURE_NAMES, input_changes=changes,
        model_sha256=result['model_sha256'],
        computed_at=datetime.now(timezone.utc).isoformat(),
        method=dict(
            ml='Failure risks, predicted duration and Criticality Index recomputed by the saved trained models on the transformed features (production inference path, engineered history from this asset\'s stored inspections).',
            rules='Feature transformations are deterministic documented scenario rules (see input_changes); no trained post-repair recovery model exists or is claimed.',
            isolation='Scenario state only - stored asset records, requests and approved plans are unchanged.'))


def apply_plan_changes(data, changes):
    """Apply twin-recomputed risk/duration values to a dataset COPY.

    Returns (new_data, applied). The input dataset is never mutated, so a
    twin replan cannot leak scenario values into baseline planning.
    """
    if not isinstance(changes, list) or not changes:
        raise ValueError('Provide at least one twin change for the replan.')
    if len(changes) > 50:
        raise ValueError('At most 50 twin changes per replan.')
    from maharashtra.store import update_task, WEIGHTS
    by_id = {}
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get('task_id'), str):
            raise ValueError('Each twin change needs a task_id.')
        if set(change) - {'task_id', 'overrides'}:
            raise ValueError('Supply scenario input overrides, never client-provided predictions.')
        if not isinstance(change.get('overrides'), dict):
            raise ValueError('Each twin change needs an overrides object.')
        if change['task_id'] in by_id:
            raise ValueError('Duplicate twin task change.')
        by_id[change['task_id']] = change['overrides']
    unknown = set(by_id) - {t['id'] for t in data['tasks']}
    if unknown:
        raise ValueError('Twin changes reference tasks outside the current planning batch: '
                         + ', '.join(sorted(unknown)))
    new_data = copy.deepcopy(data)
    applied = []
    for task in new_data['tasks']:
        if task['id'] not in by_id:
            continue
        overrides = by_id[task['id']]
        features, rules = apply_overrides(task['observation_features'], overrides)
        before = dict(risk=dict(task['risk']), predicted_duration=task['predicted_duration'],
                      duration=task['duration'], priority=task['priority'])
        held = task.get('safety_action') == 'STOP_AND_REVIEW'
        update_task(task, {'features': features}, data.get('selection', {}).get('priority_weights', WEIGHTS))
        # A hypothetical repair is not evidence of a released safety hold.
        if held:
            task['safety_action'] = 'STOP_AND_REVIEW'
        task['twin_modified'] = True
        applied.append(dict(task_id=task['id'], asset_id=task['asset_id'], overrides=overrides,
                            input_changes=rules, before=before,
                            after=dict(risk=task['risk'], predicted_duration=task['predicted_duration'],
                                       duration=task['duration'], priority=task['priority'])))
    new_data['tasks'].sort(key=lambda t: (-t['priority'], t['id']))
    return new_data, applied


def changes_fingerprint(dataset_fingerprint, changes):
    payload = json.dumps(changes or [], sort_keys=True)
    return hashlib.sha256(((dataset_fingerprint or '') + payload).encode()).hexdigest()


def scenario_stale(saved_fingerprint, current_fingerprint):
    """A saved scenario reproduces only while its source fingerprint holds."""
    return bool(saved_fingerprint) and bool(current_fingerprint) and saved_fingerprint != current_fingerprint
