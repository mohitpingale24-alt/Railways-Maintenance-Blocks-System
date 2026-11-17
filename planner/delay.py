"""Train-delay scenario layer for the Maharashtra planner.

A delay is a controller-entered SIMULATION, never a live feed: the caller
supplies {service_id, minutes} entries naming movements from the selected
timetable references, and every function here recomputes from those real
movements. By default a delay moves only the named section movement;
with propagate=true it also shifts the same service run's downstream
section movements (same train number, starting within 24 hours) - the
expansion is explicit in every result (via='propagated'), and unrelated
services or the next day's run are never shifted. Nothing is hardcoded: gaps are rebuilt with the same arithmetic
the dataset builder and CP-SAT window layer use (00:00-06:00 candidate
period per day, listed movements expanded by the 5-minute headway), and
affected blocks are found by testing saved block intervals against the
revised movements with the validator's headway rule.

ML note: a delay changes scheduling constraints only. It never changes an
asset's features, so failure-risk and duration predictions (and the
six-factor Criticality Index built from them) are NOT recomputed here.
"""
import copy
from datetime import date, timedelta

HEADWAY = 5
WINDOW_MINUTES = 360  # synthetic 00:00-06:00 candidate period, as in store.dataset
SOURCE = 'MANUAL_SIMULATION'
SOURCE_NOTE = ('Controller-entered delay simulation. This is not a verified live '
               'train update; no live delay feed exists in this project.')

ELIGIBILITY = [
    'Inside the synthetic 00:00–06:00 candidate period.',
    'Selected timetable movements plus 5-minute headways are excluded; this is not complete traffic coverage.',
    'Request duration, model uncertainty buffer, setup, section/line, safety, department, crew, equipment and dependencies are checked by CP-SAT.',
    'Candidate only; no official track possession or operating clearance is implied.',
]


def validate_delays(delays):
    if delays is None:
        return []
    if not isinstance(delays, list) or len(delays) > 20:
        raise ValueError('A delay scenario supports up to 20 delayed movements.')
    seen = set()
    for d in delays:
        if not isinstance(d, dict) or not isinstance(d.get('service_id'), str) or not d['service_id']:
            raise ValueError('Each delay must name a timetable movement service_id.')
        if d['service_id'] in seen:
            raise ValueError('The same movement is delayed twice in one scenario.')
        seen.add(d['service_id'])
        if type(d.get('minutes')) is not int or not 0 <= d['minutes'] <= 1440:
            raise ValueError('Delay minutes must be a whole number from 0 to 1440.')
        if 'propagate' in d and type(d['propagate']) is not bool:
            raise ValueError('Delay propagate must be true or false.')
    return delays


# A delay can apply to one section movement only (the default), or
# propagate to the same service run's downstream sections. Propagation is
# bounded to movements of the same train number starting within 24 hours
# of the delayed movement - the same run, never the next day's run and
# never an unrelated service.
PROPAGATION_WINDOW_MINUTES = 1440


def affected_movements(movements, entry, target=None):
    """Movements shifted by one delay entry: the target, plus (when the
    entry propagates) the same run's downstream section movements."""
    if target is None:
        target = next((m for m in movements if m['service_id'] == entry['service_id']), None)
    if target is None:
        return []
    if not entry.get('propagate'):
        return [target]
    downstream = [m for m in movements
                  if m['service_id'] != target['service_id']
                  and m.get('train_no') == target.get('train_no')
                  and target['start'] <= m['start'] < target['start'] + PROPAGATION_WINDOW_MINUTES]
    return [target] + sorted(downstream, key=lambda m: m['start'])


def expand_service_ids(movements, delays):
    """Every movement service_id a delay scenario shifts, propagation
    included. Server and engine use this one expansion so gap analysis
    and CP-SAT incidents can never disagree."""
    ids = set()
    for entry in validate_delays(delays):
        for m in affected_movements(movements, entry):
            ids.add(m['service_id'])
    return ids


def apply_delays(movements, delays):
    """Return (shifted_movements, applied). The input list is never mutated.

    `applied` has one entry per shifted movement; propagated shifts carry
    via='propagated' and name the source movement they came from."""
    delays = validate_delays(delays)
    shift = {}
    for entry in delays:
        affected = affected_movements(movements, entry)
        if not affected:
            raise ValueError('Delayed movement not in the selected timetable: ' + entry['service_id'])
        mode = 'downstream' if entry.get('propagate') and len(affected) > 1 else 'section_only'
        for m in affected:
            if m['service_id'] in shift and shift[m['service_id']][0] != entry['minutes']:
                raise ValueError('Conflicting propagated delays reach movement ' + m['service_id'])
            shift[m['service_id']] = (entry['minutes'],
                                      'direct' if m['service_id'] == entry['service_id'] else 'propagated',
                                      entry['service_id'], mode)
    shifted, applied = [], []
    for m in movements:
        n = dict(m)
        if m['service_id'] in shift:
            minutes, via, source_id, mode = shift[m['service_id']]
            n = dict(m, start=m['start'] + minutes, end=m['end'] + minutes,
                     delayed_minutes=minutes, delay_source=SOURCE)
            applied.append(dict(
                service_id=m['service_id'], train_no=m.get('train_no'),
                section=m['section'], from_station=m.get('from_station'),
                to_station=m.get('to_station'), time_scope=m.get('time_scope'),
                original_start=m['start'], original_end=m['end'],
                revised_start=n['start'], revised_end=n['end'], minutes=minutes,
                via=via, source_service_id=source_id, propagation=mode))
        shifted.append(n)
    return shifted, applied


def dataset_period(data):
    """The dataset's Controller-selected planning period (minutes of day).
    Defaults to the legacy 00:00-06:00 synthetic period."""
    p = (data or {}).get('planning_period') or {}
    start, end = p.get('start_minutes', 0), p.get('end_minutes', WINDOW_MINUTES)
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= 1440:
        return (0, WINDOW_MINUTES)
    return (start, end)


def period_label(period):
    return f"{period[0] // 60:02}:{period[0] % 60:02}–{period[1] // 60:02}:{period[1] % 60:02}"


def gaps_for_section(movements, section, horizon, period=(0, WINDOW_MINUTES)):
    """Candidate gaps inside the planning period, engine/store arithmetic."""
    occupied = [(m['start'] - HEADWAY, m['end'] + HEADWAY) for m in movements
                if m['section'] == section]
    gaps = []
    for day in range(horizon):
        spans = [(day * 1440 + period[0], day * 1440 + period[1])]
        for a, b in occupied:
            revised = []
            for lo, hi in spans:
                if a >= hi or b <= lo:
                    revised.append((lo, hi))
                else:
                    if lo < a:
                        revised.append((lo, a))
                    if b < hi:
                        revised.append((b, hi))
            spans = revised
        gaps.extend(dict(section=section, day=day, start=lo, end=hi, minutes=hi - lo)
                    for lo, hi in spans)
    return gaps


def classify_gaps(before, after):
    """Label every before/after gap: unchanged, shrunk, expanded, shifted,
    disappeared or new. Matching is by same-day overlap."""
    changes = []
    for b in before:
        overlaps = [a for a in after if a['day'] == b['day'] and a['start'] < b['end'] and a['end'] > b['start']]
        if not overlaps:
            changes.append(dict(day=b['day'], kind='disappeared', before=b, after=None))
            continue
        a = max(overlaps, key=lambda x: min(x['end'], b['end']) - max(x['start'], b['start']))
        if a['start'] == b['start'] and a['end'] == b['end']:
            kind = 'unchanged'
        elif a['start'] >= b['start'] and a['end'] <= b['end']:
            kind = 'shrunk'
        elif a['start'] <= b['start'] and a['end'] >= b['end']:
            kind = 'expanded'
        else:
            kind = 'shifted'
        changes.append(dict(day=b['day'], kind=kind, before=b, after=a))
    for a in after:
        if not any(b['day'] == a['day'] and b['start'] < a['end'] and b['end'] > a['start'] for b in before):
            changes.append(dict(day=a['day'], kind='new', before=None, after=a))
    return sorted(changes, key=lambda c: (c['day'], (c['before'] or c['after'])['start']))


def _conflicting_trains(block, movements):
    return sorted({str(m.get('train_no') or m['service_id']) for m in movements
                   if m['section'] == block['section']
                   and block['start'] < m['end'] + HEADWAY
                   and block['end'] > m['start'] - HEADWAY})


def affected_blocks(blocks, before_movements, after_movements):
    """Blocks valid before that conflict with a revised movement after.

    Blocks that already conflicted before (not expected for a validated
    plan) are reported separately from delay-caused conflicts, and blocks
    on untouched sections simply pass the check."""
    affected, already, checked = [], [], 0
    for b in blocks or []:
        checked += 1
        after_hits = _conflicting_trains(b, after_movements)
        if not after_hits:
            continue
        before_hits = _conflicting_trains(b, before_movements)
        entry = dict(id=b.get('id'), section=b['section'], start=b['start'], end=b['end'],
                     tasks=list(b.get('tasks') or []),
                     conflicting_trains=after_hits,
                     reason=('Conflicts with revised movement(s) ' + ', '.join(after_hits) +
                             ' under the 5-minute headway rule after the simulated delay.'))
        (already if before_hits else affected).append(entry)
    return dict(affected=affected, already_conflicting=already, checked=checked)


def candidate_windows(data, shifted_movements):
    """Rebuild dataset candidate windows from shifted movements, mirroring
    store.dataset's construction (dates, boundary trains, conditions)."""
    selection = data.get('selection') or {}
    start = selection.get('start_date') or date.today().isoformat()
    horizon = int(selection.get('horizon', 7))
    period = dataset_period(data)
    eligibility = [f'Inside the synthetic {period_label(period)} candidate period.'] + ELIGIBILITY[1:]
    candidates = []
    for sec in data['sections']:
        section_movements = [m for m in shifted_movements if m['section'] == sec['id']]
        for day in range(horizon):
            day_start = day * 1440 + period[0]
            gaps = [(day_start, day * 1440 + period[1])]
            relevant = [m for m in section_movements
                        if m['start'] - HEADWAY < day * 1440 + period[1]
                        and m['end'] + HEADWAY > day_start]
            for movement in relevant:
                a, b = movement['start'] - HEADWAY, movement['end'] + HEADWAY
                gaps = [piece for lo, hi in gaps for piece in
                        ([(lo, hi)] if b <= lo or a >= hi else [(lo, min(hi, a)), (max(lo, b), hi)])
                        if piece[1] > piece[0]]
            for index, (a, b) in enumerate(gaps, 1):
                boundary = [m for m in relevant
                            if abs((m['end'] + HEADWAY) - a) <= 1 or abs((m['start'] - HEADWAY) - b) <= 1]
                candidates.append(dict(
                    id=f"CAND-{sec['id']}-{day + 1:02}-{index:02}", section=sec['id'],
                    date=(date.fromisoformat(start) + timedelta(days=day)).isoformat(), day=day,
                    start=a, end=b, available_duration=b - a, conflicting_trains=[],
                    boundary_trains=sorted({str(m.get('train_no') or m.get('service_id')) for m in boundary}),
                    eligibility_conditions=list(eligibility),
                    provenance='SIMULATED candidate gap derived from the selected timetable references, recalculated for a controller-entered delay scenario'))
    return candidates


def analyze(before_data, after_data, delays, blocks=None):
    """Full before/after comparison for one delay scenario."""
    delays = validate_delays(delays)
    before_movements = before_data['movements']
    after_movements = after_data['movements']
    _, applied = apply_delays(before_movements, delays)
    horizon = int((before_data.get('selection') or {}).get('horizon', 7))
    period = dataset_period(before_data)
    sections = sorted({a['section'] for a in applied})
    if not sections and (before_data.get('selection') or {}).get('section') not in (None, 'all'):
        sections = [before_data['selection']['section']]
    gaps_before = {s: gaps_for_section(before_movements, s, horizon, period) for s in sections}
    gaps_after = {s: gaps_for_section(after_movements, s, horizon, period) for s in sections}
    changes = {s: classify_gaps(gaps_before[s], gaps_after[s]) for s in sections}
    impact = affected_blocks(blocks, before_movements, after_movements)
    import hashlib, json
    fingerprint = hashlib.sha256(json.dumps(
        dict(base=before_data.get('fingerprint'), delays=delays), sort_keys=True).encode()).hexdigest()
    return dict(
        source=SOURCE, source_note=SOURCE_NOTE, scenario_fingerprint=fingerprint,
        input_fingerprint_before=before_data.get('fingerprint'),
        input_fingerprint_after=after_data.get('fingerprint'),
        trains=applied, sections=sections, horizon=horizon,
        planning_period=dict(start_minutes=period[0], end_minutes=period[1], label=period_label(period)),
        gaps_before=gaps_before, gaps_after=gaps_after, gap_changes=changes,
        affected_blocks=impact['affected'], already_conflicting=impact['already_conflicting'],
        blocks_checked=impact['checked'],
        summary=dict(
            delayed_trains=len(applied),
            affected_blocks=len(impact['affected']),
            gaps_disappeared=sum(1 for s in sections for c in changes[s] if c['kind'] == 'disappeared'),
            gaps_shrunk_or_shifted=sum(1 for s in sections for c in changes[s] if c['kind'] in ('shrunk', 'shifted')),
            gaps_new=sum(1 for s in sections for c in changes[s] if c['kind'] == 'new'),
            longest_gap_before={s: max((g['minutes'] for g in gaps_before[s]), default=0) for s in sections},
            longest_gap_after={s: max((g['minutes'] for g in gaps_after[s]), default=0) for s in sections}),
        limitations=[
            'Freight, special and unlisted traffic remain unknown; candidate gaps come only from the selected timetable references.',
            f'Every window is a conditional simulated candidate period ({period_label(period)}), never an official track possession or operating clearance.',
            'A delay changes scheduling constraints only; saved-model risk, duration and Criticality Index values are not recomputed.'])
