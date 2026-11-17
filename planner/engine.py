"""Conservative night-possession CP-SAT model and independent feasibility audit."""
from collections import defaultdict
from itertools import combinations
from datetime import date
import copy
import time
import logging
from ortools.sat.python import cp_model

SETUP = 10
HEADWAY = 5
# Tie-break bonus for keeping a re-solved block at its previous start when
# a Controller reoptimizes with strategy='prefer_existing'. Deliberately
# far below the smallest scheduling gain (the objective is scaled x10 and
# priority coverage is weighted urgency-in-tenths*1000, so one tenth of a
# priority point is worth 1000), so it can never trade away a task,
# a deadline or any hard safety constraint - it only breaks exact ties
# toward the least-disruptive schedule.
RETENTION_BONUS = 50
logger = logging.getLogger(__name__)

# The daily planning period is a Controller-selected synthetic assumption,
# carried on the dataset as planning_period {start_minutes, end_minutes}.
# It is NOT hardcoded to the night: the legacy scenario is 00:00-06:00
# (0-360), a full-day scenario is 0-1440, and any start<end within a day is
# accepted. Datasets without the field (the legacy scenario builder) keep
# the legacy period so their saved plans validate exactly as before.
LEGACY_PERIOD = (0, 360)


def planning_period(data):
    p = (data or {}).get('planning_period') or {}
    try:
        start = int(p.get('start_minutes', LEGACY_PERIOD[0]))
        end = int(p.get('end_minutes', LEGACY_PERIOD[1]))
    except (TypeError, ValueError):
        raise ValueError('Planning period must be whole minutes of day.')
    if not 0 <= start < end <= 1440:
        raise ValueError('Planning period must satisfy 00:00 <= start < end <= 24:00.')
    return start, end


def period_label(start, end):
    return f'{start // 60:02d}:{start % 60:02d}\u2013{end // 60:02d}:{end % 60:02d}'


def compatible(a, b):
    if a['department']==b['department'] or a['section']!=b['section'] or a['line']!=b['line']:
        return False
    if a.get('zone') and any(t['power_required'] for t in [a,b]) and not (a['separation_confirmed'] and b['separation_confirmed']):
        return False
    if abs(a['km_start']-b['km_start'])>4 or a['dependencies'] or b['dependencies']:
        return False
    if abs(a['due_day']-b['due_day'])>7:
        return False
    kinds = {a['task_type'], b['task_type']}
    if {'Deep screening', 'Track circuit testing'} <= kinds:
        return False
    if 'Deep screening' in kinds and any(t['power_required'] for t in [a, b]):
        return a['separation_confirmed'] and b['separation_confirmed']
    if 'Rail welding' in kinds and any(t['power_required'] for t in [a, b]):
        return a['separation_confirmed'] and b['separation_confirmed']
    return abs(a['due_day']-b['due_day'])<=7


def bundle(tasks, joint=True):
    waiting = sorted(tasks, key=lambda t: (-t['priority'], t['id'])) if joint else sorted(tasks, key=lambda t: t['id'])
    predecessors = {p for t in tasks for p in t['dependencies']}
    groups = []
    while waiting:
        group = [waiting.pop(0)]
        if joint and group[0]['id'] not in predecessors:
            for t in list(waiting):
                if len(group)==3:
                    break
                if t['id'] not in predecessors and all(compatible(t, other) for other in group):
                    group.append(t)
                    waiting.remove(t)
        groups.append(group)
    return groups


def resources(block, tasks, data=None):
    demand = defaultdict(int)
    section = int(block['section'].split('-')[-1])
    demand['section:'+block['section']] = 1
    sec = next((s for s in (data or {}).get('sections', []) if s['id']==block['section']), None)
    demand['yard:'+str(sec['from_station'] if sec else section)] = 1
    demand['yard:'+str(sec['to_station'] if sec else section+1)] = 1
    for t in tasks:
        demand[t['department']] += t['crew']
        if t['machine']:
            demand[t['machine']] += 1
    return dict(demand)


def windows(data, section, duration, release, horizon, incidents):
    p_start, p_end = planning_period(data)
    occupied = [(m['start']-HEADWAY, m['end']+HEADWAY) for m in data['movements'] if m['section']==section and m['start']<horizon*1440]
    occupied += [(e['start']-HEADWAY, e['end']+HEADWAY) for e in incidents if e['kind'] in ('freight','delay') and e['section']==section]
    intervals = []
    for day in range(release, horizon):
        gaps = [(day*1440+p_start, day*1440+p_end)]
        for a, b in occupied:
            revised = []
            for lo, hi in gaps:
                if a>=hi or b<=lo:
                    revised.append((lo, hi))
                else:
                    if lo<a:
                        revised.append((lo, a))
                    if b<hi:
                        revised.append((b, hi))
            gaps = revised
        intervals.extend((lo, hi-duration) for lo, hi in gaps if hi-lo>=duration)
    return intervals


def window_report(data, section, horizon, incidents=None):
    """Measured candidate-window diagnostics for one section.

    Reports what the timetable actually leaves inside the selected
    synthetic planning period after listed movements and 5-minute
    headways. It never invents a gap: a zero or short longest window is a
    measured property of the selected references, not a solver failure.
    """
    p_start, p_end = planning_period(data)
    incidents = incidents or []
    occupied = [(m['start']-HEADWAY, m['end']+HEADWAY) for m in data['movements'] if m['section']==section and m['start']<horizon*1440]
    occupied += [(e['start']-HEADWAY, e['end']+HEADWAY) for e in incidents if e['kind'] in ('freight','delay') and e['section']==section]
    movement_count = sum(1 for m in data['movements'] if m['section']==section and -1440<m['start']<horizon*1440)
    gaps_by_day = []
    for day in range(horizon):
        gaps = [(day*1440+p_start, day*1440+p_end)]
        for a, b in occupied:
            revised = []
            for lo, hi in gaps:
                if a>=hi or b<=lo:
                    revised.append((lo, hi))
                else:
                    if lo<a:
                        revised.append((lo, a))
                    if b<hi:
                        revised.append((b, hi))
            gaps = revised
        gaps_by_day.append([dict(start=lo, end=hi, minutes=hi-lo) for lo, hi in gaps])
    flat = [g for day_gaps in gaps_by_day for g in day_gaps]
    return dict(section=section, movements_considered=movement_count,
                planning_period=dict(start_minutes=p_start, end_minutes=p_end, label=period_label(p_start, p_end)),
                initial_window_minutes_per_day=p_end-p_start,
                candidate_gaps=len(flat),
                longest_candidate_window_minutes=max((g['minutes'] for g in flat), default=0),
                total_candidate_minutes=sum(g['minutes'] for g in flat),
                gaps_by_day=gaps_by_day)


def selected_candidate_slot(data, block, config, group=None):
    """Describe the candidate gap used by a proposed block, including scenario closures."""
    if not data.get('version', '').startswith('mh-'):
        return None
    base = next((w for w in data.get('candidate_windows', [])
                 if w['section'] == block['section'] and w['start'] <= block['start']
                 and w['end'] >= block['end']), None)
    if not base:
        return None
    gaps = [(base['start'], base['end'])]
    scenario_conflicts = []
    for event in config.get('incidents', []):
        if event.get('kind') != 'freight' or event.get('section') != block['section']:
            continue
        a, b = event['start']-HEADWAY, event['end']+HEADWAY
        next_gaps = []
        for lo, hi in gaps:
            if b <= lo or a >= hi:
                next_gaps.append((lo, hi))
                continue
            if lo < a:
                next_gaps.append((lo, a))
            if b < hi:
                next_gaps.append((b, hi))
            if block['start'] < b and block['end'] > a:
                scenario_conflicts.append(event.get('task_id') or 'controller-added freight restriction')
        gaps = next_gaps
    chosen = next(((lo, hi) for lo, hi in gaps if lo <= block['start'] and hi >= block['end']), None)
    if chosen is None:
        return None
    lo, hi = chosen
    listed_conflicts = sorted({str(m.get('train_no') or m.get('service_id')) for m in data['movements']
                               if m['section'] == block['section']
                               and block['start'] < m['end']+HEADWAY
                               and block['end'] > m['start']-HEADWAY})
    day = block['start']//1440
    from datetime import timedelta
    local_date = (date.fromisoformat(config['start_date'])+timedelta(days=day)).isoformat()
    group = group or [next(x for x in data['tasks'] if x['id']==task_id) for task_id in block['tasks']]
    return dict(
        id=base.get('id'), date=local_date, section=block['section'],
        start=lo, end=hi, available_duration=hi-lo,
        allocated_start=block['start'], allocated_end=block['end'],
        allocated_duration=block['duration'],
        estimated_work_duration=max(t['duration'] for t in group),
        predicted_duration=max(t.get('predicted_duration', t['duration']) for t in group),
        uncertainty_buffer=max(t.get('duration_buffer', 0) for t in group),
        task_durations=[dict(id=t['id'],predicted=t.get('predicted_duration',t['duration']),
                             uncertainty_buffer=t.get('duration_buffer',0),required=t['duration']) for t in group],
        setup_minutes=SETUP, conflicting_trains=listed_conflicts,
        boundary_trains=base.get('boundary_trains', []),
        scenario_conflicts=scenario_conflicts,
        eligibility_conditions=[
            f"The complete estimated work plus {SETUP}-minute setup fits in this candidate gap.",
            'The request section and line match the selected route; the CP-SAT feasibility audit passed crew, equipment, power, dependency and compatibility constraints.',
            'Listed timetable movements and 5-minute headways are protected; conflicting listed trains: '+(', '.join(listed_conflicts) if listed_conflicts else 'none in the selected reference set')+'.',
            'Coverage is incomplete. This is a conditional candidate, not an authorized track possession or operating clearance.'
        ],
        provenance=base.get('provenance', 'Conditional candidate gap')
    )


def deferred_reason(group, block, domain, data, config):
    if not domain:
        report = window_report(data, block['section'], config['horizon'], config['incidents'])
        p_start, p_end = planning_period(data)
        return (f"No {period_label(p_start, p_end)} candidate gap on this section/date fits the full estimated work duration, model uncertainty buffer, "
                f"{SETUP}-minute setup and 5-minute timetable headways after selected disruptions. Measured: {report['movements_considered']} listed movements considered on {block['section']}; "
                f"{report['candidate_gaps']} candidate gaps remain over {config['horizon']} days and the longest is {report['longest_candidate_window_minutes']} minutes, "
                f"but this request group requires {block['duration']} minutes in a single gap (estimated work {block['duration']-SETUP} minutes including the model uncertainty buffer, plus {SETUP} minutes setup/handover). "
                'Feasible task-window pairs for this group: 0. This is a measured infeasibility under the selected incomplete timetable references, not a solver failure.') 
    demand = resources(block, group, data)
    unavailable = [f'{name} requires {amount}, capacity {data["resources"].get(name, 1)}'
                   for name, amount in demand.items() if amount > data['resources'].get(name, 1)]
    if unavailable:
        return 'Required resource capacity is unavailable: '+', '.join(unavailable)+'.'
    if data.get('version', '').startswith('mh-'):
        deadline = min((t['due_day']+1)*1440 for t in group)
        if not any(lo+block['duration'] <= deadline for lo, _ in domain):
            return 'All candidate gaps under the selected incomplete references would finish after the earliest simulated due date for this request group.'
    return ('CP-SAT deferred this request group after evaluating the feasible timetable slots: the remaining assignments conflict '
            'with the selected higher-priority schedule or shared section, crew, equipment, yard, dependency or compatibility constraints.')


def block_for(group, start=0, ident=None):
    return dict(id=ident or 'BLOCK-'+group[0]['id'].removeprefix('SIM-TASK-'), section=group[0]['section'], line=group[0]['line'], tasks=[t['id'] for t in group], departments=sorted({t['department'] for t in group}), duration=max(t['duration'] for t in group)+SETUP, start=start, end=start+max(t['duration'] for t in group)+SETUP, power=any(t['power_required'] for t in group), decision='Proposed')


def normalized_config(config, data):
    h = config.get('horizon', 7)
    if h not in [7, 30]:
        raise ValueError('Choose a 7-day or 30-day horizon.')
    try:
        start = date.fromisoformat(config['start_date']).isoformat()
    except (ValueError, KeyError, TypeError):
        raise ValueError('Choose a valid plan start date.')
    incidents = config.get('incidents', [])
    if not isinstance(incidents, list) or len(incidents)>20:
        raise ValueError('The scenario supports up to 20 disruptions.')
    for e in incidents:
        if e.get('kind')=='delay':
            continue  # derived from config.delays below; never trusted from input (saved plans carry them in config)
        if e.get('kind') not in ['freight', 'machine', 'urgent']:
            raise ValueError('Unknown disruption.')
        if e['kind']=='urgent':
            if e.get('task_id') not in {t['id'] for t in data['tasks']}:
                raise ValueError('Choose a scenario task.')
        else:
            if not all(type(e.get(k)) is int for k in ['start', 'end']) or not 0<=e['start']<e['end']<=h*1440:
                raise ValueError('Disruption must be inside the plan horizon.')
            if e['kind']=='freight' and e.get('section') not in {s['id'] for s in data['sections']}:
                raise ValueError('Unknown section.')
            if e['kind']=='machine' and e.get('machine') not in ['tamper', 'tower', 'welder']:
                raise ValueError('Unknown machine.')
    delays = config.get('delays') or []
    if data['version'].startswith('mh-'):
        if not isinstance(delays, list) or len(delays)>20:
            raise ValueError('A delay scenario supports up to 20 delayed movements.')
        for d in delays:
            if not isinstance(d, dict) or not isinstance(d.get('service_id'), str) or type(d.get('minutes')) is not int or not 0<=d['minutes']<=1440:
                raise ValueError('Each delay needs a timetable movement and whole minutes from 0 to 1440.')
            if 'propagate' in d and type(d['propagate']) is not bool:
                raise ValueError('Delay propagate must be true or false.')
        if 'disruptions_enabled' in config and type(config['disruptions_enabled']) is not bool:
            raise ValueError('disruptions_enabled must be true or false.')
        if config.get('disruptions_enabled') is False:
            # Simulated delays disabled by configuration: baseline inputs
            # only, and the stored plan config records that honestly.
            delays = []
            config = {**config, 'delays': []}
        # Bounded solver configuration (validated here, never trusted raw):
        _tl = config.get('solver_time_limit')
        if _tl is not None and (isinstance(_tl, bool) or not isinstance(_tl, (int, float)) or not 1<=_tl<=30):
            raise ValueError('Solver time limit must be between 1 and 30 seconds.')
        if type(config.get('solver_workers', 4)) is not int or not 1<=config.get('solver_workers', 4)<=8:
            raise ValueError('Solver workers must be a whole number between 1 and 8.')
        if type(config.get('solver_seed', 26092)) is not int or not 0<=config.get('solver_seed', 26092)<=999999999:
            raise ValueError('Solver seed must be a whole number between 0 and 999999999.')
        if config.get('strategy', 'prefer_existing') not in ('prefer_existing', 'full_reoptimize'):
            raise ValueError('Strategy must be prefer_existing or full_reoptimize.')
    merged = [e for e in incidents if e['kind'] not in ('machine','delay')]
    for machine in ['tamper', 'tower', 'welder']:
        ranges = sorted((e['start'], e['end']) for e in incidents if e['kind']=='machine' and e['machine']==machine)
        combined = []
        for lo, hi in ranges:
            if combined and lo<=combined[-1][1]:
                combined[-1][1] = max(hi, combined[-1][1])
            else:
                combined.append([lo, hi])
        merged += [dict(kind='machine', machine=machine, start=lo, end=hi) for lo, hi in combined]
    if data['version'].startswith('mh-'):
        # Delay incidents are derived here, never client-supplied: each one
        # is the REVISED interval of a delayed movement in the (already
        # delay-shifted) dataset, so retain/freeze, window and validation
        # logic treat a delayed train exactly like other disrupted traffic.
        # The server expands each delay (propagation included) into
        # data['delay_service_ids'] using delay.expand_service_ids on the
        # ORIGINAL movements, so incidents cover every shifted movement,
        # not just the directly delayed one.
        expanded = set(data.get('delay_service_ids') or [])
        for d in delays:
            expanded.add(d['service_id'])
        if expanded:
            for m in data['movements']:
                if m.get('service_id') is not None and m['service_id'] in expanded:
                    merged.append(dict(kind='delay', section=m['section'], start=m['start'], end=m['end'], service_id=m['service_id']))
    return dict(horizon=h, start_date=start, incidents=merged, **({k:config.get(k,v) for k,v in [('zone','Z1'),('corridor','all'),('section','all'),('traffic_mode','reference'),('resource_overrides',{}),('batch_offset',0),('priority_weights',dict(asset=15,safety=25,urgency=15,failure=25,overdue=10,operational=10)),('task_overrides',{}),('delays',[]),('twin_changes',[]),('planning_start',planning_period(data)[0]),('planning_end',planning_period(data)[1]),('solver_time_limit',None),('solver_workers',4),('solver_seed',26092),('strategy','prefer_existing'),('disruptions_enabled',True)]} if data['version'].startswith('mh-') else {}))


def effective_tasks(data, config):
    tasks = copy.deepcopy(data['tasks'])
    urgent = {e['task_id'] for e in config['incidents'] if e['kind']=='urgent'}
    for t in tasks:
        if t['id'] in urgent:
            t.update(release_day=0, due_day=0, emergency=True)
            if not data['version'].startswith('mh-'):t['priority']=100
    return tasks


def affected(block, incidents, lookup):
    for e in incidents:
        if e['kind']=='urgent' and e['task_id'] in block['tasks']:
            return True
        if e['kind'] in ('freight','delay') and block['section']==e['section'] and block['start']<e['end']+HEADWAY and block['end']>e['start']-HEADWAY:
            return True
        if e['kind']=='machine' and any(lookup[t]['machine']==e['machine'] for t in block['tasks']) and block['start']<e['end'] and block['end']>e['start']:
            return True
    return False


def solve(data, config, joint=True, frozen=None, preferences=None, task_subset=None, period=None, completed_predecessors=None, workers=4, time_limit=None, seed=26092, retention=None):
    horizon = config['horizon']
    tasks = effective_tasks(data, config)
    lookup = {t['id']: t for t in tasks}
    withheld = [t for t in tasks if t.get('safety_action')=='STOP_AND_REVIEW']
    eligible = [t for t in tasks if t not in withheld and t['release_day']<horizon and (task_subset is None or t['id'] in task_subset)]
    frozen = frozen or []
    fixed_ids = {t for b in frozen for t in b['tasks']}
    groups = [[lookup[t] for t in b['tasks']] for b in frozen]+bundle([t for t in eligible if t['id'] not in fixed_ids], joint)
    model = cp_model.CpModel()
    pools, variables, by_task, reward, unscheduled = defaultdict(list), [], {}, [], [dict(id=t['id'],reason='STOP_AND_REVIEW: simulated critical inspection evidence; urgent controller review required independently of score.') for t in withheld]
    candidate_intervals = 0
    section_stats = {}
    caps = data['resources']
    p_start, p_end = planning_period(data)
    slots_per_day = 6 if p_end<=360 else 24
    for i, group in enumerate(groups):
        b = block_for(group)
        domain = windows(data, b['section'], b['duration'], max(t['release_day'] for t in group), horizon, config['incidents'])
        if period is not None:
            lower, upper = period
            domain = [(max(lo, lower), min(hi, upper-b['duration'])) for lo, hi in domain
                      if max(lo, lower) <= min(hi, upper-b['duration'])]
        candidate_intervals += len(domain)
        stats = section_stats.setdefault(b['section'], dict(groups=0, feasible_groups=0, candidate_intervals=0, min_required_block_minutes=None))
        stats['groups'] += 1
        stats['candidate_intervals'] += len(domain)
        if domain:
            stats['feasible_groups'] += 1
        stats['min_required_block_minutes'] = b['duration'] if stats['min_required_block_minutes'] is None else min(stats['min_required_block_minutes'], b['duration'])
        if not domain:
            if i<len(frozen):
                raise ValueError('A retained block no longer fits its authorization. Generate a fresh plan.')
            reason=deferred_reason(group,b,domain,data,config)
            unscheduled += [dict(id=t['id'], reason=reason) for t in group]
            continue
        start = model.new_int_var_from_domain(cp_model.Domain.from_intervals(domain), f's{i}')
        end = model.new_int_var(0, horizon*1440, f'e{i}')
        present = model.new_bool_var(f'p{i}')
        interval = model.new_optional_interval_var(start, b['duration'], end, present, f'i{i}')
        if data['version'].startswith('mh-'):
            model.add(end <= min(t['due_day']+1 for t in group)*1440).only_enforce_if(present)
        if i<len(frozen):
            model.add(present==1)
            model.add(start==frozen[i]['start'])
        for resource, demand in resources(b, group, data).items():
            pools[resource].append((interval, demand))
        for t in group:
            by_task[t['id']] = (start, end, present)
        day = model.new_int_var(0, horizon-1, f'day{i}')
        model.add_division_equality(day, start, 1440)
        scheduled_day = model.new_int_var(0, horizon, f'sd{i}')
        model.add(scheduled_day==day).only_enforce_if(present)
        model.add(scheduled_day==0).only_enforce_if(present.Not())
        # Priority coverage dominates all soft costs. Forecast and feedback are bounded.
        # Priorities carry one decimal (Criticality Index, e.g. 68.4), so the
        # urgency sum is kept in TENTHS: int(t['priority']) would truncate
        # 68.4 and 68.0 to the same coefficient and let CP-SAT break a
        # contested-slot contest arbitrarily instead of highest-first.
        # Every other objective term below is scaled by the same factor of
        # 10, so all relative weights are exactly as before - only the
        # fractional priority resolution is new.
        urgency = sum(int(round(t['priority']*10))+(10000 if t.get('emergency') else 0) for t in group)
        section_index = int(b['section'].split('-')[-1])-1
        # Forecast-cost lookup over the start hours the selected planning
        # period can actually reach. The legacy period keeps the original
        # six-slot grid and bounds exactly; a wider period uses one slot
        # per hour so daytime starts are costed, never silently infeasible.
        hour = model.new_int_var(0, horizon*slots_per_day-1, f'hour{i}')
        within_day = model.new_int_var(0, p_end-1, f'within_day{i}')
        local_hour = model.new_int_var(0, (p_end-1)//60, f'local_hour{i}')
        model.add(within_day == start - day*1440)
        model.add_division_equality(local_hour, within_day, 60)
        model.add(hour == day*slots_per_day + local_hour)
        traffic_costs = [int(sum(data['forecasts'][slot//slots_per_day][section_index][slot%slots_per_day:min(24, slot%slots_per_day+(b['duration']+59)//60)])*100) for slot in range(horizon*slots_per_day)]
        traffic = model.new_int_var(0, max(traffic_costs+[0]), f'traffic{i}')
        model.add_element(hour, traffic_costs, traffic)
        traffic_active = model.new_int_var(0, max(traffic_costs+[0]), f'traffic_active{i}')
        model.add(traffic_active==traffic).only_enforce_if(present)
        model.add(traffic_active==0).only_enforce_if(present.Not())
        preference = (preferences or {}).get(b['section'], 0)
        base_reward = (urgency*1000-b['duration']*20-1000+int(preference*100)*10)*present-urgency*scheduled_day*12-traffic_active*10
        if retention:
            for entry in retention:
                if set(entry['tasks']) == {t['id'] for t in group}:
                    kept = model.new_bool_var(f'kept{i}')
                    model.add(start == entry['start']).only_enforce_if(kept)
                    model.add(start != entry['start']).only_enforce_if(kept.Not())
                    model.add_implication(kept, present)
                    base_reward = base_reward + RETENTION_BONUS*10*kept
                    break
        reward.append(base_reward)
        variables.append((b, group, start, end, present, i<len(frozen)))
    window_analysis = {}
    for section, stats in section_stats.items():
        report = window_report(data, section, horizon, config['incidents'])
        report.pop('gaps_by_day', None)
        window_analysis[section] = {**report, **stats}
    for resource, entries in pools.items():
        cap = caps.get(resource, 1)
        for n, event in enumerate(config['incidents']):
            if event['kind']=='machine' and event['machine']==resource:
                entries.append((model.new_fixed_size_interval_var(event['start'], event['end']-event['start'], f'outage-{resource}-{n}'), cap))
        model.add_cumulative([x[0] for x in entries], [x[1] for x in entries], cap)
    for task in eligible:
        if task['id'] not in by_task:
            continue
        start, _, present = by_task[task['id']]
        for predecessor in task['dependencies']:
            if predecessor not in by_task:
                if predecessor not in (completed_predecessors or set()):
                    model.add(present==0)
            else:
                _, previous_end, previous_present = by_task[predecessor]
                model.add(present<=previous_present)
                model.add(start>=previous_end).only_enforce_if(present)
    model.maximize(sum(reward))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit if time_limit is not None else (8 if horizon==7 else 14)
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed
    status = solver.solve(model)
    proto = model.proto
    diagnostics = dict(eligible_tasks=len(eligible), groups=len(groups), modeled_groups=len(variables),
                       candidate_intervals=candidate_intervals, variables=len(proto.variables),
                       constraints=len(proto.constraints), seed=seed, workers=workers,
                       time_limit_seconds=solver.parameters.max_time_in_seconds)
    logger.warning('CP-SAT horizon=%s joint=%s diagnostics=%s status=%s objective=%s bound=%s seconds=%.3f',
                   horizon, joint, diagnostics, solver.status_name(status),
                   solver.objective_value if status in (cp_model.FEASIBLE, cp_model.OPTIMAL) else None,
                   solver.best_objective_bound if status in (cp_model.FEASIBLE, cp_model.OPTIMAL, cp_model.UNKNOWN) else None,
                   solver.wall_time)
    blocks = []
    if status not in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        if frozen:
            if status == cp_model.INFEASIBLE:
                raise ValueError('Retained blocks conflict with the revised constraints. Generate a fresh plan for a full reschedule.')
            raise ValueError(f'CP-SAT returned {solver.status_name(status)}; retained-block feasibility was not established. Retry or generate a fresh plan.')
        return dict(blocks=[], unscheduled=[dict(id=t['id'], reason='Solver returned '+solver.status_name(status)) for t in eligible]+[dict(id=t['id'],reason='STOP_AND_REVIEW: simulated critical inspection evidence; urgent controller review required independently of score.') for t in withheld], solver_status=solver.status_name(status), seconds=round(solver.wall_time, 2), diagnostics=diagnostics, window_analysis=window_analysis)
    for b, group, start, end, present, fixed in variables:
        if solver.value(present):
            b.update(start=solver.value(start), end=solver.value(end), frozen=fixed)
            day = b['start']//1440
            expected = sum(data['forecasts'][day][int(b['section'].split('-')[-1])-1][b['start']%1440//60:min(24, (b['end']%1440+59)//60)])
            b['expected_freight'] = round(expected, 2)
            b['opportunity'] = round(max(0, min(100, 100-expected*12-b['duration']/30)), 1)
            b['candidate_slot']=selected_candidate_slot(data,b,config,group)
            score_summary=', '.join(f"{t['id']} {t['priority']}/100" for t in group)
            slot=b['candidate_slot']
            if slot:
                local_start=f"{slot['start']%1440//60:02d}:{slot['start']%60:02d}"
                local_end=f"{slot['end']%1440//60:02d}:{slot['end']%60:02d}"
                slot_summary=f"Candidate gap {slot['date']} {local_start}–{local_end} IST: {slot['available_duration']} minutes in simulated candidate period; {b['duration']} minutes proposed."
                conflict_summary=('No selected timetable train conflicts; 5-minute headways are protected.' if not slot['conflicting_trains'] else 'Conflicting selected timetable trains: '+', '.join(slot['conflicting_trains'])+'.')
            else:
                slot_summary='The block fits the solver candidate period; candidate metadata was unavailable.'
                conflict_summary='Listed train movements and headways were checked; unknown traffic remains unresolved.'
            b['reasons'] = [
                f"Priority-weighted CP-SAT selected this slot under the route, date, duration, deadline and resource constraints; request scores: {score_summary}.",
                f"{len(b['departments'])} department(s), {len(group)} tasks share one {b['duration']}-minute possession, including {SETUP} minutes setup/handover.",
                slot_summary,
                conflict_summary+' Unknown traffic and operating permissions remain unresolved.',
                'Power isolation and work separation reserved.' if b['power'] else 'Traffic possession; no power isolation required.',
                f"No observed freight forecast is active; the selected interval remains subject to incomplete traffic coverage (freight estimate {expected:.2f})."
            ]
            blocks.append(b)
        else:
            current_domain=windows(data,b['section'],b['duration'],max(t['release_day'] for t in group),horizon,config['incidents'])
            reason=deferred_reason(group,b,current_domain,data,config)
            unscheduled += [dict(id=t['id'], reason=reason) for t in group]
    logger.warning('CP-SAT allocations=%s deferred=%s', sum(len(b['tasks']) for b in blocks), len(unscheduled))
    return dict(blocks=sorted(blocks, key=lambda b: (b['start'], b['section'])), unscheduled=unscheduled, solver_status=solver.status_name(status), seconds=round(solver.wall_time, 2), objective=round(solver.objective_value), bound=round(solver.best_objective_bound), diagnostics=diagnostics, window_analysis=window_analysis)


def solve_rolling(data, config, joint=True, preferences=None):
    """Recover an UNKNOWN monthly solve with disjoint weekly CP-SAT periods.

    Each stage reserves its entire calendar period, so blocks from distinct
    stages cannot conflict over shared crews, equipment, yards or sections.
    Only already completed predecessors may cross period boundaries.
    """
    tasks = effective_tasks(data, config)
    eligible = [t for t in tasks if t['release_day'] < config['horizon'] and t.get('safety_action') != 'STOP_AND_REVIEW']
    done, blocks, stages = set(), [], []
    for first_day in range(0, config['horizon'], 7):
        end_day = min(first_day + 7, config['horizon'])
        remaining = sorted((t for t in eligible if t['id'] not in done and t['release_day'] < end_day), key=lambda t: (-t['priority'], t['id']))
        selected = {t['id'] for t in remaining[:100]}
        if not selected:
            continue
        result = solve(data, config, joint=joint, preferences=preferences, task_subset=selected,
                       period=(first_day*1440, end_day*1440), completed_predecessors=done, time_limit=5,
                       workers=config.get('solver_workers', 4), seed=config.get('solver_seed', 26092))
        if result['solver_status'] not in ('OPTIMAL', 'FEASIBLE'):
            raise ValueError(f"Rolling CP-SAT period {first_day+1}–{end_day} returned {result['solver_status']}; no feasible schedule was established.")
        blocks.extend(result['blocks'])
        done.update(tid for block in result['blocks'] for tid in block['tasks'])
        stages.append(dict(days=[first_day+1,end_day], status=result['solver_status'], scheduled=len(done), seconds=result['seconds'], objective=result.get('objective'), bound=result.get('bound'), diagnostics=result['diagnostics'], window_analysis=result.get('window_analysis', {})))
    unscheduled = [dict(id=t['id'], reason=('STOP_AND_REVIEW: simulated critical inspection evidence; urgent controller review required independently of score.' if t.get('safety_action')=='STOP_AND_REVIEW' else 'Not allocated in the sequential 100-request planning periods; review and replan.')) for t in tasks if t['release_day'] < config['horizon'] and t['id'] not in done]
    errors=validate(data,dict(config=config,blocks=blocks))
    if errors:
        raise ValueError('Rolling fallback failed the full-horizon feasibility audit: '+'; '.join(errors[:4]))
    return dict(blocks=sorted(blocks,key=lambda b:(b['start'],b['section'])), unscheduled=unscheduled,
                solver_status='ROLLING_FEASIBLE', seconds=round(sum(stage['seconds'] for stage in stages),2),
                objective=sum(s['objective'] for s in stages if s.get('objective') is not None), bound=None,
                diagnostics=dict(strategy='independently audited sequential periods; global solve returned UNKNOWN; no global optimality claim', stages=stages))


def validate(data, plan):
    """Audit outputs without relying on solver variables or its constraint code."""
    errors, seen, pool_events, assigned = [], set(), defaultdict(list), {}
    p_start, p_end = planning_period(data)
    config = normalized_config(plan['config'], data)
    lookup = {t['id']: t for t in effective_tasks(data, config)}
    block_ids = set()
    for b in plan['blocks']:
        if b.get('id') in block_ids:
            errors.append('Duplicate block ID.')
        block_ids.add(b.get('id'))
        if not b.get('tasks') or any(t not in lookup for t in b['tasks']):
            errors.append('Unknown or empty task bundle.')
            continue
        group = [lookup[t] for t in b['tasks']]
        if any(type(b.get(k)) is not int for k in ['start', 'end']):
            errors.append('Block times must be whole minutes.')
            continue
        if not 0<=b['start']<b['end']<=config['horizon']*1440:
            errors.append(b['id']+': outside horizon.')
        block_day = b['start']//1440
        if b['start'] < block_day*1440+p_start or b['end'] > block_day*1440+p_end:
            errors.append(b['id']+f": outside the conditional {period_label(p_start, p_end)} candidate planning period selected for this dataset.")
        if b.get('duration')!=b['end']-b['start'] or sorted(b.get('departments', []))!=sorted({t['department'] for t in group}):
            errors.append(b['id']+': inconsistent block metadata.')
        if b['end']-b['start']!=max(t['duration'] for t in group)+SETUP:
            errors.append(b['id']+': incorrect work duration or clearance.')
        slot=b.get('candidate_slot')
        if slot is not None:
            if (slot.get('section')!=b['section'] or slot.get('allocated_start')!=b['start']
                    or slot.get('allocated_end')!=b['end'] or slot.get('available_duration')!=slot.get('end',0)-slot.get('start',0)
                    or not slot.get('start',b['start'])<=b['start'] or not slot.get('end',b['end'])>=b['end']):
                errors.append(b['id']+': selected candidate slot metadata is inconsistent.')
            if slot.get('conflicting_trains'):
                errors.append(b['id']+': candidate slot lists a conflicting timetable train.')
            if not slot.get('eligibility_conditions'):
                errors.append(b['id']+': candidate slot has no eligibility conditions.')
        for t in group:
            if t.get('safety_action')=='STOP_AND_REVIEW':
                errors.append(t['id']+': critical evidence requires urgent review outside routine planning.')
            if data['version'].startswith('mh-') and b['end']>(t['due_day']+1)*1440:
                errors.append(t['id']+': deadline violated.')
            if t['id'] in seen:
                errors.append(t['id']+': duplicated task.')
            seen.add(t['id'])
            assigned[t['id']] = b
            sec = next(s for s in data['sections'] if s['id']==t['section'])
            if t['section']!=b['section'] or t['line']!=b['line'] or not sec['km_start']<=t['km_start']<t['km_end']<=sec['km_end'] or b['start']<t['release_day']*1440:
                errors.append(t['id']+': location or release violation.')
            if t['power_required'] and not b.get('power'):
                errors.append(t['id']+': missing power isolation.')
        if any(not compatible(a, c) for a, c in combinations(group, 2)):
            errors.append(b['id']+': incompatible activities.')
        for m in data['movements']:
            if m['section']==b['section'] and b['start']<m['end']+HEADWAY and b['end']>m['start']-HEADWAY:
                errors.append(b['id']+': train headway violation.')
                break
        for e in config['incidents']:
            if e['kind'] in ('freight','delay') and e['section']==b['section'] and b['start']<e['end']+HEADWAY and b['end']>e['start']-HEADWAY:
                errors.append(b['id']+': disrupted traffic window.')
        for resource, demand in resources(b, group, data).items():
            pool_events[resource] += [(b['start'], demand), (b['end'], -demand)]
    for e in config['incidents']:
        if e['kind']=='machine':
            cap = data['resources'][e['machine']]
            pool_events[e['machine']] += [(e['start'], cap), (e['end'], -cap)]
    for resource, events in pool_events.items():
        used = 0
        for _, delta in sorted(events):
            used += delta
            if used>data['resources'].get(resource, 1):
                errors.append(resource+': capacity exceeded.')
                break
    for tid, b in assigned.items():
        for parent in lookup[tid]['dependencies']:
            if parent not in assigned or assigned[parent]['end']>b['start']:
                errors.append(tid+': predecessor incomplete.')
    return sorted(set(errors))


def metrics(data, blocks, horizon, config=None):
    lookup = {t['id']: t for t in (effective_tasks(data, config) if config else data['tasks'])}
    tids = {t for b in blocks for t in b['tasks']}
    downtime = sum(b['end']-b['start'] for b in blocks)
    work = sum(max(lookup[t]['duration'] for t in b['tasks']) for b in blocks)
    availability = round(100*(1-downtime/(max(1,len(data['sections']))*horizon*1440)), 2)
    if blocks:
        availability_note = 'Availability is the share of all selected section-time (full 24-hour days) not consumed by a possession. It is not a measure of how much requested work was completed.'
    else:
        availability_note = 'No blocks were scheduled, so no possession was taken: the availability figure only means no track time was consumed. It does NOT mean the requested work was done; see the per-request deferral reasons and window_analysis for why nothing could be allocated.'
    return dict(tasks=len(tids), blocks=len(blocks), joint_blocks=sum(len({lookup[t]['department'] for t in b['tasks']})>1 for b in blocks), possession_minutes=downtime, availability=availability, availability_basis='100 = no possession minutes consumed, measured over selected sections x horizon x 1440 minutes (full 24-hour days, not only the selected candidate planning-period windows)', availability_note=availability_note, utilization=round(100*work/downtime, 1) if downtime else 0, planned_before_due=sum(b['end']<=(lookup[t]['due_day']+1)*1440 for b in blocks for t in b['tasks']), protected_train_conflicts=0, average_block_minutes=round(downtime/len(blocks), 1) if blocks else 0)


def generate(data, raw_config, previous=None, preferences=None):
    config = normalized_config(raw_config, data)
    lookup = {t['id']: t for t in data['tasks']}
    frozen = []
    retention = []
    strategy = config.get('strategy', 'prefer_existing')
    if previous and strategy != 'full_reoptimize':
        if previous['config']['horizon']!=config['horizon'] or previous['config']['start_date']!=config['start_date']:
            raise ValueError('Re-optimization keeps the existing dates and horizon.')
        affected_ids = {t for b in previous['blocks'] if affected(b, config['incidents'], lookup) for t in b['tasks']}
        # Twin scenario changes alter a task's risk/duration, so its block
        # cannot stay frozen at the old timing: re-solve it (the retention
        # bonus still rewards keeping the previous start when feasible).
        affected_ids |= {t['id'] for t in data['tasks'] if t.get('twin_modified')}
        while True:
            expanded = affected_ids | {t['id'] for t in data['tasks'] if set(t['dependencies']) & affected_ids}
            if expanded==affected_ids:
                break
            affected_ids=expanded
        frozen = [b for b in previous['blocks'] if not set(b['tasks']) & affected_ids]
        # Affected blocks are re-solved, but keeping their previous start
        # earns the small RETENTION_BONUS tie-break inside solve().
        retention = [dict(tasks=list(b['tasks']), start=b['start']) for b in previous['blocks'] if set(b['tasks']) & affected_ids]
    elif previous and strategy == 'full_reoptimize':
        if previous['config']['horizon']!=config['horizon'] or previous['config']['start_date']!=config['start_date']:
            raise ValueError('Re-optimization keeps the existing dates and horizon.')
    settings = dict(workers=config.get('solver_workers', 4), time_limit=config.get('solver_time_limit'), seed=config.get('solver_seed', 26092))
    optimized = solve(data, config, frozen=frozen, preferences=preferences, retention=retention, **settings)
    if optimized['solver_status'] == 'UNKNOWN' and config['horizon'] == 30 and not frozen:
        optimized = solve_rolling(data, config, preferences=preferences)
    if optimized['solver_status'] not in ('FEASIBLE', 'OPTIMAL', 'ROLLING_FEASIBLE'):
        raise ValueError(f"CP-SAT returned {optimized['solver_status']}; no feasible schedule was established. Retry or reduce the planning batch.")
    baseline = solve(data, config, joint=False, **settings)
    if baseline['solver_status'] == 'UNKNOWN' and config['horizon'] == 30:
        baseline = solve_rolling(data, config, joint=False)
    if baseline['solver_status'] not in ('FEASIBLE', 'OPTIMAL', 'ROLLING_FEASIBLE'):
        raise ValueError(f"Comparison solver returned {baseline['solver_status']}; no feasible comparison was established. Retry or reduce the planning batch.")
    plan = dict(config=config, **optimized, scenario=data['version'])
    errors = validate(data, plan)
    baseline_errors = validate(data, dict(config=config, blocks=baseline['blocks']))
    if errors or baseline_errors:
        raise ValueError('Feasibility audit failed: '+'; '.join((errors+baseline_errors)[:4]))
    opt_ids = {t for b in optimized['blocks'] for t in b['tasks']}
    base_ids = {t for b in baseline['blocks'] for t in b['tasks']}
    # A matched-work comparison includes only entire bundles served in both plans.
    comparable = [b for b in optimized['blocks'] if set(b['tasks'])<=base_ids]
    common = {t for b in comparable for t in b['tasks']}
    base_common = [b for b in baseline['blocks'] if set(b['tasks'])<=common]
    base_minutes = sum(b['duration'] for b in base_common)
    optimized_minutes = sum(b['duration'] for b in comparable)
    plan['metrics'] = metrics(data, optimized['blocks'], config['horizon'], config)
    plan['warnings'] = []
    if not optimized['blocks']:
        for section, analysis in optimized.get('window_analysis', {}).items():
            if not analysis.get('feasible_groups'):
                plan['warnings'].append(f"No allocation on {section}: the longest candidate window is {analysis['longest_candidate_window_minutes']} minutes but the smallest request group needs {analysis['min_required_block_minutes']} minutes in a single gap ({analysis['movements_considered']} listed movements considered, {analysis['candidate_gaps']} candidate gaps remain). Zero allocations is the measured outcome under the selected timetable references, not a solver error.")
    if optimized['solver_status'] == 'FEASIBLE':
        plan['warnings'].append('CP-SAT stopped at its time limit with a feasible schedule; global optimality is not claimed.')
    plan['baseline'] = dict(**metrics(data, baseline['blocks'], config['horizon'], config), solver_status=baseline['solver_status'], blocks_detail=baseline['blocks'], diagnostics=baseline.get('diagnostics', {}))
    plan['comparison'] = dict(tasks=len(common), baseline_minutes=base_minutes, optimized_minutes=optimized_minutes, minutes_saved=base_minutes-optimized_minutes, reduction=round(100*(base_minutes-optimized_minutes)/base_minutes, 1) if base_minutes else 0, note='Same retained tasks in both feasible schedules. Baseline uses separate department blocks with the same solver, limits and horizon; it is not a measured human plan.')
    if optimized.get('diagnostics', {}).get('strategy') or baseline.get('diagnostics', {}).get('strategy'):
        plan['comparison']['note'] += ' Sequential period recovery was used; the combined schedule is feasible, with no claim of global optimality.'
    plan['validation'] = dict(passed=True, violations=0, checked_blocks=len(plan['blocks']), checks=['Authorization and duration', 'Pairwise compatibility', 'Power isolation', 'Passenger and freight headway', 'Crew and machine capacity', 'Adjacent yard exclusion', 'Release times and precedence'])
    plan['preference_adjustments'] = preferences or {}
    plan['changes'] = []
    if previous:
        old = {t: b for b in previous['blocks'] for t in b['tasks']}
        new = {t: b for b in plan['blocks'] for t in b['tasks']}
        for tid in sorted(set(old)|set(new)):
            a, b = old.get(tid), new.get(tid)
            change = 'Retained' if a and b and a['start']==b['start'] and a['end']==b['end'] else 'Moved' if a and b else 'Newly scheduled' if b else 'Deferred'
            plan['changes'].append(dict(task_id=tid, change=change, old_start=a['start'] if a else None, new_start=b['start'] if b else None))
    return plan
