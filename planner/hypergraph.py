"""Hypergraph task grouping for RailBlock AI.

A hyperedge joins three or more (here: two or more) maintenance tasks that
share a section and a day on which a single candidate window can host them
together. Joint blocks in this system run tasks in parallel, so a window
hosts a group when it fits the group's longest task plus setup.

These groups steer bundle formation: engine.solve() bundles clustered
tasks first, under the same compatibility rules as its greedy pass. They
are never executed directly: CP-SAT (planner.engine) remains the only
authority that checks safety, resources, dependencies and timetable
constraints before a block is proposed.
"""
from collections import defaultdict

SETUP_MINUTES = 10


def _windows_by_section(dataset):
    windows = defaultdict(list)
    for w in dataset.get('candidate_windows', []):
        windows[w['section']].append(w)
    return windows


def _fits(task, window):
    need = task['duration'] + SETUP_MINUTES
    return (window['end'] - window['start'] >= need
            and task['release_day'] <= window['day'] <= task['due_day'] + 6)


def suggested_groups(dataset):
    """Return hypergraph group suggestions for a store dataset.

    Each suggestion: {section, day, date, task_ids, window_id,
    longest_task_minutes}. STOP_AND_REVIEW tasks are excluded — they are
    withheld from routine allocation independently of grouping.
    """
    windows = _windows_by_section(dataset)
    tasks = [t for t in dataset.get('tasks', [])
             if t.get('safety_action') != 'STOP_AND_REVIEW']
    by_id = {t['id']: t for t in tasks}

    edges = defaultdict(list)
    for t in tasks:
        seen_days = set()
        for w in windows.get(t['section'], []):
            if w['day'] in seen_days:
                continue
            if _fits(t, w):
                edges[(t['section'], w['day'])].append(t['id'])
                seen_days.add(w['day'])

    groups = []
    for (section, day), ids in sorted(edges.items()):
        if len(ids) < 2:
            continue
        longest = max(by_id[i]['duration'] for i in ids)
        host = None
        for w in windows.get(section, []):
            if w['day'] == day and w['end'] - w['start'] >= longest + SETUP_MINUTES:
                host = w
                break
        if host is None:
            continue
        groups.append(dict(section=section, day=day, date=host.get('date'),
                           task_ids=sorted(ids), window_id=host['id'],
                           longest_task_minutes=longest))
    return groups
