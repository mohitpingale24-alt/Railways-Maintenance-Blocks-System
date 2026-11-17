"""Railway Digital Twin tests.

Layer tests (override rules, plan-change composition, fingerprints) run
anywhere. Database tests run against the real synthetic lifecycle store
and saved models when the artifacts exist, and skip cleanly otherwise -
the same convention as the delay engine tests.
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import twin

BASE_FEATURES = [12.0, 1.0, 30.0, 0.5, 0.4, 0.3, 150.0, 1.1, 0.0]


class TestTwinLayer(unittest.TestCase):
    def test_parse_asset_id(self):
        self.assertEqual(twin.parse_asset_id('SYN-A-00123'), 123)
        self.assertEqual(twin.parse_asset_id('syn-a-7'), 7)
        with self.assertRaises(ValueError):
            twin.parse_asset_id('locomotive-9')

    def test_elapsed_days_updates_overdue_by_dataset_identity(self):
        new, changes = twin.apply_overrides(BASE_FEATURES, {'elapsed_days': 50})
        self.assertEqual(new[6], 200.0)
        self.assertEqual(new[2], 80.0)  # max(0, 200 - 120)
        self.assertTrue(any(c['feature'] == 'overdue_days' for c in changes))
        self.assertEqual(BASE_FEATURES[6], 150.0)  # input never mutated

    def test_deterioration_delta_clamps_to_unit_interval(self):
        new, _ = twin.apply_overrides(BASE_FEATURES, {'deterioration_delta': 0.9})
        self.assertEqual(new[4], 1.0)

    def test_fault_preset(self):
        new, changes = twin.apply_overrides(BASE_FEATURES, {'fault': True})
        self.assertAlmostEqual(new[4], 0.75)
        self.assertAlmostEqual(new[7], 2.6)
        self.assertEqual(new[1], 2.0)
        self.assertTrue(all(c['kind'] == 'DETERMINISTIC_RULE' for c in changes))

    def test_maintenance_completed_rule(self):
        new, _ = twin.apply_overrides(BASE_FEATURES, {'maintenance_completed': True})
        self.assertEqual(new[6], 0.0)
        self.assertEqual(new[2], 0.0)
        self.assertAlmostEqual(new[4], 0.4 * 0.35)
        self.assertAlmostEqual(new[7], 1.1 * 0.5)

    def test_unknown_and_out_of_range_inputs_rejected(self):
        with self.assertRaises(ValueError):
            twin.apply_overrides(BASE_FEATURES, {'sensor_feed': 1})
        with self.assertRaises(ValueError):
            twin.apply_overrides(BASE_FEATURES, {'deterioration_delta': 5})
        with self.assertRaises(ValueError):
            twin.apply_overrides(BASE_FEATURES, {'elapsed_days': -3})

    def _data(self):
        return {'version': 'mh-test', 'tasks': [
            dict(id='T1', asset_id='SYN-A-00001', risk={'7': .1, '30': .2, '90': .3},
                 predicted_duration=60.0, duration_buffer=10.0, duration=70, priority=40.0),
            dict(id='T2', asset_id='SYN-A-00002', risk={'7': .1, '30': .2, '90': .3},
                 predicted_duration=50.0, duration_buffer=5.0, duration=55, priority=30.0)]}

    def test_apply_plan_changes_rejects_client_predictions(self):
        # The twin contract: clients supply scenario INPUT overrides; the
        # server recomputes predictions. Client-provided predictions,
        # missing overrides, duplicates and empty change sets are refused
        # during validation, before any model is touched.
        with self.assertRaises(ValueError):
            twin.apply_plan_changes(self._data(), [
                dict(task_id='T1', risk={'7': .2, '30': .5, '90': .6},
                     predicted_duration=80.0, duration_buffer=12.5)])
        with self.assertRaises(ValueError):
            twin.apply_plan_changes(self._data(), [dict(task_id='T1')])
        with self.assertRaises(ValueError):
            twin.apply_plan_changes(self._data(), [
                dict(task_id='T1', overrides={'fault': True}),
                dict(task_id='T1', overrides={'fault': True})])
        with self.assertRaises(ValueError):
            twin.apply_plan_changes(self._data(), [
                dict(task_id='NOPE', overrides={'fault': True})])
        with self.assertRaises(ValueError):
            twin.apply_plan_changes(self._data(), [])

    def test_scenario_fingerprints(self):
        changes = [dict(task_id='T1', risk={'7': .1, '30': .2, '90': .3})]
        self.assertEqual(twin.changes_fingerprint('fp', changes),
                         twin.changes_fingerprint('fp', copy.deepcopy(changes)))
        self.assertNotEqual(twin.changes_fingerprint('fp', changes),
                            twin.changes_fingerprint('other', changes))
        self.assertTrue(twin.scenario_stale('a', 'b'))
        self.assertFalse(twin.scenario_stale('a', 'a'))
        self.assertFalse(twin.scenario_stale(None, 'b'))


try:
    from maharashtra.pipeline import ARTIFACTS as _ART
    _DB_AVAILABLE = (_ART / 'simulation.sqlite').exists() and (_ART / 'models.joblib').exists()
except Exception:
    _DB_AVAILABLE = False


@unittest.skipUnless(_DB_AVAILABLE, 'synthetic lifecycle DB / model artifacts not present here')
class TestTwinDatabase(unittest.TestCase):
    def test_asset_detail_loads_stored_history(self):
        detail = twin.asset_detail('SYN-A-00000')
        self.assertEqual(detail['asset_id'], 'SYN-A-00000')
        self.assertGreater(detail['inspection_count'], 0)
        self.assertGreater(len(detail['requests']), 0)
        self.assertEqual(len(detail['features']), 9)
        base = detail['baseline']
        self.assertIsNotNone(base)
        obs_by_id = {o['observation_id']: o for o in detail['observations']}
        self.assertIn(base['observation_id'], obs_by_id)
        self.assertAlmostEqual(detail['features'][4], obs_by_id[base['observation_id']]['deterioration'])

    def test_baseline_inference_reproduces_stored_risks(self):
        detail = twin.asset_detail('SYN-A-00000')
        self.assertTrue(detail['baseline']['reproduces_stored'],
                        'production inference must reproduce the stored baseline risks')

    def test_whatif_recomputes_predictions_and_criticality(self):
        result = twin.whatif('SYN-A-00000', {'deterioration_delta': 0.2})
        self.assertAlmostEqual(result['features_after'][4],
                               min(1.0, result['features_before'][4] + 0.2))
        for horizon in ('7', '30', '90'):
            self.assertTrue(0 <= result['after']['risk'][horizon] <= 1)
        self.assertAlmostEqual(sum(result['after']['components'].values()),
                               result['after']['priority'], places=0)
        self.assertTrue(result['model_sha256'])

    def test_fault_scores_at_least_repaired(self):
        fault = twin.whatif('SYN-A-00000', {'fault': True})
        repaired = twin.whatif('SYN-A-00000', {'maintenance_completed': True})
        self.assertEqual(repaired['features_after'][6], 0.0)
        self.assertGreaterEqual(fault['after']['risk']['30'] + 1e-9,
                                repaired['after']['risk']['30'])

    def test_whatif_leaves_stored_records_unchanged(self):
        before = twin.asset_detail('SYN-A-00000')['baseline']['risk']
        twin.whatif('SYN-A-00000', {'fault': True})
        after = twin.asset_detail('SYN-A-00000')['baseline']['risk']
        self.assertEqual(before, after)

    def test_apply_plan_changes_recomputes_on_a_copy(self):
        from maharashtra import store as mh_store
        data = mh_store.dataset({'zone': 'Z2', 'corridor': 'all', 'section': 'all',
                                 'start_date': '2026-11-18', 'horizon': 7,
                                 'traffic_mode': 'reference', 'batch_offset': 0,
                                 'resource_overrides': {}})
        task = next(t for t in data['tasks'] if t.get('observation_features'))
        before_risk = dict(task['risk'])
        changed, applied = twin.apply_plan_changes(
            data, [{'task_id': task['id'], 'overrides': {'fault': True}}])
        self.assertEqual(applied[0]['before']['risk'], before_risk)
        self.assertNotEqual(applied[0]['after']['risk'], before_risk)
        new_task = next(t for t in changed['tasks'] if t['id'] == task['id'])
        self.assertTrue(new_task['twin_modified'])
        self.assertEqual(new_task['risk'], applied[0]['after']['risk'])
        original = next(t for t in data['tasks'] if t['id'] == task['id'])
        self.assertEqual(original['risk'], before_risk)  # input never mutated

    def test_all_zones_and_departments_load(self):
        from maharashtra.pipeline import connect
        with connect() as db:
            rows = db.execute('SELECT zone, MIN(id) AS id FROM assets GROUP BY zone').fetchall()
            departments = {r[0] for r in db.execute('SELECT DISTINCT department FROM assets')}
        self.assertEqual(len(rows), 4)
        self.assertEqual(departments, {'TRK', 'SIG', 'TRC'})
        for row in rows:
            detail = twin.asset_detail(f'SYN-A-{row["id"]:05}')
            self.assertEqual(detail['zone'], row['zone'])
            self.assertIn(detail['department'], ('TRK', 'SIG', 'TRC'))


if __name__ == '__main__':
    unittest.main()


def _ortools_available():
    try:
        import ortools.sat.python.cp_model  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(_DB_AVAILABLE and _ortools_available(),
                     'needs the lifecycle artifacts and real OR-Tools (Mac)')
class TestTwinReplanIntegration(unittest.TestCase):
    """Deterministic end-to-end twin replan: the task is chosen from the
    selected batch itself (sorted by id), so the test can never skip for
    being outside the batch. One worker + fixed seed make the solve
    reproducible."""

    @classmethod
    def setUpClass(cls):
        from maharashtra import store as mh_store
        import engine
        cls.store = mh_store
        cls.engine = engine
        cls.config = {'zone': 'Z2', 'corridor': 'all', 'section': 'all',
                      'start_date': '2026-11-18', 'horizon': 7,
                      'traffic_mode': 'reference', 'batch_offset': 0,
                      'solver_time_limit': 8, 'solver_workers': 1,
                      'solver_seed': 26092}
        cls.data = mh_store.dataset({**{k: cls.config[k] for k in
                                     ('zone', 'corridor', 'section', 'start_date',
                                      'horizon', 'traffic_mode', 'batch_offset')},
                                     'resource_overrides': {}})
        eligible = sorted((t for t in cls.data['tasks']
                           if t.get('safety_action') != 'STOP_AND_REVIEW'),
                          key=lambda t: t['id'])
        assert eligible, 'batch must contain eligible tasks'
        cls.task = eligible[0]
        cls.change = {'task_id': cls.task['id'], 'overrides': {'fault': True}}

    def test_changed_values_reach_cpsat_and_plan_is_audited(self):
        changed, applied = twin.apply_plan_changes(self.data, [self.change])
        self.assertEqual(applied[0]['task_id'], self.task['id'])
        self.assertNotEqual(applied[0]['after'], applied[0]['before'])
        original = next(t for t in self.data['tasks'] if t['id'] == self.task['id'])
        self.assertEqual(original['risk'], self.task['risk'])  # original dataset untouched
        plan = self.engine.generate(changed, self.config)
        self.assertIn(plan['solver_status'], ('OPTIMAL', 'FEASIBLE', 'ROLLING_FEASIBLE'))
        self.assertEqual(self.engine.validate(changed, plan), [])
        self.assertTrue(plan['validation']['passed'])
        # The solver configuration is stored with the plan and its
        # diagnostics report the configured limit honestly.
        self.assertEqual(plan['config']['solver_workers'], 1)
        self.assertEqual(plan['config']['solver_seed'], 26092)
        self.assertEqual(plan['diagnostics']['time_limit_seconds'], 8.0)
        if plan['solver_status'] == 'FEASIBLE':
            self.assertTrue(any('optimality is not claimed' in w for w in plan['warnings']))

    def test_same_seed_and_settings_reproduce_the_schedule(self):
        changed, _ = twin.apply_plan_changes(self.data, [self.change])
        first = self.engine.generate(changed, self.config)
        second = self.engine.generate(changed, self.config)
        signature = lambda p: [(b['start'], tuple(b['tasks'])) for b in p['blocks']]
        self.assertEqual(signature(first), signature(second))

    def test_prefer_existing_reports_changes_against_previous(self):
        baseline = self.engine.generate(self.data, self.config)
        # Change a task the baseline left unscheduled: every baseline block
        # then belongs to an unchanged task, so the frozen retention solve
        # stays feasible by construction and the change report is the
        # behaviour under test (not solver luck).
        scheduled = {tid for b in baseline['blocks'] for tid in b['tasks']}
        eligible = sorted((t for t in self.data['tasks']
                           if t.get('safety_action') != 'STOP_AND_REVIEW'),
                          key=lambda t: t['id'])
        target = next(t for t in eligible if t['id'] not in scheduled)
        changed, applied = twin.apply_plan_changes(
            self.data, [{'task_id': target['id'], 'overrides': {'fault': True}}])
        self.assertNotEqual(applied[0]['after'], applied[0]['before'])
        revised = self.engine.generate(changed, self.config, previous=baseline)
        self.assertEqual(self.engine.validate(changed, revised), [])
        kinds = {c['change'] for c in revised['changes']}
        self.assertTrue(kinds <= {'Retained', 'Moved', 'Newly scheduled', 'Deferred'})
        self.assertTrue(revised['changes'])
