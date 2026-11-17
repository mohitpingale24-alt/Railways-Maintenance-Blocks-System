'use client';
import type {Dataset, Plan, PlanningConfig} from '@/lib/planner-types';

/* Shared scheduling configuration + plan comparison for the dynamic
   scheduling tabs (Train Delay & Rescheduling, Railway Digital Twin).
   Every value is validated server-side in planner/engine.py
   normalized_config: time limit 1-30s, workers 1-8, seed 0-999999999,
   strategy prefer_existing | full_reoptimize, disruptions boolean.
   The settings are stored in the plan config and bound into its
   freshness fingerprint by planner/server.py. */

export type SolverSettings = {
  timeLimit: number | null; // null = engine default (8s weekly / 14s monthly)
  workers: number;
  seed: number;
  strategy: 'prefer_existing' | 'full_reoptimize';
  disruptions: boolean;
};

export const defaultSolverSettings: SolverSettings = {
  timeLimit: null, workers: 4, seed: 26092, strategy: 'prefer_existing', disruptions: true,
};

export function solverConfigFields(s: SolverSettings): Partial<PlanningConfig> {
  return {
    solver_time_limit: s.timeLimit, solver_workers: s.workers, solver_seed: s.seed,
    strategy: s.strategy, disruptions_enabled: s.disruptions,
  } as Partial<PlanningConfig>;
}

function Pick({label, value, onChange, options}: {label: string; value: string; onChange: (v: string) => void; options: {value: string; label: string}[]}) {
  return <label className="pick"><span>{label}</span><select aria-label={label} value={value} onChange={e => onChange(e.target.value)}>{options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>;
}

export function SolverConfigPanel({value, onChange, disabled}: {value: SolverSettings; onChange: (v: SolverSettings) => void; disabled?: boolean}) {
  const set = (patch: Partial<SolverSettings>) => onChange({...value, ...patch});
  return <section className="panel">
    <div className="section-header"><div><h2>Scheduling configuration</h2><p>Bounded CP-SAT settings, validated by the planner and stored with every generated plan. Prefer-existing keeps unaffected blocks frozen and gives re-solved blocks a small tie-break bonus for keeping their previous start; it never overrides safety, deadlines or priority coverage.</p></div></div>
    <div className="delay-controls">
      <Pick label="Solver time limit" value={value.timeLimit === null ? 'default' : String(value.timeLimit)} onChange={v => set({timeLimit: v === 'default' ? null : Number(v)})} options={[{value: 'default', label: 'Default (8s weekly / 14s monthly)'}, {value: '4', label: '4 seconds'}, {value: '8', label: '8 seconds'}, {value: '15', label: '15 seconds'}, {value: '30', label: '30 seconds'}]} />
      <Pick label="Solver workers (cap 8)" value={String(value.workers)} onChange={v => set({workers: Number(v)})} options={[{value: '1', label: '1 (deterministic)'}, {value: '2', label: '2'}, {value: '4', label: '4'}, {value: '8', label: '8'}]} />
      <label className="pick"><span>Solver seed</span><input aria-label="Solver seed" type="number" min={0} max={999999999} step={1} value={value.seed} disabled={disabled} onChange={e => set({seed: Math.max(0, Math.min(999999999, Number(e.target.value) || 0))})} /></label>
      <Pick label="Rescheduling strategy" value={value.strategy} onChange={v => set({strategy: v as SolverSettings['strategy']})} options={[{value: 'prefer_existing', label: 'Prefer existing blocks'}, {value: 'full_reoptimize', label: 'Full reoptimization'}]} />
      <label className="twin-check"><input type="checkbox" checked={value.disruptions} disabled={disabled} onChange={e => set({disruptions: e.target.checked})} /> Simulated disruptions enabled</label>
    </div>
    <p className="delay-note">Planning period, horizon, corridor, section, department, crew/resource availability and Criticality weights come from the Controller toolbar scope and are already bound into the dataset fingerprint. A busy planner answers 409 - requests are rejected, never queued. No real-time guarantee is claimed; solver seconds are reported on every plan.</p>
  </section>;
}

const fmt = (mins: number) => {
  const day = Math.floor(mins / 1440), m = ((mins % 1440) + 1440) % 1440;
  const label = `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  return day > 0 ? `+${day}d ${label}` : label;
};

/* Before/after plan comparison computed entirely from the two plan
   payloads and the dataset's task priorities - nothing hardcoded. */
export function PlanCompare({baseline, revised, dataset}: {baseline: Plan | null; revised: Plan | null; dataset: Dataset}) {
  if (!baseline || !revised) return null;
  const before = new Map<string, {start: number; end: number; section: string}>();
  (baseline.blocks || []).forEach(b => b.tasks.forEach(id => before.set(id, b)));
  const after = new Map<string, {start: number; end: number; section: string}>();
  (revised.blocks || []).forEach(b => b.tasks.forEach(id => after.set(id, b)));
  const kept: string[] = [], moved: {id: string; from: number; to: number}[] = [], added: string[] = [];
  after.forEach((b, id) => {
    const a = before.get(id);
    if (!a) added.push(id);
    else if (a.start === b.start && a.section === b.section) kept.push(id);
    else moved.push({id, from: a.start, to: b.start});
  });
  const reasons = new Map((revised.unscheduled || []).map(u => [u.id, u.reason]));
  const deferred = [...before.keys()].filter(id => !after.has(id));
  const priorityOf = (id: string) => dataset.tasks.find(t => t.id === id)?.priority ?? 0;
  const occupancy = (p: Plan) => (p.blocks || []).reduce((n, b) => n + b.duration, 0);
  const criticality = (ids: Iterable<string>) => [...ids].reduce((n, id) => n + priorityOf(id), 0);
  const occBefore = occupancy(baseline), occAfter = occupancy(revised);
  const critBefore = criticality(before.keys()), critAfter = criticality(after.keys());
  return <section className="panel">
    <div className="section-header"><div><h2>Schedule comparison · {baseline.id || 'baseline'} → {revised.id || 'revised draft'}</h2><p>Computed from the two plan payloads. Solver status and objective are reported exactly as returned - a time-limited FEASIBLE result is never labelled optimal.</p></div><span className="pill gray">{revised.solver_status}</span></div>
    <div className="delay-stats">
      <div className="delay-stat"><span>Tasks kept in place</span><b>{kept.length}</b></div>
      <div className="delay-stat"><span>Tasks moved</span><b>{moved.length}</b></div>
      <div className="delay-stat"><span>Newly scheduled</span><b>{added.length}</b></div>
      <div className="delay-stat"><span>Deferred</span><b>{deferred.length}</b></div>
      <div className="delay-stat"><span>Block occupancy</span><b>{occBefore} → {occAfter} min</b><small>{occAfter - occBefore >= 0 ? '+' : ''}{occAfter - occBefore} min</small></div>
      <div className="delay-stat"><span>Scheduled criticality</span><b>{critBefore.toFixed(1)} → {critAfter.toFixed(1)}</b><small>{critAfter - critBefore >= 0 ? '+' : ''}{(critAfter - critBefore).toFixed(1)} pts</small></div>
      <div className="delay-stat"><span>Solver objective (revised)</span><b>{revised.objective ?? '—'}</b><small>bound {revised.bound ?? '—'} · {revised.seconds}s · {revised.diagnostics?.workers ?? revised.config?.solver_workers ?? '—'} workers · seed {revised.diagnostics?.seed ?? revised.config?.solver_seed ?? '—'}</small></div>
      <div className="delay-stat"><span>Feasibility audit</span><b>{revised.validation?.passed ? 'Passed' : 'See plan'}</b><small>{revised.validation ? `${revised.validation.checked_blocks} blocks checked` : ''}</small></div>
    </div>
    {!!moved.length && <ul className="delay-changes">{moved.slice(0, 12).map(m => <li key={m.id}><b>{m.id}</b> moved {fmt(m.from)} → {fmt(m.to)} <small>criticality {priorityOf(m.id)}/100</small></li>)}</ul>}
    {!!deferred.length && <ul className="delay-changes">{deferred.slice(0, 12).map(id => <li key={id}><b>{id}</b> deferred <small>{reasons.get(id) || 'No longer scheduled in the revised draft.'}</small></li>)}</ul>}
    {!!added.length && <p className="delay-note">Newly scheduled: {added.slice(0, 12).join(', ')}{added.length > 12 ? ` … +${added.length - 12} more` : ''}</p>}
  </section>;
}
