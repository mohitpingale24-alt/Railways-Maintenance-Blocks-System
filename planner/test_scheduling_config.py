"""Solver-configuration tests for the unified dynamic scheduling workflow.

normalized_config is the single server-side validation point for the
bounded CP-SAT settings (time limit, workers, seed, strategy,
disruptions toggle). These tests need no solver: an ortools stub stands
in at import time, exactly like the planning-period replica checks. The
real-solver behaviour of the same settings is covered by
test_twin.TestTwinReplanIntegration on machines with OR-Tools.
"""
import sys
import types
import unittest

if 'ortools' not in sys.modules:
    _ortools = types.ModuleType('ortools')
    _sat = types.ModuleType('ortools.sat')
    _python = types.ModuleType('ortools.sat.python')
    _cp_model = types.ModuleType('ortools.sat.python.cp_model')
    _cp_model.CpModel = object
    _cp_model.CpSolver = object
    _cp_model.OPTIMAL = 4
    _cp_model.FEASIBLE = 3
    _cp_model.INFEASIBLE = 2
    _cp_model.UNKNOWN = 0
    _python.cp_model = _cp_model
    _sat.python = _python
    _ortools.sat = _sat
    sys.modules.update({'ortools': _ortools, 'ortools.sat': _sat,
                        'ortools.sat.python': _python,
                        'ortools.sat.python.cp_model': _cp_model})

import engine

MH_DATA = {'version': 'mh-config-test', 'movements': [], 'sections': [], 'tasks': []}
BASE = {'start_date': '2026-11-18', 'horizon': 7, 'zone': 'Z2'}


def normalize(**overrides):
    return engine.normalized_config({**BASE, **overrides}, MH_DATA)


class TestSolverConfiguration(unittest.TestCase):
    def test_defaults_match_the_historical_engine(self):
        config = normalize()
        self.assertIsNone(config['solver_time_limit'])   # engine default 8s/14s by horizon
        self.assertEqual(config['solver_workers'], 4)
        self.assertEqual(config['solver_seed'], 26092)
        self.assertEqual(config['strategy'], 'prefer_existing')
        self.assertTrue(config['disruptions_enabled'])

    def test_valid_settings_pass_through(self):
        config = normalize(solver_time_limit=15, solver_workers=1, solver_seed=7,
                           strategy='full_reoptimize')
        self.assertEqual(config['solver_time_limit'], 15)
        self.assertEqual(config['solver_workers'], 1)
        self.assertEqual(config['solver_seed'], 7)
        self.assertEqual(config['strategy'], 'full_reoptimize')

    def test_time_limit_bounds(self):
        for bad in (0, 31, '8', True):
            with self.assertRaises(ValueError, msg=f'time_limit={bad!r}'):
                normalize(solver_time_limit=bad)
        normalize(solver_time_limit=1)
        normalize(solver_time_limit=30)

    def test_worker_bounds(self):
        for bad in (0, 9, 2.5, '4'):
            with self.assertRaises(ValueError, msg=f'workers={bad!r}'):
                normalize(solver_workers=bad)

    def test_seed_bounds(self):
        for bad in (-1, 1000000000, 1.5):
            with self.assertRaises(ValueError, msg=f'seed={bad!r}'):
                normalize(solver_seed=bad)

    def test_strategy_and_disruptions_validation(self):
        with self.assertRaises(ValueError):
            normalize(strategy='random')
        with self.assertRaises(ValueError):
            normalize(disruptions_enabled='yes')

    def test_disruptions_disabled_drops_delays_from_the_plan_config(self):
        delays = [{'service_id': 'REF-X-2026-11-18-MH-SEC-01', 'minutes': 10}]
        config = normalize(delays=delays, disruptions_enabled=False)
        self.assertEqual(config['delays'], [])
        self.assertEqual([e for e in config['incidents'] if e['kind'] == 'delay'], [])
        enabled = normalize(delays=delays)
        self.assertEqual(enabled['delays'], delays)

    def test_delay_propagate_must_be_boolean(self):
        with self.assertRaises(ValueError):
            normalize(delays=[{'service_id': 'REF-X', 'minutes': 5, 'propagate': 'yes'}])

    def test_solver_keys_never_leak_into_legacy_configs(self):
        legacy = engine.normalized_config(dict(BASE), {'version': 'legacy-scenario',
                                                        'movements': [], 'sections': [], 'tasks': []})
        self.assertNotIn('solver_workers', legacy)


if __name__ == '__main__':
    unittest.main()
