"""Regression tests for the configurable planning period.

The planning period is a Controller-selected synthetic assumption carried
on the dataset (planning_period {start_minutes, end_minutes}); it is not
hardcoded to 00:00-06:00. The legacy night period remains the default for
datasets that carry no planning_period (the legacy scenario builder), so
saved legacy plans validate exactly as before. All data here is synthetic
and hand-built: no timetable, gap or allocation is hardcoded from the real
Maharashtra dataset.
"""
import unittest

import delay as delay_mod
from engine import planning_period, period_label, validate, windows, window_report


def movement(section, start, end, service_id='SVC-1', train_no='10001'):
    return dict(service_id=service_id, train_no=train_no, section=section,
                start=start, end=end, kind='Passenger')


def mh_data(period=None, movements=()):
    data = dict(version='mh-lifecycle-v1', movements=list(movements),
                sections=[dict(id='MH-SEC-01', km_start=0.0, km_end=10.0, from_station=1, to_station=2)],
                tasks=[dict(id='T1', section='MH-SEC-01', line='UP', km_start=0.0, km_end=1.0,
                            duration=60, department='TRK', crew=1, machine=None,
                            power_required=False, separation_confirmed=True,
                            dependencies=[], release_day=0, due_day=6,
                            safety_action='NORMAL', task_type='Track inspection', priority=50.0)],
                resources=dict(TRK=10))
    if period is not None:
        data['planning_period'] = dict(start_minutes=period[0], end_minutes=period[1],
                                       label=period_label(*period))
    return data


def block(start, duration=70):
    return dict(id='B1', section='MH-SEC-01', line='UP', tasks=['T1'], departments=['TRK'],
                duration=duration, start=start, end=start + duration,
                power=False, decision='Proposed')


CONFIG = dict(horizon=7, start_date='2026-11-18', incidents=[])


class PlanningPeriodTests(unittest.TestCase):
    def test_period_parsing_defaults_and_validation(self):
        self.assertEqual(planning_period({}), (0, 360))
        self.assertEqual(planning_period(mh_data((0, 1440))), (0, 1440))
        self.assertEqual(period_label(0, 1440), '00:00\u201324:00')
        for bad in [(360, 360), (700, 300), (-1, 60), (0, 1441)]:
            with self.assertRaises(ValueError):
                planning_period(mh_data(bad))

    def test_daytime_gaps_in_day_and_full_day_periods(self):
        moves = [movement('MH-SEC-01', 480, 540)]  # one train 08:00-09:00
        for period in [(360, 1320), (0, 1440)]:
            data = mh_data(period, moves)
            starts = windows(data, 'MH-SEC-01', 100, 0, 1, [])
            self.assertTrue(any(lo >= 545 for lo, _ in starts), period)
            self.assertTrue(any(lo + 100 <= 475 for lo, _ in starts), period)
            report = window_report(data, 'MH-SEC-01', 1)
            self.assertEqual(report['initial_window_minutes_per_day'], period[1] - period[0])
        legacy = window_report(mh_data(None, moves), 'MH-SEC-01', 1)
        self.assertEqual(legacy['longest_candidate_window_minutes'], 360)

    def test_busy_night_section_opens_up_only_in_full_day(self):
        # Overnight trains tile 00:00-06:00 end to end (with headways).
        moves = [movement('MH-SEC-01', -20, 200, 'SVC-1'), movement('MH-SEC-01', 190, 420, 'SVC-2')]
        night = mh_data(None, moves)
        self.assertEqual(windows(night, 'MH-SEC-01', 60, 0, 1, []), [])
        self.assertEqual(window_report(night, 'MH-SEC-01', 1)['longest_candidate_window_minutes'], 0)
        day = mh_data((0, 1440), moves)
        self.assertTrue(windows(day, 'MH-SEC-01', 120, 0, 1, []))
        self.assertGreaterEqual(window_report(day, 'MH-SEC-01', 1)['longest_candidate_window_minutes'], 1000)

    def test_overnight_train_spill_is_respected(self):
        # Previous-day train still on the section until 01:30, and a train
        # departing 23:30 crosses midnight out of the day.
        moves = [movement('MH-SEC-01', -30, 90, 'SVC-1'), movement('MH-SEC-01', 1410, 1500, 'SVC-2')]
        data = mh_data((0, 1440), moves)
        report = window_report(data, 'MH-SEC-01', 1)
        gaps = report['gaps_by_day'][0]
        self.assertEqual(len(gaps), 1)
        self.assertEqual((gaps[0]['start'], gaps[0]['end']), (95, 1405))
        for lo, hi in windows(data, 'MH-SEC-01', 60, 0, 1, []):
            self.assertGreaterEqual(lo, 95)
            self.assertLessEqual(hi + 60, 1405)

    def test_candidate_gap_is_not_a_feasible_gap(self):
        # A 30-minute timetable gap exists but no 120-minute job fits it:
        # candidate diagnostics report it, the feasible window layer does not.
        moves = [movement('MH-SEC-01', 0, 175, 'SVC-1'), movement('MH-SEC-01', 215, 360, 'SVC-2')]
        data = mh_data(None, moves)
        report = window_report(data, 'MH-SEC-01', 1)
        self.assertEqual(report['candidate_gaps'], 1)
        self.assertEqual(report['longest_candidate_window_minutes'], 30)
        self.assertEqual(windows(data, 'MH-SEC-01', 120, 0, 1, []), [])
        self.assertTrue(windows(data, 'MH-SEC-01', 25, 0, 1, []))

    def test_full_closure_leaves_no_windows(self):
        closure = [dict(kind='freight', section='MH-SEC-01', start=0, end=1440)]
        data = mh_data((0, 1440), [])
        self.assertEqual(windows(data, 'MH-SEC-01', 60, 0, 1, closure), [])
        blocked = mh_data((0, 1440), [movement('MH-SEC-01', -10, 1450)])
        self.assertEqual(window_report(blocked, 'MH-SEC-01', 1)['candidate_gaps'], 0)
        self.assertEqual(windows(blocked, 'MH-SEC-01', 15, 0, 1, []), [])

    def test_validator_uses_the_selected_period(self):
        legacy_errors = validate(mh_data(None), dict(config=CONFIG, blocks=[block(600)]))
        self.assertTrue(any('outside the conditional 00:00\u201306:00' in e for e in legacy_errors), legacy_errors)
        full = mh_data((0, 1440))
        self.assertEqual(validate(full, dict(config=CONFIG, blocks=[block(600)])), [])
        day = mh_data((360, 1320))
        self.assertEqual(validate(day, dict(config=CONFIG, blocks=[block(600)])), [])
        night_errors = validate(day, dict(config=CONFIG, blocks=[block(180)]))
        self.assertTrue(any('outside the conditional 06:00\u201322:00' in e for e in night_errors), night_errors)

    def test_cpsat_schedules_a_daytime_block_in_full_day_period(self):
        # Regression: solve() once constrained every block start to the
        # first 359 minutes of a day (the forecast-cost lookup assumed
        # 00:00-06:00 starts), so a wider planning period produced windows
        # the solver could never use. The only gap here is 10:00-12:00.
        import engine as engine_mod
        # The two trains run DAILY, so the same pattern repeats on every
        # horizon day: each day is occupied except the 10:00-12:00 gap.
        # (A single-day fixture leaves days 1-6 completely free, and the
        # legacy solver then correctly schedules into a later night -
        # the block at day 1 00:59 in the first Mac run was the fixture's
        # fault, not the engine's.)
        moves = [movement('MH-SEC-01', day * 1440 - 10, day * 1440 + 595, f'SVC-1-{day}')
                 for day in range(7)] + [movement('MH-SEC-01', day * 1440 + 725, day * 1440 + 1450, f'SVC-2-{day}')
                                         for day in range(7)]
        data = mh_data((0, 1440), moves)
        data['forecasts'] = [[[0.0] * 24] for _ in range(7)]
        result = engine_mod.solve(data, dict(CONFIG))
        self.assertIn(result['solver_status'], ('FEASIBLE', 'OPTIMAL'))
        self.assertEqual(len(result['blocks']), 1)
        self.assertGreaterEqual(result['blocks'][0]['start'], 600)
        self.assertLessEqual(result['blocks'][0]['end'], 720)
        legacy = mh_data(None, moves)
        legacy['forecasts'] = [[[0.0] * 24] for _ in range(7)]
        legacy_result = engine_mod.solve(legacy, dict(CONFIG))
        self.assertEqual(legacy_result['blocks'], [])

    def test_delay_rescheduling_recomputes_gaps_in_selected_period(self):
        moves = [movement('MH-SEC-01', 480, 540)]
        period = (0, 1440)
        before = dict(movements=moves, selection=dict(horizon=7, section='MH-SEC-01', start_date='2026-11-18'),
                      fingerprint='before', planning_period=dict(start_minutes=0, end_minutes=1440))
        shifted, _ = delay_mod.apply_delays(moves, [dict(service_id='SVC-1', minutes=120)])
        after = dict(before, movements=shifted, fingerprint='after')
        result = delay_mod.analyze(before, after, [dict(service_id='SVC-1', minutes=120)], blocks=[])
        self.assertEqual(result['planning_period']['label'], '00:00\u201324:00')
        kinds = {c['kind'] for c in result['gap_changes']['MH-SEC-01']}
        self.assertTrue(kinds & {'shrunk', 'shifted', 'disappeared', 'new'}, kinds)
        legacy_before = dict(before, planning_period=dict(start_minutes=0, end_minutes=360))
        legacy_shifted, _ = delay_mod.apply_delays(moves, [dict(service_id='SVC-1', minutes=120)])
        legacy_after = dict(legacy_before, movements=legacy_shifted, fingerprint='after')
        legacy = delay_mod.analyze(legacy_before, legacy_after, [dict(service_id='SVC-1', minutes=120)], blocks=[])
        # The 08:00 train is outside the legacy night period entirely, so a
        # delay to it changes nothing there - the period drives the result.
        self.assertTrue(all(c['kind'] == 'unchanged' for c in legacy['gap_changes']['MH-SEC-01']))


if __name__ == '__main__':
    unittest.main()
