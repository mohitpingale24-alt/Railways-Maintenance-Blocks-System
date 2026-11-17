"""Regression checks for the independently audited monthly fallback."""
import copy
import unittest
from unittest.mock import patch

from scenario import build_scenario
import engine
from engine import solve_rolling, validate


class RollingFallbackTests(unittest.TestCase):
    def test_monthly_fallback_audits_complete_schedule(self):
        data = copy.deepcopy(build_scenario())
        data['tasks'] = data['tasks'][:12]
        config = dict(horizon=30, start_date='2026-09-08', incidents=[])
        result = solve_rolling(data, config)
        self.assertEqual(result['solver_status'], 'ROLLING_FEASIBLE')
        self.assertTrue(result['diagnostics']['stages'])
        self.assertTrue(all(s['status'] in ('OPTIMAL', 'FEASIBLE') for s in result['diagnostics']['stages']))
        assigned = [task for block in result['blocks'] for task in block['tasks']]
        self.assertTrue(assigned)
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(set(assigned) | {t['id'] for t in result['unscheduled']}, {t['id'] for t in data['tasks']})
        self.assertEqual(validate(data, dict(config=config, blocks=result['blocks'])), [])

    def test_unknown_global_uses_audited_rolling_result(self):
        data = copy.deepcopy(build_scenario())
        data['tasks'] = data['tasks'][:12]
        config = dict(horizon=30, start_date='2026-09-08', incidents=[])
        original = engine.solve
        def solve_with_unknown(data, config, *args, **kwargs):
            if kwargs.get('joint', True) and kwargs.get('period') is None:
                return dict(solver_status='UNKNOWN', blocks=[], unscheduled=[], seconds=0)
            return original(data, config, *args, **kwargs)
        with patch.object(engine, 'solve', side_effect=solve_with_unknown):
            result = engine.generate(data, config)
        self.assertEqual(result['solver_status'], 'ROLLING_FEASIBLE')
        self.assertGreater(len(result['blocks']), 0)
        self.assertEqual(validate(data, result), [])
