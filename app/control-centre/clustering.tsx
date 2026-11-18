'use client';
import { useEffect, useState } from 'react';
import { GitBranch, Info, Layers3, Loader2 } from 'lucide-react';
import type { Dataset, Plan, PlanningConfig } from '@/lib/planner-types';
import type { LucideIcon } from 'lucide-react';

async function request<T>(body?: unknown, url = '/api/planner') { const res = await fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { cache: 'no-store' }); const d: unknown = await res.json(); if (!res.ok) throw new Error(d && typeof d === 'object' && 'error' in d && typeof d.error === 'string' ? d.error : 'Unable to complete the request.'); return d as T }
function Stat({ label, value, note, icon: Icon }: { label: string; value: string | number; note: string; icon?: LucideIcon }) { return <div className="cc-stat"><span>{label}{Icon && <Icon size={17} />}</span><strong>{value}</strong><small>{note}</small></div> }

type ClusterGroup = { section: string; day: number; date: string | null; task_ids: string[]; window_id: string; longest_task_minutes: number };
type ClusterReport = {
  groups: ClusterGroup[];
  summary: { suggested_groups: number; tasks_in_clusters: number; eligible_tasks: number; bundles: number; cluster_bundles: number; clustered_tasks: number };
  per_section: { section: string; name: string; suggested: number; cluster_bundles: number }[];
  cluster_bundle_tasks: string[][];
  realized: { plan_blocks: number; joint_blocks: number; cluster_flagged: number; cluster_shaped_blocks: number } | null;
};

export default function Clustering({ dataset, plan, scope }: { dataset: Dataset; plan: Plan | null; scope: Omit<PlanningConfig, 'incidents'> }) {
  const [report, setReport] = useState<ClusterReport | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    request<ClusterReport>({ action: 'clustering', config: { ...scope }, blocks: plan?.blocks || [] })
      .then(r => { if (live) { setReport(r); setError(''); } })
      .catch(e => { if (live) setError((e as Error).message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [scope, plan]);
  const sectionName = (id: string) => dataset.sections.find(s => s.id === id)?.name || id;
  const maxSection = report ? Math.max(1, ...report.per_section.map(r => r.suggested)) : 1;
  const bundledIn = (g: ClusterGroup) => !!report && report.cluster_bundle_tasks.some(b => b.every(id => g.task_ids.includes(id)));
  const realizedIn = (g: ClusterGroup) => !!plan && plan.blocks.some(b => b.tasks.length >= 2 && b.tasks.every(id => g.task_ids.includes(id)));
  return <>
    <section className="panel controller-data-note"><b>Clustering now feeds the solver</b><p>A cluster is a set of maintenance tasks on one section that can share a single candidate window on the same day (hypergraph grouping). When CP-SAT plans, these clusters are bundled <b>first</b> — under the same compatibility rules as the usual priority pass (different departments, nearby chainage, no dependencies, at most 3 tasks) — and CP-SAT still verifies every block against timetable, resource and safety constraints before proposing it. Groups that cannot pair genuinely fall back to the normal pass untouched.</p></section>
    {error && <div className="error" role="alert">{error}</div>}
    {loading && !report ? <div className="panel cc-empty"><Loader2 className="spin" />Loading clustering…</div> : !report ? <div className="panel cc-empty"><p>Clustering data is unavailable.</p></div> : <>
      <div className="cc-stats four">
        <Stat label="Suggested clusters" value={report.summary.suggested_groups} note={`${report.summary.tasks_in_clusters} tasks share a section-day window`} icon={Layers3} />
        <Stat label="Cluster bundles for CP-SAT" value={report.summary.cluster_bundles} note={`of ${report.summary.bundles} bundles formed from ${report.summary.eligible_tasks} eligible tasks`} icon={GitBranch} />
        <Stat label="Tasks clustered" value={report.summary.clustered_tasks} note="tasks CP-SAT receives as joint cluster blocks" icon={Layers3} />
        <Stat label="In current plan" value={report.realized ? report.realized.cluster_shaped_blocks : '—'} note={report.realized ? `of ${report.realized.joint_blocks} joint blocks match a cluster (${report.realized.cluster_flagged} flagged by the solver)` : 'Generate a plan to see which clusters it realizes'} icon={GitBranch} />
      </div>
      <section className="panel">
        <div className="section-header"><div><h2>Clusters by section</h2><p>Suggested hypergraph clusters vs the bundles CP-SAT will actually receive, per section in this scope.</p></div><span className="pill gray">Live from the planning engine</span></div>
        {report.per_section.length ? <>
          <div className="cluster-legend"><span><i />Suggested clusters</span><span><i className="cluster" />Bundles formed for CP-SAT</span></div>
          <div className="cluster-chart">{report.per_section.map(r => <div className="cluster-bar-row" key={r.section}><div className="cluster-section"><b>{r.section}</b><span>{r.name}</span></div><div className="cluster-bars"><div className="cluster-bar" title={`${r.suggested} suggested clusters on ${r.section}`}><i style={{ width: `${Math.max(4, r.suggested / maxSection * 100)}%` }} /><em>{r.suggested}</em></div><div className="cluster-bar cluster" title={`${r.cluster_bundles} cluster bundles formed on ${r.section}`}><i style={{ width: `${Math.max(4, r.cluster_bundles / maxSection * 100)}%` }} /><em>{r.cluster_bundles}</em></div></div></div>)}</div>
        </> : <p className="metric-note cluster-empty">No clusters in this scope: no section-day window can host two or more current tasks together. This is data-dependent — short windows (for example the separate synthetic timetable) legitimately produce zero clusters.</p>}
      </section>
      <section className="panel">
        <div className="section-header"><div><h2>Suggested clusters</h2><p>Each row is one section-day hyperedge: tasks that fit one candidate window together, longest task first. Status shows whether the solver bundles the group and whether the current plan realized it.</p></div><span className="pill amber">Simulation · conditional</span></div>
        <div className="compact-scroll"><table className="zone-queue-table"><thead><tr><th>Section</th><th>Date</th><th>Tasks</th><th>Longest task</th><th>Window</th><th>Status</th></tr></thead><tbody>
          {report.groups.map(g => <tr key={g.section + '|' + g.day + '|' + g.window_id}><td><b>{g.section}</b><small>{sectionName(g.section)}</small></td><td>{g.date || `Day ${g.day + 1}`}</td><td><b>{g.task_ids.length} tasks</b><small>{g.task_ids.map(id => { const t = dataset.tasks.find(x => x.id === id); return t ? `${id} · ${t.task_type}` : id; }).join('; ')}</small></td><td>{g.longest_task_minutes} min<small>+ 10 min setup</small></td><td>{g.window_id}</td><td>{realizedIn(g) ? <span className="pill green">In current plan</span> : bundledIn(g) ? <span className="pill amber">Bundled for CP-SAT</span> : <span className="pill gray">Suggested only</span>}</td></tr>)}
        </tbody></table>{!report.groups.length && <p className="metric-note">No suggested clusters for this selection.</p>}</div>
        <p className="metric-note"><Info size={13} /> A cluster never forces a block: if CP-SAT cannot place the joint block inside a valid window with crews available, the tasks defer like any other work. STOP_AND_REVIEW tasks are excluded from clustering entirely.</p>
      </section>
    </>}
  </>;
}
