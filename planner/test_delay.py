"""Delay-scenario tests for the Train Delay & Rescheduling workflow.

Two layers:
* TestDelayLayer - pure delay mathematics on the REAL section-specific
  timetable (Pune-Lonavala, MH-SEC-04). No ortools or model artifacts
  needed; every expected number below was computed from
  timetable.movements('2026-11-18', 7), whose day-0 candidate gaps are
  (45,65,20), (175,180,5) and (272,360,88).
* TestDelayEngine - full CP-SAT replans on the real Maharashtra dataset
  (skipped when ortools or the saved artifacts are unavailable). Sections
  and trains are chosen from the data at runtime, never hardcoded, so the
  tests stay honest if the synthetic dataset is regenerated.
"""
import copy
import unittest

import delay
from maharashtra import network as mh_network
from maharashtra import timetable

START = '2026-11-18'  # a Wednesday; every date-applicable service runs daily
SEC = 'MH-SEC-04'     # Pune-Lonavala


def movements():
    return timetable.movements(START, 7)


def sid(train_no, day='2026-11-18'):
    return f'REF-{train_no}-{day}-{SEC}'


class TestDelayLayer(unittest.TestCase):
    def setUp(self):
        self.moves = movements()
        self.before = delay.gaps_for_section(self.moves, SEC, 7)

    def test_baseline_gaps_are_the_measured_ones(self):
        day0 = [(g['start'], g['end']) for g in self.before if g['day'] == 0]
        self.assertEqual(day0, [(45, 65), (175, 180), (272, 360)])

    def test_scenario1_delay_overlaps_window_and_shrinks_gap(self):
        # 17412 (03:30-04:27, minutes 210-267) delayed +30 -> 240-297.
        shifted, applied = delay.apply_delays(self.moves, [{'service_id': sid('17412'), 'minutes': 30}])
        self.assertEqual((applied[0]['revised_start'], applied[0]['revised_end']), (240, 297))
        after = delay.gaps_for_section(shifted, SEC, 7)
        day0 = [(g['start'], g['end'], g['minutes']) for g in after if g['day'] == 0]
        self.assertIn((302, 360, 58), day0)
        kinds = {(c['before'] or c['after'])['start']: c['kind']
                 for c in delay.classify_gaps(self.before, after) if c['day'] == 0}
        self.assertEqual(kinds[272], 'shrunk')
        # A block occupying the old window is now affected, with the train named.
        block = {'id': 'BLK-T', 'section': SEC, 'start': 272, 'end': 360, 'tasks': ['T1']}
        impact = delay.affected_blocks([block], self.moves, shifted)
        self.assertEqual(len(impact['affected']), 1)
        self.assertEqual(impact['affected'][0]['conflicting_trains'], ['17412'])

    def test_scenario2_window_shifts_but_maintenance_still_fits(self):
        # Same delay: a 50-minute block no longer fits at 272, but the
        # recalculated 58-minute gap still fits it (CP-SAT moves it to >=302).
        shifted, _ = delay.apply_delays(self.moves, [{'service_id': sid('17412'), 'minutes': 30}])
        after = delay.gaps_for_section(shifted, SEC, 7)
        self.assertTrue(any(g['minutes'] >= 50 for g in after if g['day'] == 0))
        # And a delay that does not touch a block's gap leaves it unaffected.
        shifted2, _ = delay.apply_delays(self.moves, [{'service_id': sid('17317'), 'minutes': 15}])
        block = {'id': 'BLK-U', 'section': SEC, 'start': 272, 'end': 360, 'tasks': ['T2']}
        self.assertEqual(delay.affected_blocks([block], self.moves, shifted2)['affected'], [])

    def test_scenario3_no_alternative_window_remains(self):
        # Delay 17412 +30 on every day: the only 88-minute gap drops to 58,
        # so an 88-minute requirement has zero feasible (day, gap) pairs.
        delays = [{'service_id': sid('17412', f'2026-11-{18 + d}'), 'minutes': 30} for d in range(7)]
        shifted, _ = delay.apply_delays(self.moves, delays)
        after = delay.gaps_for_section(shifted, SEC, 7)
        self.assertFalse(any(g['minutes'] >= 88 for g in after))
        self.assertTrue(any(g['minutes'] >= 88 for g in self.before))

    def test_scenario4_one_new_slot_two_competitors(self):
        # Delaying 17317 +15 widens the 5-minute sliver (175,180) to
        # (175,195): exactly one 20-minute slot appears where none fitted.
        shifted, _ = delay.apply_delays(self.moves, [{'service_id': sid('17317'), 'minutes': 15}])
        after = delay.gaps_for_section(shifted, SEC, 7)
        new_slots = [g for g in after if g['day'] == 0 and g['start'] == 175]
        self.assertEqual([(g['end'], g['minutes']) for g in new_slots], [(195, 20)])
        fitting_before = [g for g in self.before if g['day'] == 0 and g['minutes'] >= 20 and g['start'] == 175]
        self.assertEqual(fitting_before, [])

    def test_scenario5_multiple_trains_delayed_same_section(self):
        delays = [{'service_id': sid('17412'), 'minutes': 30},
                  {'service_id': sid('17317'), 'minutes': 15}]
        shifted, applied = delay.apply_delays(self.moves, delays)
        self.assertEqual(len(applied), 2)
        day0 = [(g['start'], g['end']) for g in delay.gaps_for_section(shifted, SEC, 7) if g['day'] == 0]
        self.assertEqual(day0, [(45, 65), (175, 195), (302, 360)])

    def test_scenario6_delay_crosses_midnight(self):
        # 11020 departs 23:40, arrives 00:40 next day; +30 -> ends 01:10.
        shifted, applied = delay.apply_delays(self.moves, [{'service_id': sid('11020'), 'minutes': 30}])
        self.assertEqual((applied[0]['original_start'], applied[0]['original_end']), (1420, 1480))
        self.assertEqual((applied[0]['revised_start'], applied[0]['revised_end']), (1450, 1510))
        # The next day's first gap (20 min at 1485-1505) is consumed entirely.
        after = delay.gaps_for_section(shifted, SEC, 7)
        self.assertFalse(any(g['day'] == 1 and g['start'] == 1485 for g in after))
        kinds = [c['kind'] for c in delay.classify_gaps(self.before, after)
                 if c['day'] == 1 and c['before'] and c['before']['start'] == 1485]
        self.assertEqual(kinds, ['disappeared'])

    def test_scenario7_inputs_are_never_mutated(self):
        # A simulation must not silently modify the approved inputs: the
        # original movements and plan blocks survive analysis untouched.
        snapshot = copy.deepcopy(self.moves)
        blocks = [{'id': 'B1', 'section': SEC, 'start': 272, 'end': 360, 'tasks': ['T1']}]
        blocks_snapshot = copy.deepcopy(blocks)
        shifted, _ = delay.apply_delays(self.moves, [{'service_id': sid('17412'), 'minutes': 45}])
        delay.affected_blocks(blocks, self.moves, shifted)
        self.assertEqual(self.moves, snapshot)
        self.assertEqual(blocks, blocks_snapshot)
        self.assertTrue(all('delayed_minutes' not in m for m in self.moves))

    def test_scenario8_zero_delay_changes_nothing(self):
        shifted, _ = delay.apply_delays(self.moves, [{'service_id': sid('17412'), 'minutes': 0}])
        self.assertEqual(delay.gaps_for_section(shifted, SEC, 7), self.before)
        kinds = {c['kind'] for c in delay.classify_gaps(self.before, delay.gaps_for_section(shifted, SEC, 7))}
        self.assertEqual(kinds, {'unchanged'})
        block = {'id': 'BLK-T', 'section': SEC, 'start': 272, 'end': 360, 'tasks': ['T1']}
        self.assertEqual(delay.affected_blocks([block], self.moves, shifted)['affected'], [])

    def test_delay_validation(self):
        for bad in ([{'service_id': 'NOPE', 'minutes': 10}],
                    [{'service_id': sid('17412'), 'minutes': -5}],
                    [{'service_id': sid('17412'), 'minutes': 10.5}],
                    [{'service_id': sid('17412'), 'minutes': 10},
                     {'service_id': sid('17412'), 'minutes': 20}]):
            with self.assertRaises(ValueError):
                delay.apply_delays(self.moves, bad)

    def test_candidate_windows_rebuild_matches_gap_math(self):
        graph = mh_network.network()
        data = {'sections': graph['sections'], 'selection': {'start_date': START, 'horizon': 7}}
        shifted, _ = delay.apply_delays(self.moves, [{'service_id': sid('17412'), 'minutes': 30}])
        windows = delay.candidate_windows(data, shifted)
        rebuilt = {(w['section'], w['day'], w['start'], w['end']) for w in windows}
        for sec in ('MH-SEC-04', 'MH-SEC-01'):
            for g in delay.gaps_for_section(shifted, sec, 7):
                self.assertIn((sec, g['day'], g['start'], g['end']), rebuilt)


try:
    import engine
    from maharashtra import store
    ENGINE_READY = True
except Exception:
    ENGINE_READY = False


@unittest.skipUnless(ENGINE_READY, 'ortools or Maharashtra artifacts unavailable')
class TestDelayEngine(unittest.TestCase):
    """CP-SAT replans over the real dataset, trimmed to a few tasks so the
    solves stay fast. Server composition is replicated exactly: shift the
    movements, rebuild candidate windows, keep delays in the config."""

    @classmethod
    def setUpClass(cls):
        cls.base = store.dataset({'zone': 'Z2', 'corridor': 'all', 'section': 'all',
                                  'start_date': START, 'horizon': 7,
                                  'traffic_mode': 'reference', 'resource_overrides': {},
                                  'batch_offset': 0, 'task_overrides': {}})
        # Choose a section whose measured day-0 gap can hold a real block.
        cls.section = None
        for sec in cls.base['sections']:
            gaps = delay.gaps_for_section(cls.base['movements'], sec['id'], 7)
            if any(g['day'] == 0 and g['minutes'] >= 150 for g in gaps):
                cls.section = sec['id']
                break
        if cls.section is None:
            raise unittest.SkipTest('no section with a >=150-minute day-0 gap in this dataset')

    def trimmed(self, data, task_ids):
        trimmed = dict(data)
        trimmed['tasks'] = [t for t in data['tasks'] if t['id'] in task_ids]
        return trimmed

    def config(self, **extra):
        cfg = {'zone': 'Z2', 'corridor': 'all', 'section': self.section,
               'start_date': START, 'horizon': 7, 'traffic_mode': 'reference',
               'incidents': []}
        cfg.update(extra)
        return cfg

    def delayed_data(self, data, delays):
        shifted, _ = delay.apply_delays(data['movements'], delays)
        out = dict(data)
        out['movements'] = shifted
        out['traffic'] = shifted
        out['candidate_windows'] = delay.candidate_windows(out, shifted)
        return out

    def section_tasks(self, count=1):
        # STOP_AND_REVIEW tasks are withheld from scheduling by design and
        # can never appear in a block; fixtures must exclude them.
        return [t for t in self.base['tasks']
                if t['section'] == self.section and not t['dependencies']
                and t.get('safety_action') != 'STOP_AND_REVIEW'][:count]

    def test_scenario1_and_7_replan_moves_only_the_affected_block(self):
        tasks = self.section_tasks(2)
        if len(tasks) < 2:
            self.skipTest('not enough dependency-free tasks on the chosen section')
        data = self.trimmed(self.base, [t['id'] for t in tasks])
        baseline = engine.generate(data, self.config())
        self.assertEqual(engine.validate(data, baseline), [])
        scheduled = [b for b in baseline['blocks'] if b['section'] == self.section]
        if not scheduled:
            self.skipTest('baseline scheduled nothing on the chosen section')
        block = scheduled[0]
        # Delay the last train that clears before the block so its revised
        # interval lands inside the block.
        prior = [m for m in data['movements']
                 if m['section'] == self.section and m['start'] >= 0
                 and 0 <= block['start'] - m['end'] <= 1200]
        if not prior:
            self.skipTest('no earlier train to delay into the block')
        train = max(prior, key=lambda m: m['end'])
        minutes = block['start'] - train['end'] + 30
        delays = [{'service_id': train['service_id'], 'minutes': minutes}]
        snapshot = copy.deepcopy(baseline)
        delayed = self.delayed_data(data, delays)
        revised = engine.generate(delayed, self.config(delays=delays), previous=baseline)
        self.assertEqual(baseline, snapshot, 'the original plan object must not be modified')
        self.assertEqual(engine.validate(delayed, revised), [])
        moved_or_deferred = (
            any(b['id'] == block['id'] and b['start'] != block['start'] for b in revised['blocks'])
            or any(u['id'] in block['tasks'] for u in revised['unscheduled']))
        self.assertTrue(moved_or_deferred, 'the affected block must move or defer with a reason')
        for u in revised['unscheduled']:
            if u['id'] in block['tasks']:
                self.assertTrue(u['reason'])

    def test_scenario2_small_delay_retains_unaffected_block(self):
        tasks = self.section_tasks(1)
        if not tasks:
            self.skipTest('no task on the chosen section')
        data = self.trimmed(self.base, [tasks[0]['id']])
        baseline = engine.generate(data, self.config())
        scheduled = [b for b in baseline['blocks'] if b['section'] == self.section]
        if not scheduled:
            self.skipTest('baseline scheduled nothing on the chosen section')
        block = scheduled[0]
        # Delay a train on a DIFFERENT section: nothing on this section changes.
        other = next(m for m in data['movements']
                     if m['section'] != self.section and m['start'] >= 0)
        delays = [{'service_id': other['service_id'], 'minutes': 20}]
        delayed = self.delayed_data(data, delays)
        revised = engine.generate(delayed, self.config(delays=delays), previous=baseline)
        kept = [b for b in revised['blocks'] if b['id'] == block['id']]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]['start'], block['start'], 'unaffected allocation must be retained')

    def pune_task(self, duration=78):
        for t in self.base['tasks']:
            if t['section'] == SEC and t.get('safety_action') != 'STOP_AND_REVIEW':
                task = copy.deepcopy(t)
                task['dependencies'] = []
                task['duration'] = duration
                task['release_day'] = 0
                task['due_day'] = 6
                return task
        return None

    def test_scenario3_delay_closes_every_window_so_work_defers(self):
        task = self.pune_task()
        if task is None:
            self.skipTest('no Pune-Lonavala task in this dataset batch')
        data = self.trimmed(self.base, [])
        data['tasks'] = [task]
        cfg = {'zone': 'Z2', 'corridor': 'all', 'section': SEC, 'start_date': START,
               'horizon': 7, 'traffic_mode': 'reference', 'incidents': []}
        baseline = engine.generate(data, cfg)
        self.assertTrue(any(task['id'] in b['tasks'] for b in baseline['blocks']),
                        'fixture check: the 88-minute gap must hold this block before the delay')
        delays = [{'service_id': sid('17412', f'2026-11-{18 + d}'), 'minutes': 30} for d in range(7)]
        delayed = self.delayed_data(data, delays)
        revised = engine.generate(delayed, dict(cfg, delays=delays))
        self.assertEqual(engine.validate(delayed, revised), [])
        self.assertFalse(any(task['id'] in b['tasks'] for b in revised['blocks']))
        reasons = [u['reason'] for u in revised['unscheduled'] if u['id'] == task['id']]
        self.assertEqual(len(reasons), 1)
        self.assertIn('candidate gap', reasons[0])

    def test_scenario4_two_groups_compete_for_the_only_remaining_gap(self):
        first = self.pune_task()
        if first is None:
            self.skipTest('no Pune-Lonavala task in this dataset batch')
        second = None
        for t in self.base['tasks']:
            if t['section'] == SEC and t['id'] != first['id'] and t.get('safety_action') != 'STOP_AND_REVIEW':
                candidate = copy.deepcopy(t)
                candidate.update(dependencies=[], duration=78, release_day=0, due_day=6,
                                 km_start=first['km_start'] + 10, km_end=first['km_end'] + 10)
                if len(engine.bundle([first, candidate])) == 2:
                    second = candidate
                    break
        if second is None:
            self.skipTest('could not form two competing groups on Pune-Lonavala')
        data = self.trimmed(self.base, [])
        data['tasks'] = [first, second]
        cfg = {'zone': 'Z2', 'corridor': 'all', 'section': SEC, 'start_date': START,
               'horizon': 7, 'traffic_mode': 'reference', 'incidents': []}
        # Close the 88-minute gap on days 1-6; only day 0 can hold one block.
        delays = [{'service_id': sid('17412', f'2026-11-{18 + d}'), 'minutes': 30} for d in range(1, 7)]
        delayed = self.delayed_data(data, delays)
        revised = engine.generate(delayed, dict(cfg, delays=delays))
        self.assertEqual(engine.validate(delayed, revised), [])
        scheduled = {tid for b in revised['blocks'] for tid in b['tasks']}
        deferred = {u['id'] for u in revised['unscheduled']}
        self.assertEqual(len(scheduled & {first['id'], second['id']}), 1,
                         'exactly one competitor fits the single remaining gap')
        self.assertEqual(len(deferred & {first['id'], second['id']}), 1)
        winner = next(iter(scheduled & {first['id'], second['id']}))
        loser = next(iter(deferred & {first['id'], second['id']}))
        priorities = {first['id']: first['priority'], second['id']: second['priority']}
        self.assertGreaterEqual(priorities[winner], priorities[loser],
                                'the higher-criticality request must win the contested slot')

    def test_scenario8_zero_delay_replan_is_identical(self):
        tasks = self.section_tasks(1)
        if not tasks:
            self.skipTest('no task on the chosen section')
        data = self.trimmed(self.base, [tasks[0]['id']])
        baseline = engine.generate(data, self.config())
        train = next(m for m in data['movements'] if m['section'] == self.section and m['start'] >= 0)
        delays = [{'service_id': train['service_id'], 'minutes': 0}]
        delayed = self.delayed_data(data, delays)
        revised = engine.generate(delayed, self.config(delays=delays), previous=baseline)
        self.assertEqual([(b['id'], b['start']) for b in revised['blocks']],
                         [(b['id'], b['start']) for b in baseline['blocks']])
        self.assertEqual([u['id'] for u in revised['unscheduled']],
                         [u['id'] for u in baseline['unscheduled']])


@unittest.skipUnless(ENGINE_READY, 'ortools or Maharashtra artifacts unavailable')
class TestCriticalityTieBreak(unittest.TestCase):
    """Deterministic policy proof, independent of the regenerated dataset:
    two tasks identical in every scheduling factor (same section, line,
    department, crew, duration, release/due days, exactly one feasible
    start in the whole horizon) and differing ONLY in Criticality Index
    by 0.4 - the higher-criticality task must win the contested slot,
    whichever order the tasks are presented in. Regression guard for the
    int(priority) truncation that made 68.4 and 68.0 tie in the objective."""

    def _task(self, tid, priority):
        return dict(id=tid, section=SEC, line='Up', zone='Z2', department='TRK',
                    task_type='Track inspection', crew=3, machine=None,
                    power_required=False, separation_confirmed=False,
                    dependencies=[], release_day=0, due_day=6, duration=78,
                    predicted_duration=78, duration_buffer=0,
                    km_start=10.0, km_end=10.3, safety_action='NORMAL',
                    priority=priority)

    def _data(self, tasks):
        movements = [{'section': SEC, 'start': 0, 'end': 267}]
        movements += [{'section': SEC, 'start': d * 1440, 'end': d * 1440 + 360}
                      for d in range(1, 7)]
        return {'version': 'mh-test', 'tasks': tasks, 'movements': movements,
                'sections': [{'id': SEC, 'from_station': 'Pune', 'to_station': 'Lonavala'}],
                'resources': {'TRK': 24}, 'candidate_windows': [],
                'forecasts': [[[0.0] * 24 for _ in range(19)] for _ in range(7)]}

    def test_higher_criticality_wins_when_all_else_is_equal(self):
        hi = self._task('TASK-HI', 68.4)
        lo = self._task('TASK-LO', 68.0)
        for order in ([hi, lo], [lo, hi]):
            data = self._data([copy.deepcopy(t) for t in order])
            result = engine.solve(data, {'horizon': 7, 'incidents': []})
            self.assertIn(result['solver_status'], ('OPTIMAL', 'FEASIBLE'))
            scheduled = {tid for b in result['blocks'] for tid in b['tasks']}
            deferred = {u['id'] for u in result['unscheduled']}
            self.assertEqual(scheduled, {'TASK-HI'},
                             'the 68.4 task must win the only slot over the identical 68.0 task')
            self.assertEqual(deferred, {'TASK-LO'})


if __name__ == '__main__':
    unittest.main()


class TestDelayPropagation(unittest.TestCase):
    """Propagation semantics on the real timetable: 17617 runs SEC-11
    (645-785) -> SEC-12 (790-978) -> SEC-13 (980-1080) on day 0."""

    def setUp(self):
        self.moves = movements()

    def sid(self, train_no, section, day='2026-11-18'):
        return f'REF-{train_no}-{day}-{section}'

    def starts(self, shifted, train_no, section, lo=0, hi=1440):
        return sorted((m['start'], m['end']) for m in shifted
                      if m['train_no'] == train_no and m['section'] == section and lo <= m['start'] < hi)

    def test_section_only_is_the_default(self):
        shifted, applied = delay.apply_delays(self.moves, [
            {'service_id': self.sid('17617', 'MH-SEC-11'), 'minutes': 30}])
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]['via'], 'direct')
        self.assertEqual(applied[0]['propagation'], 'section_only')
        self.assertEqual(self.starts(shifted, '17617', 'MH-SEC-12'), [(790, 978)])

    def test_propagation_shifts_downstream_sections(self):
        shifted, applied = delay.apply_delays(self.moves, [
            {'service_id': self.sid('17617', 'MH-SEC-11'), 'minutes': 30, 'propagate': True}])
        self.assertEqual({a['section'] for a in applied}, {'MH-SEC-11', 'MH-SEC-12', 'MH-SEC-13'})
        vias = {a['section']: a['via'] for a in applied}
        self.assertEqual(vias['MH-SEC-11'], 'direct')
        self.assertEqual(vias['MH-SEC-12'], 'propagated')
        self.assertEqual(self.starts(shifted, '17617', 'MH-SEC-12'), [(820, 1008)])
        self.assertEqual(self.starts(shifted, '17617', 'MH-SEC-13'), [(1010, 1110)])
        # An unrelated service on a downstream section never moves.
        self.assertEqual(self.starts(shifted, '12139', 'MH-SEC-15'), [(65, 145)])

    def test_propagation_never_touches_the_next_day_run(self):
        shifted, _ = delay.apply_delays(self.moves, [
            {'service_id': self.sid('17617', 'MH-SEC-11'), 'minutes': 30, 'propagate': True}])
        self.assertEqual(self.starts(shifted, '17617', 'MH-SEC-11', 1440, 2880), [(2085, 2225)])

    def test_propagation_rebuilds_downstream_gaps(self):
        entry = {'service_id': self.sid('17617', 'MH-SEC-11'), 'minutes': 120, 'propagate': True}
        shifted, _ = delay.apply_delays(self.moves, [entry])
        # Full-day planning period: the downstream SEC-12 movement (790-978)
        # sits outside the legacy night period, so only a wider period can
        # show its gaps move - exactly the planning-period interaction.
        period = {'start_minutes': 0, 'end_minutes': 1440}
        before = {'movements': self.moves, 'selection': {'horizon': 7}, 'fingerprint': 'x', 'planning_period': period}
        after = {'movements': shifted, 'selection': {'horizon': 7}, 'fingerprint': 'y', 'planning_period': period}
        result = delay.analyze(before, after, [entry], [])
        self.assertIn('MH-SEC-12', result['sections'])
        self.assertNotEqual(result['gaps_before']['MH-SEC-12'], result['gaps_after']['MH-SEC-12'])

    def test_expand_service_ids_matches_applied(self):
        entries = [{'service_id': self.sid('17617', 'MH-SEC-11'), 'minutes': 15, 'propagate': True},
                   {'service_id': self.sid('17412', SEC), 'minutes': 10}]
        _, applied = delay.apply_delays(self.moves, entries)
        self.assertEqual(delay.expand_service_ids(self.moves, entries),
                         {a['service_id'] for a in applied})

    def test_conflicting_propagated_delays_are_rejected(self):
        with self.assertRaises(ValueError):
            delay.apply_delays(self.moves, [
                {'service_id': self.sid('17617', 'MH-SEC-11'), 'minutes': 30, 'propagate': True},
                {'service_id': self.sid('17617', 'MH-SEC-12'), 'minutes': 45}])

    def test_propagate_must_be_boolean(self):
        with self.assertRaises(ValueError):
            delay.validate_delays([{'service_id': self.sid('17617', 'MH-SEC-11'),
                                    'minutes': 30, 'propagate': 'yes'}])
