"""Hypergraph grouping prototype for RailBlock AI.

A hyperedge = a set of tasks that share a section AND at least one common
candidate window (same day), i.e. tasks that could in principle be executed
in one joint block. CP-SAT remains the verifier: we compare the suggested
groups against the joint blocks the solver actually formed on the same
batch, and count suggested groups the solver did NOT form (missed bundling
candidates a controller could review).
"""
import sys, json
from collections import defaultdict
sys.path.insert(0, '/home/hatch/workspace/railblock_audit/proj9')
from planner.maharashtra import store
from planner import engine

ds = store.dataset({})
tasks = [t for t in ds['tasks'] if t.get('safety_action') != 'STOP_AND_REVIEW']
by_id = {t['id']: t for t in tasks}
from collections import defaultdict as _dd
windows = _dd(list)  # section -> candidate windows
for w in ds['candidate_windows']:
    windows[w['section']].append(w)

# feasible days per task: days where some candidate window on its section fits
# duration + buffer + setup (store already encodes block length in duration)
def feasible_days(t):
    out = set()
    for w in windows.get(t['section'], []):
        need = t['duration'] + 10
        if w['end'] - w['start'] >= need and t['release_day'] <= w['day'] <= t['due_day'] + 6:
            out.add(w['day'])
    return out

fd = {t['id']: feasible_days(t) for t in tasks}

# hyperedges: (section, day) -> tasks that can all use a window that day.
# Refine: within a day, tasks form one hyperedge per section (they share the
# section's windows); a stricter edge requires a SINGLE window fitting the
# SUM of durations (true joint-block feasibility ignoring resources).
hyper = defaultdict(list)
for t in tasks:
    for d in fd[t['id']]:
        hyper[(t['section'], d)].append(t['id'])

multi = {k: v for k, v in hyper.items() if len(v) >= 2}
covered = set()
for v in multi.values():
    covered.update(v)
print(f'batch tasks (excl STOP_AND_REVIEW): {len(tasks)}')
print(f'hyperedges (section, day) with >=2 tasks: {len(multi)}')
print(f'tasks covered by >=1 multi-task hyperedge: {len(covered)}')

# stricter: single window fits the group the way CP-SAT builds joint blocks
# (tasks run in parallel inside a block: max duration + setup)
strict = 0
for (sec, d), ids in multi.items():
    total = max(by_id[i]['duration'] for i in ids) + 10
    if any(w['day'] == d and w['end'] - w['start'] >= total for w in windows.get(sec, [])):
        strict += 1
print(f'hyperedges where ONE window fits the whole group (parallel, max+setup): {strict}')

# what CP-SAT actually does on the same dataset
cfg = dict(ds['selection'], incidents=[])
plan = engine.solve(ds, cfg)
blocks = plan.get('blocks', [])
joint = [b for b in blocks if len(b.get('tasks', [])) >= 2]
print(f'CP-SAT: {len(blocks)} blocks, {len(joint)} joint blocks, '
      f'{sum(len(b["tasks"]) for b in joint)} tasks in joint blocks')

# solver joint groups vs hypergraph suggestions (same section+day)
solver_groups = set()
for b in joint:
    solver_groups.add((b['section'], b['start'] // 1440))
suggested = set(multi.keys())
print(f'section-days where solver formed a joint block: {len(solver_groups)}')
print(f'of those, also suggested by hypergraph: {len(solver_groups & suggested)}')
print(f'hypergraph suggestions with no solver joint block: {len(suggested - solver_groups)}')
json.dump(dict(batch=len(tasks), hyperedges=len(multi), covered=len(covered),
               strict=strict, cpsat_blocks=len(blocks), cpsat_joint=len(joint),
               solver_groups=len(solver_groups),
               overlap=len(solver_groups & suggested),
               missed=len(suggested - solver_groups)),
          open('/home/hatch/workspace/gnn_experiment/hypergraph_results.json', 'w'), indent=1)
