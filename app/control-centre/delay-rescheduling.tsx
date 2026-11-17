'use client';
import { useEffect, useRef, useState } from 'react';
import { ClipboardList, GitBranch, Info, Loader2, TrainFront, X, Zap } from 'lucide-react';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import { clockMinute, timelineTime } from '@/lib/planner-time';

import type {Block,Plan,Dataset,PlanningConfig,Movement,Gap,DelayAnalysis} from '@/lib/planner-types';
import {PlanCompare,SolverConfigPanel,defaultSolverSettings,solverConfigFields,type SolverSettings} from './scheduling-config';
import type {LucideIcon} from 'lucide-react';

const minute = clockMinute;
const dayDate = (start: string, day: number) => new Date(new Date(start + 'T00:00:00+05:30').getTime() + day * 86400000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' });
function Pick({id, value, onChange, options, label }: { id?:string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; label: string }) { return <Select value={value} onValueChange={v => v !== null && onChange(v)} items={options}><SelectTrigger id={id} className="pick" aria-label={label}><SelectValue /></SelectTrigger><SelectContent>{options.map(o => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent></Select> }
function Stat({ label, value, note, icon: Icon }: { label: string; value: string | number; note: string; icon?: LucideIcon }) { return <div className="cc-stat"><span>{label}{Icon && <Icon size={17} />}</span><strong>{value}</strong><small>{note}</small></div> }
async function request<T>(body?: unknown, url = '/api/planner') { const res = await fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { cache: 'no-store' }); const d:unknown = await res.json(); if (!res.ok) throw new Error(d&&typeof d==='object'&&'error' in d&&typeof d.error==='string'?d.error: 'Unable to complete the request.'); return d as T }

function DelayDayPanel({ title, startDate, dayN, section, movements, gaps, blocks, dataset }: { title: string; startDate: string; dayN: number; section: string; movements: Movement[]; gaps: Gap[]; blocks: Block[]; dataset: Dataset }) {
 const offset = dayN * 1440, span = 1440;
 const rect = (start: number, end: number) => ({ left: `${Math.max(0, (start - offset) / span * 100)}%`, width: `${Math.max(.35, (Math.min(offset + span, end) - Math.max(offset, start)) / span * 100)}%` });
 const visible = (m: {start:number;end:number}) => m.start < offset + span && m.end > offset;
 const moves = movements.filter(m => m.section === section && visible(m));
 return <section className="panel timeline-panel"><div className="section-header"><div><h2>{title}</h2><p>{section} · {dayDate(startDate, dayN)} · IST · hover for times</p></div></div><div className="timeline-scroll"><div className="timeline-inner"><div className="timeline-ruler"><span /><div>{Array.from({ length: 9 }, (_, h) => <span key={h}>{String(h * 3).padStart(2, '0')}:00</span>)}</div></div>{['Passenger', 'Candidate gaps', 'Proposed block'].map(lane => <div className="timeline-row" key={lane}><span>{lane}</span><div className="timeline-track">{lane === 'Passenger' ? moves.map(m => <span key={m.service_id} className="traffic-bar passenger" style={rect(m.start, m.end)} title={`Train ${m.train_no || m.service_id}: ${timelineTime(m.start, dayN)}–${timelineTime(m.end, dayN)} · ${m.from_station || ''} → ${m.to_station || ''} · ${m.provenance || 'timetable reference'}`} />) : lane === 'Candidate gaps' ? gaps.filter(g => g.day === dayN).map((g, i) => <div key={i} className="timeline-authorization" style={rect(g.start, g.end)} title={`Candidate gap ${minute(g.start)}–${minute(g.end)} · ${g.minutes} minutes · conditional/simulated period; unknown traffic and possession clearance remain unresolved`}>Conditional gap</div>) : blocks.filter(b => b.section === section && visible(b)).map(b => <div key={b.id} className="timeline-block joint" style={rect(b.start, b.end)} title={`${b.id}: ${timelineTime(b.start, dayN)}–${timelineTime(b.end, dayN)} · ${b.tasks.map(id => { const t = dataset.tasks.find(x => x.id === id); return `${id} · criticality ${t?.priority ?? '—'}/100` }).join('; ')}`}><span>{timelineTime(b.start, dayN)} · {b.tasks.length} tasks</span></div>)}</div></div>)}</div></div></section>
}

export default function DelayRescheduling({ dataset, plan, scope, busy, onReplan }: { dataset: Dataset; plan: Plan | null; scope: Omit<PlanningConfig,'incidents'>; busy: boolean; onReplan: (config: Partial<PlanningConfig>, previousPlanId?: string) => Promise<Plan|null> }) {
 const sel = dataset.selection || scope;
 const startDate = String(sel.start_date || scope.start_date), horizon = Number(sel.horizon || scope.horizon || 7);
 const [localSection, setLocalSection] = useState('');
 const section = scope.section !== 'all' ? scope.section : (localSection || dataset.sections[0]?.id || '');
 const [day, setDay] = useState('0'); const dayN = Math.min(Number(day), horizon - 1);
 const [trainPick, setTrainPick] = useState(''), [minutesInput, setMinutesInput] = useState('30');
 const [delays, setDelays] = useState<{service_id:string;train_no:string;section:string;minutes:number;original_start:number;original_end:number;propagate:boolean}[]>([]), [analysis, setAnalysis] = useState<DelayAnalysis|null>(null), [afterPlan, setAfterPlan] = useState<Plan | null>(null), [baseDataset, setBaseDataset] = useState<Dataset | null>(null), [basePlan, setBasePlan] = useState<Plan | null>(null), [working, setWorking] = useState(false), [localError, setLocalError] = useState(''), [ranAt, setRanAt] = useState('');
 const [propagate, setPropagate] = useState(false), [solverCfg, setSolverCfg] = useState<SolverSettings>(defaultSolverSettings);
 const scopeKey = `${sel.zone}|${startDate}|${horizon}|${section}`;
 const scopeRef = useRef(scopeKey);
 useEffect(() => { if (scopeRef.current !== scopeKey) { scopeRef.current = scopeKey; setDelays([]); setAnalysis(null); setAfterPlan(null); setBaseDataset(null); setBasePlan(null); setTrainPick('') } }, [scopeKey]);
 const sourceDataset = baseDataset || dataset, sourcePlan = basePlan || plan;
 const dayMoves = (sourceDataset.traffic || []).filter(m => m.section === section && m.end > dayN * 1440 && m.start < (dayN + 1) * 1440);
 const delayedIds = new Set(delays.map(d => d.service_id));
 const trainOptions = dayMoves.filter(m => !delayedIds.has(m.service_id)).map(m => ({ value: m.service_id, label: `${m.train_no} · ${m.from_station || ''} → ${m.to_station || ''} · ${timelineTime(m.start, dayN)}–${timelineTime(m.end, dayN)}` }));
 const picked = dayMoves.find(m => m.service_id === trainPick);
 const delayMinutes = Math.max(0, Math.min(1440, Number(minutesInput) || 0));
 const shiftFor = (m: Movement) => { for (const d of delays) { if (d.service_id === m.service_id) return d.minutes; if (d.propagate) { const target = (sourceDataset.traffic || []).find(x => x.service_id === d.service_id); if (target && m.train_no === target.train_no && m.start >= target.start && m.start < target.start + 1440) return d.minutes } } return 0 };
 const revisedMoves = (sourceDataset.traffic || []).map(m => { const s = shiftFor(m); return s ? { ...m, start: m.start + s, end: m.end + s } : m });
 const delayPayload = delays.map(d => ({ service_id: d.service_id, minutes: d.minutes, ...(d.propagate ? { propagate: true } : {}) }));
 async function simulateAndReplan() {
  if (!delays.length || working || busy) return;
  setWorking(true); setLocalError('');
  try {
   if (!baseDataset) { setBaseDataset(dataset); setBasePlan(plan) }
   const a = await request<DelayAnalysis>({ action: 'delay_analyze', config: { ...scope, delays: delayPayload }, blocks: (basePlan || plan)?.blocks || [] });
   setAnalysis(a); setRanAt(new Date().toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }));
   const p = await onReplan({ ...scope, batch_offset: scope.batch_offset || 0, task_overrides: scope.task_overrides || {}, incidents: ((basePlan || plan)?.config.incidents || []).filter(e => e.kind !== 'delay'), delays: delayPayload, ...solverConfigFields(solverCfg) }, solverCfg.strategy === 'prefer_existing' ? (basePlan || plan)?.id : undefined);
   if (p) setAfterPlan(p);
  } catch (e) { setLocalError((e as Error).message) } finally { setWorking(false) }
 }
 const beforeTasks = new Map<string, Block>(); (sourcePlan?.blocks || []).forEach(b => b.tasks.forEach(id => beforeTasks.set(id, b)));
 const afterTasks = new Map<string, Block>(); (afterPlan?.blocks || []).forEach(b => b.tasks.forEach(id => afterTasks.set(id, b)));
 const moved = [...afterTasks.entries()].filter(([id, b]) => beforeTasks.has(id) && beforeTasks.get(id)!.start !== b.start).map(([id, b]) => ({ id, from: beforeTasks.get(id)!, to: b }));
 const retained = [...afterTasks.entries()].filter(([id, b]) => beforeTasks.has(id) && beforeTasks.get(id)!.start === b.start).length;
 const deferredAfter = (afterPlan?.unscheduled || []).filter(u => beforeTasks.has(u.id));
 const highCritDeferred = deferredAfter.filter(u => { const t = dataset.tasks.find(x => x.id === u.id); return (t?.priority ?? 0) >= 70 });
 const changesHere = (analysis?.gap_changes?.[section] || []).filter(c => c.day === dayN);
 return <>
  <section className="panel planner-toolbar"><div className="planner-intro"><span className="eyebrow">MANUALLY SIMULATED DELAY · NOT A LIVE FEED</span><h2>Train Delay & Rescheduling</h2><p>Delays are controller-entered simulations against the selected timetable references. No live train-delay feed exists in this project, and nothing here is a verified live update. A delay changes scheduling constraints only — saved-model risk, duration and Criticality Index values are not recomputed. Simulating never modifies an existing plan: CP-SAT produces a separate revised draft for review in Maharashtra zones.</p></div>
   <div className="plan-controls">
    {scope.section === 'all' && <label htmlFor="field-delay-rescheduling-1">Track section<Pick id="field-delay-rescheduling-1" label="Delay section" value={section} onChange={v => { setLocalSection(v); setTrainPick(''); setAnalysis(null); setAfterPlan(null) }} options={dataset.sections.map(s => ({ value: s.id, label: s.name }))} /></label>}
    <label htmlFor="field-delay-rescheduling-2">Planning day<Pick id="field-delay-rescheduling-2" label="Delay planning day" value={String(dayN)} onChange={v => { setDay(v); setTrainPick('') }} options={Array.from({ length: horizon }, (_, d) => ({ value: String(d), label: dayDate(startDate, d) }))} /></label>
    <label htmlFor="field-delay-rescheduling-3">Train (selected route timetable)<Pick id="field-delay-rescheduling-3" label="Delayed train" value={trainPick} onChange={setTrainPick} options={[{ value: '', label: 'Choose a train' }, ...trainOptions]} /></label>
    <label>Delay (minutes)<input aria-label="Delay minutes" type="number" min="0" max="1440" value={minutesInput} onChange={e => setMinutesInput(e.target.value)} /></label>
    <label className="twin-check"><input type="checkbox" checked={propagate} onChange={e => setPropagate(e.target.checked)} /> Propagate to downstream sections</label>
    <button className="btn" disabled={!picked || delayedIds.has(trainPick)} onClick={() => { if (!picked) return; setDelays(prev => [...prev, { service_id: picked.service_id, train_no: picked.train_no, section: picked.section, minutes: delayMinutes, original_start: picked.start, original_end: picked.end, propagate }]); setTrainPick(''); setAnalysis(null); setAfterPlan(null) }}>Add delayed train</button>
    <button className="btn primary" disabled={!delays.length || working || busy} onClick={() => void simulateAndReplan()}>{working ? <Loader2 className="spin" size={17} /> : <Zap size={17} />} Simulate Delay & Replan</button>
   </div>
   {picked && <p className="metric-note">Train {picked.train_no} · {picked.from_station || ''} → {picked.to_station || ''} · original {timelineTime(picked.start, dayN)}–{timelineTime(picked.end, dayN)} · revised with +{delayMinutes} min: {timelineTime(picked.start + delayMinutes, dayN)}–{timelineTime(picked.end + delayMinutes, dayN)}{picked.time_scope === 'full_route' ? ' · full-route reference interval (protective)' : ''}</p>}
   {delays.length > 0 && <div className="plan-history">{delays.map(d => <span key={d.service_id} className="pill amber">{d.train_no} +{d.minutes} min{d.propagate ? ' · propagates downstream' : ' · this section only'} <button className="dismiss" aria-label={`Remove delay for train ${d.train_no}`} onClick={() => { setDelays(prev => prev.filter(x => x.service_id !== d.service_id)); setAnalysis(null); setAfterPlan(null) }}><X size={13} /></button></span>)}<span>{delays.length} delayed movement{delays.length === 1 ? '' : 's'} in this scenario</span></div>}
  </section>
  <SolverConfigPanel value={solverCfg} onChange={setSolverCfg} />
  {localError && <div className="error" role="alert">{localError}</div>}
  {!sourcePlan && <p className="metric-note">No plan is loaded for this scope yet. Generate or open a plan in Maharashtra zones to compare affected blocks; the delay analysis and revised draft still run against the full pending queue for this scope.</p>}
  {analysis && <>
   <div className="cc-stats four"><Stat label="Delayed trains" value={analysis.summary.delayed_trains} note="Controller-entered simulation" icon={TrainFront} /><Stat label="Affected maintenance blocks" value={analysis.summary.affected_blocks} note={`of ${analysis.blocks_checked} blocks checked`} icon={Info} /><Stat label="Requests rescheduled" value={afterPlan ? moved.length : '—'} note={afterPlan ? `${retained} allocations retained unchanged` : 'Run the replan to compare'} icon={GitBranch} /><Stat label="Requests deferred" value={afterPlan ? deferredAfter.length : '—'} note={`${highCritDeferred.length} high-criticality (≥70/100) awaiting a feasible window`} icon={ClipboardList} /></div>
   <section className="panel"><div className="section-header"><div><h2>Before vs after — {section}, {dayDate(startDate, dayN)}</h2><p>Same Gantt language as the Controller timeline. Gaps are recomputed from the revised movements, never hardcoded.</p></div><span className="pill gray">Solver: {afterPlan ? afterPlan.solver_status : 'not run'}</span></div>
    <div className="plan-comparison">
     <DelayDayPanel title="BEFORE DELAY" startDate={startDate} dayN={dayN} section={section} movements={sourceDataset.traffic || []} gaps={analysis.gaps_before[section] || []} blocks={sourcePlan?.blocks || []} dataset={sourceDataset} />
     <DelayDayPanel title="AFTER DELAY" startDate={startDate} dayN={dayN} section={section} movements={revisedMoves} gaps={analysis.gaps_after[section] || []} blocks={afterPlan?.blocks || []} dataset={dataset} />
    </div>
    <p className="metric-note">Longest candidate gap on {section}: before {Math.max(0, ...(analysis.gaps_before[section] || []).filter(g => g.day === dayN).map(g => g.minutes))} min · after {Math.max(0, ...(analysis.gaps_after[section] || []).filter(g => g.day === dayN).map(g => g.minutes))} min. Freight, special and unlisted traffic remain unknown; every window stays a conditional simulated candidate, never an official possession.</p></section>
   <section className="panel"><div className="section-header"><div><h2>What changed</h2><p>Every change below is computed from the revised timetable movements.</p></div></div>
    <h3>Delayed trains</h3>{analysis.trains.map(t => <p key={t.service_id} className="metric-note">Train {t.train_no} · {t.from_station || ''} → {t.to_station || ''} · +{t.minutes} min · {timelineTime(t.original_start, dayN)}–{timelineTime(t.original_end, dayN)} → {timelineTime(t.revised_start, dayN)}–{timelineTime(t.revised_end, dayN)}{t.via === 'propagated' ? ' · propagated downstream from the delayed section' : ''}</p>)}
    <h3>Candidate gaps on {section} ({dayDate(startDate, dayN)})</h3>{changesHere.length ? changesHere.map((c, i) => <p key={i} className="metric-note"><b>{c.kind}</b> · {c.before ? `${minute(c.before.start)}–${minute(c.before.end)} (${c.before.minutes} min)` : '—'} → {c.after ? `${minute(c.after.start)}–${minute(c.after.end)} (${c.after.minutes} min)` : 'no gap remains'}</p>) : <p className="metric-note">No gap changes on this day.</p>}
    <h3>Affected maintenance blocks</h3>{analysis.affected_blocks.length ? analysis.affected_blocks.map(b => <p key={b.id} className="metric-note"><b>{b.id}</b> · {timelineTime(b.start, dayN)}–{timelineTime(b.end, dayN)} · tasks {b.tasks.join(', ')} · {b.reason}</p>) : <p className="metric-note">No existing block conflicts with the revised movements.</p>}
    {afterPlan && <><h3>Rescheduling outcome</h3>{moved.length ? moved.map(m => <p key={m.id} className="metric-note"><b>{m.id}</b> · criticality {dataset.tasks.find(x => x.id === m.id)?.priority ?? '—'}/100 · {timelineTime(m.from.start, dayN)}–{timelineTime(m.from.end, dayN)} → {timelineTime(m.to.start, dayN)}–{timelineTime(m.to.end, dayN)}</p>) : <p className="metric-note">No scheduled request had to move.</p>}
     {deferredAfter.length > 0 && <><h3>Deferred with reasons</h3>{deferredAfter.map(u => <p key={u.id} className="metric-note"><b>{u.id}</b> · criticality {dataset.tasks.find(x => x.id === u.id)?.priority ?? '—'}/100 · {u.reason}</p>)}</>}</>}
   </section>
   <PlanCompare baseline={sourcePlan} revised={afterPlan} dataset={dataset} />
   <section className="panel"><div className="section-header"><div><h2>Scenario audit</h2><p>Traceable and reviewable; nothing was silently applied.</p></div></div>
    <p className="metric-note">Source: {analysis.source} — controller-entered simulation, not a verified live update · Simulated at: {ranAt} IST · Scenario fingerprint: {analysis.scenario_fingerprint.slice(0, 16)}… · Input fingerprint before: {String(analysis.input_fingerprint_before || '').slice(0, 12)}… · after: {String(analysis.input_fingerprint_after || '').slice(0, 12)}…</p>
    <p className="metric-note">{sourcePlan ? <>Original plan {sourcePlan.id} · status {sourcePlan.status} · revision {sourcePlan.revision} — unchanged by this simulation.</> : 'No original plan was loaded.'} {afterPlan ? <>Revised draft {afterPlan.id} · status {afterPlan.status} · revision {afterPlan.revision} · solver {afterPlan.solver_status} — review, accept or reject it in Maharashtra zones; stale revisions are rejected there as usual.</> : 'No revised draft yet.'}</p>
    {analysis.limitations.map((l: string) => <p key={l} className="metric-note">{l}</p>)}
   </section>
  </>}
 </>
}
