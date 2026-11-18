'use client';
import {useEffect, useMemo, useRef, useState} from 'react';
import {ChevronLeft, ChevronRight, Clock3, FolderOpen, Pause, Play, Save, Search, TriangleAlert, Wrench} from 'lucide-react';
import {PlanCompare, SolverConfigPanel, defaultSolverSettings, solverConfigFields, type SolverSettings} from './scheduling-config';
import type {Asset, AssetDetail, Dataset, DelayAnalysis, Plan, PlanningConfig, PriorityWeights, Section, TaskOverride, TwinNetwork, TwinOverrides, TwinScenario, WhatIf} from '@/lib/planner-types';

/* Railway Digital Twin: a scenario layer over the existing synthetic
   lifecycle store, saved ML models, corrected timetable and CP-SAT engine.
   Asset values come from the lifecycle database (paginated asset register
   + /twin/asset), what-if predictions from the production inference path
   (/twin/infer), windows from the delay layer (/delay/analyze) and revised
   schedules from the unchanged CP-SAT engine (/twin/replan, saved as a
   separate reviewable Draft). Nothing here writes to stored asset records
   or approved plans. Playback is a simulation from published times - it is
   NOT live train GPS. */

type TwinScope = {
  zone: string; corridor: string; section: string;
  start_date?: string; startDate?: string; horizon?: number;
  traffic_mode?: string; trafficMode?: string;
  planning_start?: number; planningStart?: number;
  planning_end?: number; planningEnd?: number;
  priority_weights?: PriorityWeights;
  resource_overrides?: Record<string, number>;
  batch_offset?: number;
  task_overrides?: Record<string, TaskOverride>;
};
type SavedScenario = {id: string; name: string};
type OpenedScenario = TwinScenario & {id: string; stale: boolean};
type AssetsResponse = {assets?: Asset[]; total?: number; error?: string};
const names: Record<string, string> = {TRK: 'Track', SIG: 'Signalling', TRC: 'Traction / OHE'};
const fmt = (mins: number) => {
  const day = Math.floor(mins / 1440), m = ((mins % 1440) + 1440) % 1440;
  const label = `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  return day > 0 ? `+${day}d ${label}` : day < 0 ? `${day}d ${label}` : label;
};
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const signed = (v: number, digits = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(digits)}`;

async function post<T>(body: Record<string, unknown>): Promise<T> {
  const r = await fetch('/api/planner', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body)});
  const d: unknown = await r.json();
  if (!r.ok) throw new Error(d && typeof d === 'object' && 'error' in d && typeof d.error === 'string' ? d.error : 'Request failed.');
  return d as T;
}

function Pick({label, value, onChange, options}: {label: string; value: string; onChange: (v: string) => void; options: {value: string; label: string}[]}) {
  return <label className="pick"><span>{label}</span><select aria-label={label} value={value} onChange={e => onChange(e.target.value)}>{options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>;
}
function Stat({label, value, sub}: {label: string; value: string; sub?: string}) {
  return <div className="delay-stat"><span>{label}</span><b>{value}</b>{sub && <small>{sub}</small>}</div>;
}

export default function DigitalTwin({dataset, plan, scope, busy, onReplan}: {dataset: Dataset; plan: Plan | null; scope: TwinScope; busy: boolean; onReplan: (body: Record<string, unknown>) => Promise<void>}) {
  const sel: Partial<PlanningConfig> = dataset.selection || {};
  const sections = dataset.sections || [];
  const [section, setSection] = useState(scope.section !== 'all' ? scope.section : sections[0]?.id || '');
  const sec = sections.find(s => s.id === section);
  const [dep, setDep] = useState('all'), [search, setSearch] = useState(''), [page, setPage] = useState(0);
  const [assets, setAssets] = useState<Asset[]>([]), [assetTotal, setAssetTotal] = useState(0), [assetError, setAssetError] = useState('');
  const [detail, setDetail] = useState<AssetDetail | null>(null), [detailError, setDetailError] = useState(''), [detailBusy, setDetailBusy] = useState(false);
  const [detDelta, setDetDelta] = useState(0), [measDelta, setMeasDelta] = useState(0), [elapsed, setElapsed] = useState(0), [fault, setFault] = useState(false), [repaired, setRepaired] = useState(false);
  const [whatif, setWhatif] = useState<WhatIf | null>(null), [whatifError, setWhatifError] = useState(''), [whatifBusy, setWhatifBusy] = useState(false);
  const movements = useMemo(() => (dataset.traffic || []).filter(m => m.section === section && m.end > 0 && m.start < 1440).sort((a, b) => a.start - b.start), [dataset, section]);
  const [trainId, setTrainId] = useState(''), [delayMinutes, setDelayMinutes] = useState(30), [delays, setDelays] = useState<{service_id: string; minutes: number; propagate?: boolean}[]>([]);
  const effectiveTrainId = movements.some(m => m.service_id === trainId) ? trainId : movements[0]?.service_id || '';
  const [propagate, setPropagate] = useState(false), [solverCfg, setSolverCfg] = useState<SolverSettings>(defaultSolverSettings);
  const [analysis, setAnalysis] = useState<DelayAnalysis | null>(null), [analysisError, setAnalysisError] = useState(''), [analysisBusy, setAnalysisBusy] = useState(false);
  const [network, setNetwork] = useState<TwinNetwork | null>(null);
  const [clock, setClock] = useState(0), [playing, setPlaying] = useState(false);
  const [basePlan, setBasePlan] = useState<Plan | null>(null);
  const [scenarioName, setScenarioName] = useState(''), [saved, setSaved] = useState<SavedScenario[]>([]), [opened, setOpened] = useState<OpenedScenario | null>(null), [saveNote, setSaveNote] = useState('');
  const planRef = useRef<Plan | null>(null);
  useEffect(() => { planRef.current = plan; }, [plan]);

  const routes = (dataset.corridors || []).filter(c => c.zone === sel.zone && (sel.corridor === 'all' || c.id === sel.corridor))
    .map(c => ({...c, visibleSections: c.sections.map(id => sections.find(s => s.id === id)).filter((s): s is Section => Boolean(s))})).filter(c => c.visibleSections.length);

  useEffect(() => {
    let active = true;
    const q = new URLSearchParams({resource: 'assets', zone: sel.zone || scope.zone, corridor: sel.corridor || 'all', section, department: dep, search, limit: '10', offset: String(page * 10)});
    fetch(`/api/planner?${q.toString()}`).then(r => r.json() as Promise<AssetsResponse>).then(d => { if (active) { if (d.error) throw new Error(d.error); setAssets(d.assets || []); setAssetTotal(d.total || 0); setAssetError(''); } }).catch(e => active && setAssetError(e.message));
    return () => { active = false; };
  }, [section, dep, search, page, sel.zone, sel.corridor, scope.zone]);
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setClock(c => { const next = c + 6; if (next > 1560) { setPlaying(false); return 1560; } return next; }), 100);
    return () => clearInterval(t);
  }, [playing]);
  useEffect(() => { post<{scenarios?: SavedScenario[]}>({action: 'twin_list'}).then(d => setSaved(d.scenarios || [])).catch(() => {}); }, []);

  const buildConfig = (withDelays: boolean): Partial<PlanningConfig> => {
    const config: Partial<PlanningConfig> = {
      zone: sel.zone || scope.zone, corridor: sel.corridor || scope.corridor, section: sel.section || scope.section,
      start_date: sel.start_date || scope.startDate, horizon: sel.horizon || scope.horizon, traffic_mode: sel.traffic_mode || scope.trafficMode,
      batch_offset: sel.batch_offset || 0, priority_weights: sel.priority_weights, task_overrides: sel.task_overrides,
      resource_overrides: sel.resource_overrides || {},
    };
    const ps = scope.planningStart ?? sel.planning_start, pe = scope.planningEnd ?? sel.planning_end;
    if (Number.isInteger(ps) && Number.isInteger(pe)) { config.planning_start = ps; config.planning_end = pe; }
    Object.assign(config, solverConfigFields(solverCfg));
    if (withDelays && delays.length) config.delays = delays;
    return config;
  };

  const loadAsset = async (assetId: string) => {
    setDetailBusy(true); setDetailError(''); setWhatif(null); setWhatifError('');
    try { setDetail(await post<AssetDetail>({action: 'twin_asset', asset_id: assetId})); }
    catch (e) { setDetailError((e as Error).message); setDetail(null); }
    finally { setDetailBusy(false); }
  };

  const overrides = (): TwinOverrides => {
    const o: TwinOverrides = {};
    if (detDelta) o.deterioration_delta = detDelta;
    if (measDelta) o.measurement_delta = measDelta;
    if (elapsed) o.elapsed_days = elapsed;
    if (fault) o.fault = true;
    if (repaired) o.maintenance_completed = true;
    return o;
  };
  const hasOverrides = detDelta !== 0 || measDelta !== 0 || elapsed !== 0 || fault || repaired;

  const runWhatif = async () => {
    if (!detail) return;
    setWhatifBusy(true); setWhatifError('');
    try { setWhatif(await post<WhatIf>({action: 'twin_infer', asset_id: detail.asset_id, overrides: overrides()})); }
    catch (e) { setWhatifError((e as Error).message); }
    finally { setWhatifBusy(false); }
  };

  const runAnalysis = async () => {
    setAnalysisBusy(true); setAnalysisError('');
    try {
      const config = buildConfig(true);
      config.section = section;
      setAnalysis(await post<DelayAnalysis>({action: 'delay_analyze', config, blocks: plan?.blocks || []}));
      post<TwinNetwork>({action: 'twin_network', config: buildConfig(false), delays}).then(setNetwork).catch(() => {});
    } catch (e) { setAnalysisError((e as Error).message); }
    finally { setAnalysisBusy(false); }
  };

  const batchTask = detail ? (dataset.tasks || []).find(t => t.asset_id === detail.asset_id) : null;
  const twinChange = whatif && batchTask ? {task_id: batchTask.id, overrides: overrides()} : null;

  const replan = async () => {
    if (!twinChange) return;
    setBasePlan(planRef.current);
    const body: Record<string, unknown> = {action: 'twin_replan', config: buildConfig(true), changes: [twinChange]};
    if (solverCfg.strategy === 'prefer_existing' && planRef.current?.id) body.previous_plan_id = planRef.current.id;
    await onReplan(body);
  };

  const saveScenario = async () => {
    const payload: TwinScenario = {
      name: scenarioName || `Twin scenario · ${detail?.asset_id || section}`,
      asset_id: detail?.asset_id || null, section, overrides: overrides(), delays,
      config: buildConfig(true), input_fingerprint: dataset.fingerprint || null,
      model_sha256: whatif?.model_sha256 || detail?.baseline?.recomputed?.model_sha256 || null,
      results: {
        whatif: whatif ? {before: whatif.before, after: {risk: whatif.after.risk, predicted_duration: whatif.after.predicted_duration, priority: whatif.after.priority}} : null,
        delay_summary: analysis?.summary || null,
        solver_status: plan?.scenario_source === 'DIGITAL_TWIN' ? plan.solver_status : null,
      },
      saved_at: new Date().toISOString(),
    };
    try { const d = await post<{id: string}>({action: 'twin_save', scenario: payload}); setSaveNote(`Saved as ${d.id}.`); setSaved(await post<{scenarios?: SavedScenario[]}>({action: 'twin_list'}).then(x => x.scenarios || [])); }
    catch (e) { setSaveNote((e as Error).message); }
  };

  const openScenario = async (id: string) => {
    try {
      const d = await post<{scenario: TwinScenario; id: string}>({action: 'twin_open', scenario_id: id});
      const s = d.scenario;
      setOpened({...s, id: d.id, stale: Boolean(s.input_fingerprint && dataset.fingerprint && s.input_fingerprint !== dataset.fingerprint)});
      if (s.section) setSection(s.section);
      setDelays(s.delays || []);
      const o = s.overrides || {};
      setDetDelta(o.deterioration_delta || 0); setMeasDelta(o.measurement_delta || 0); setElapsed(o.elapsed_days || 0); setFault(Boolean(o.fault)); setRepaired(Boolean(o.maintenance_completed));
      if (s.asset_id) void loadAsset(s.asset_id);
    } catch (e) { setSaveNote((e as Error).message); }
  };

  const delayShift = (serviceId: string) => delays.find(d => d.service_id === serviceId)?.minutes || 0;
  const activeTrains = movements.filter(m => clock >= m.start + delayShift(m.service_id) && clock <= m.end + delayShift(m.service_id));
  const base = detail?.baseline;

  return <>
    <section className="panel delay-warning" role="note">
      <TriangleAlert size={18} />
      <p><b>Simulation twin - not a live railway feed.</b> Asset condition, positions and train playback are computed from the synthetic lifecycle database, the saved trained models and the published-times timetable. Coordinates are approximate synthetic interpolations, failure risks are uncalibrated, timetable coverage is incomplete, and no window shown is an authorized track possession. Freight and unlisted traffic remain unknown. Twin scenarios never modify stored assets or approved plans; replans are saved as separate Drafts for Controller review.</p>
    </section>

    <SolverConfigPanel value={solverCfg} onChange={setSolverCfg} />

    <section className="panel network-panel">
      <div className="section-header"><div><h2>Digital network · {sel.zone || scope.zone}</h2><p>Project-defined Maharashtra network schematic. Select a section to twin its assets, trains and windows.</p></div><span className="pill gray">Approximate project geography · synthetic positions</span></div>
      <div className="network-routes">{routes.map(route => <div className="network-route" key={route.id}><b>{route.name}</b><div className="network-path">{route.visibleSections.map((s, i) => <div className="network-segment" key={s.id}><span className="network-station"><i /><b>{s.from_station}</b></span><button className={section === s.id ? 'active' : ''} onClick={() => { setSection(s.id); setPage(0); setAnalysis(null); }} aria-label={`Select section ${s.name}`}><span>{s.id}</span><i /></button>{i === route.visibleSections.length - 1 && <span className="network-station"><i /><b>{s.to_station}</b></span>}</div>)}</div></div>)}</div>
      {sec && <div className="network-section-stats"><span><b>{section}</b>{sec.name}</span><span><b>{assetTotal}</b>saved assets in section</span><span><b>{movements.length}</b>listed trains · day 1</span><span><b>{(plan?.blocks || []).filter(b => b.section === section).length}</b>blocks in current plan</span></div>}
      <div className="delay-controls">
        <button className="btn" onClick={() => { if (clock >= 1560) setClock(0); setPlaying(p => !p); }}>{playing ? <Pause size={15} /> : <Play size={15} />}{playing ? 'Pause playback' : 'Play timetable'}</button>
        <label className="pick"><span>Simulated clock · {fmt(clock)}</span><input aria-label="Simulated clock" type="range" min={0} max={1560} step={5} value={clock} onChange={e => setClock(Number(e.target.value))} /></label>
        <span className="pill gray"><Clock3 size={13} /> {activeTrains.length ? activeTrains.map(m => `${m.train_no} in section`).join(' · ') : 'No listed train in this section at the simulated clock'}</span>
      </div>
      <p className="delay-note">Simulated playback from published section times (delays applied where set below). This is not live GPS or verified operational traffic. Overnight movements run past 24:00 on the simulated clock.</p>
      <div className="twin-timeline">{movements.map(m => { const shift = delayShift(m.service_id); const left = ((m.start + shift) / 1560) * 100, width = Math.max(1.2, ((m.end - m.start) / 1560) * 100); const active = clock >= m.start + shift && clock <= m.end + shift; return <div key={m.service_id} className="twin-train-row"><span>{m.train_no}</span><div className="twin-track"><i className={shift ? 'delayed' : active ? 'active' : ''} style={{left: `${left}%`, width: `${width}%`}} />{active && <b style={{left: `${(((clock - m.start - shift) / Math.max(1, m.end - m.start)) * width + left).toFixed(2)}%`}} />}</div><small>{fmt(m.start + shift)}–{fmt(m.end + shift)}{shift ? ` (+${shift}m)` : ''}</small></div>; })}</div>
    </section>

    <div className="delay-grid">
      <section className="panel">
        <div className="section-header"><div><h2>Asset register · {section}</h2><p>Paginated from the lifecycle database - selecting an asset loads its real stored history.</p></div></div>
        <div className="overview-filters">
          <div className="search-field"><Search size={15} /><input aria-label="Search asset IDs" value={search} placeholder="Search asset ID" onChange={e => { setSearch(e.target.value); setPage(0); }} /></div>
          <Pick label="Department" value={dep} onChange={v => { setDep(v); setPage(0); }} options={[{value: 'all', label: 'All departments'}, ...Object.entries(names).map(([value, label]) => ({value, label}))]} />
        </div>
        {assetError && <p className="error" role="alert">{assetError}</p>}
        <div className="twin-asset-list">{assets.map(a => <button key={a.id} className={detail?.asset_id === a.id ? 'active' : ''} onClick={() => void loadAsset(a.id)}><b>{a.id}</b><span>{names[a.department]} · KM {a.km}</span><span>Condition {Math.round(a.criticality * 100)}/100 · 30d risk {pct(a.risk['30'])}</span><small>{a.pending_requests} pending · {a.request_count} requests</small></button>)}</div>
        {!assets.length && !assetError && <p className="cc-empty">No saved assets match this section and department.</p>}
        <div className="table-pager"><span>{assetTotal} saved assets</span><button className="btn" aria-label="Previous assets" disabled={!page} onClick={() => setPage(page - 1)}><ChevronLeft size={16} /></button><span>{page + 1} / {Math.max(1, Math.ceil(assetTotal / 10))}</span><button className="btn" aria-label="Next assets" disabled={(page + 1) * 10 >= assetTotal} onClick={() => setPage(page + 1)}><ChevronRight size={16} /></button></div>
      </section>

      <section className="panel">
        <div className="section-header"><div><h2>Asset digital twin</h2><p>{detail ? `${detail.asset_id} · ${detail.asset_type} · ${detail.section_name}` : 'Select an asset from the register.'}</p></div>{detailBusy && <span className="pill gray">Loading…</span>}</div>
        {detailError && <p className="error" role="alert">{detailError}</p>}
        {detail && base && <>
          <div className="delay-stats">
            <Stat label="Condition (deterioration)" value={`${Math.round(detail.features[4] * 100)} / 100`} sub={`Age ${detail.features[0]}y · defects ${detail.features[1]}`} />
            <Stat label="Failure risk · 7 / 30 / 90d" value={`${pct(base.risk['7'])} / ${pct(base.risk['30'])} / ${pct(base.risk['90'])}`} sub="Saved-model estimates · uncalibrated" />
            <Stat label="Criticality Index" value={`${base.priority} / 100`} sub={Object.entries(base.components || {}).map(([k, v]) => `${k} ${v}`).join(' · ')} />
            <Stat label="Predicted duration" value={`${Math.round(base.predicted_duration)} min`} sub={`+ ${Number(base.duration_buffer).toFixed(1)} min model buffer`} />
          </div>
          {base.reproduces_stored === true && <p className="delay-note">Baseline audit: production inference on the stored features reproduces the stored risks exactly - what-if results below use the same path.</p>}
          {base.reproduces_stored === false && <p className="error" role="alert">Baseline audit failed: recomputed risks differ from stored risks. Treat what-if results with caution.</p>}
          <p className="delay-note">Position KM {detail.km} · {detail.latitude?.toFixed(4)}, {detail.longitude?.toFixed(4)} - {detail.position_provenance}. Status of latest request {base.request_id}: {base.status}. {detail.inspection_count} stored inspections · {detail.completions.length} recorded completions.</p>
          <div className="twin-history"><table><thead><tr><th>Inspection day</th><th>Deterioration</th><th>Measurement</th><th>Overdue</th><th>Failed ≤30d</th></tr></thead><tbody>{detail.observations.slice(-8).reverse().map(o => <tr key={o.observation_id}><td>{o.day}</td><td>{o.deterioration.toFixed(2)}</td><td>{o.inspection_measurement.toFixed(2)}</td><td>{o.overdue_days}d</td><td>{o.failure_within_30d ? 'Yes' : 'No'}</td></tr>)}</tbody></table></div>
        </>}
        {!detail && !detailError && <p className="cc-empty">No asset selected yet.</p>}
      </section>
    </div>

    {detail && <section className="panel">
      <div className="section-header"><div><h2>What-if health simulation · {detail.asset_id}</h2><p>Deterministic scenario inputs transform the current features; the saved trained models then recompute risk, duration and criticality. Scenario state only - stored records never change.</p></div><Wrench size={18} /></div>
      <div className="delay-controls">
        <label className="pick"><span>Deterioration change · {signed(detDelta, 2)}</span><input aria-label="Deterioration change" type="range" min={-0.3} max={0.5} step={0.05} value={detDelta} onChange={e => setDetDelta(Number(e.target.value))} /></label>
        <label className="pick"><span>Inspection measurement change · {signed(measDelta, 2)}</span><input aria-label="Inspection measurement change" type="range" min={-2} max={3} step={0.25} value={measDelta} onChange={e => setMeasDelta(Number(e.target.value))} /></label>
        <label className="pick"><span>Elapsed days since maintenance · {elapsed}</span><input aria-label="Elapsed days" type="range" min={0} max={180} step={5} value={elapsed} onChange={e => setElapsed(Number(e.target.value))} /></label>
        <label className="twin-check"><input type="checkbox" checked={fault} onChange={e => setFault(e.target.checked)} /> Simulate asset fault</label>
        <label className="twin-check"><input type="checkbox" checked={repaired} onChange={e => setRepaired(e.target.checked)} /> Maintenance completed (scenario)</label>
        <button className="btn primary" disabled={!hasOverrides || whatifBusy} onClick={() => void runWhatif()}>{whatifBusy ? 'Recomputing…' : 'Recompute ML predictions'}</button>
      </div>
      {whatifError && <p className="error" role="alert">{whatifError}</p>}
      {whatif && <>
        <div className="twin-compare">
          <div><h3>Baseline</h3><p>Risk 7/30/90d <b>{pct(whatif.before.risk['7'])} / {pct(whatif.before.risk['30'])} / {pct(whatif.before.risk['90'])}</b></p><p>Duration <b>{Math.round(whatif.before.predicted_duration)} min</b></p><p>Criticality <b>{whatif.before.priority} / 100</b></p></div>
          <div><h3>What-if scenario</h3><p>Risk 7/30/90d <b>{pct(whatif.after.risk['7'])} / {pct(whatif.after.risk['30'])} / {pct(whatif.after.risk['90'])}</b> <small>({signed((whatif.after.risk['30'] - whatif.before.risk['30']) * 100)} pts @30d)</small></p><p>Duration <b>{Math.round(whatif.after.predicted_duration)} min</b> <small>({signed(whatif.after.predicted_duration - whatif.before.predicted_duration, 0)} min)</small></p><p>Criticality <b>{whatif.after.priority} / 100</b> <small>({signed(whatif.after.priority - whatif.before.priority)} pts)</small></p></div>
        </div>
        <p className="delay-note">Component changes: {Object.keys(whatif.after.components || {}).map(k => `${k} ${whatif.before.components?.[k] ?? 0} → ${whatif.after.components[k]}`).join(' · ')}</p>
        <ul className="delay-changes">{whatif.input_changes.map((c, i) => <li key={i}><b>{c.feature}</b> {c.before} → {c.after} <small>{c.rule} · deterministic rule; predictions above are ML</small></li>)}</ul>
        <p className="delay-note">{whatif.method.ml} {whatif.method.rules}</p>
      </>}
    </section>}

    <div className="delay-grid">
      <section className="panel">
        <div className="section-header"><div><h2>Train movement twin · {section}</h2><p>The whole network is re-simulated as one twin state: direct delays, downstream propagation and knock-on pushes between trains, then gaps recalculated on the corrected section timetable.</p></div></div>
        <div className="delay-controls">
          <Pick label="Train (day 1, this section)" value={effectiveTrainId} onChange={setTrainId} options={movements.map(m => ({value: m.service_id, label: `${m.train_no} · ${m.from_station} → ${m.to_station} · ${fmt(m.start)}–${fmt(m.end)}`}))} />
          <label className="pick"><span>Delay · minutes</span><input aria-label="Delay minutes" type="number" min={0} max={720} step={5} value={delayMinutes} onChange={e => setDelayMinutes(Number(e.target.value))} /></label>
          <label className="twin-check"><input type="checkbox" checked={propagate} onChange={e => setPropagate(e.target.checked)} /> Propagate downstream</label>
          <button className="btn" disabled={!effectiveTrainId} onClick={() => { setDelays(prev => [...prev.filter(d => d.service_id !== effectiveTrainId), {service_id: effectiveTrainId, minutes: delayMinutes, ...(propagate ? {propagate: true} : {})}]); setAnalysis(null); }}>Add delay</button>
          <button className="btn primary" disabled={!delays.length || analysisBusy} onClick={() => void runAnalysis()}>{analysisBusy ? 'Recalculating…' : 'Recalculate windows'}</button>
        </div>
        {!!delays.length && <div className="delay-chips">{delays.map(d => { const m = movements.find(x => x.service_id === d.service_id); return <span key={d.service_id} className="pill amber">{m?.train_no || d.service_id} +{d.minutes}m{d.propagate ? ' · propagates' : ''} <button aria-label="Remove delay" onClick={() => { setDelays(delays.filter(x => x.service_id !== d.service_id)); setAnalysis(null); }}>×</button></span>; })}</div>}
        {analysisError && <p className="error" role="alert">{analysisError}</p>}
        {analysis && <div className="delay-stats">
          <Stat label="Longest gap · before → after" value={`${analysis.summary.longest_gap_before[section] ?? 0} → ${analysis.summary.longest_gap_after[section] ?? 0} min`} />
          <Stat label="Candidate time · before → after" value={`${(analysis.gaps_before[section] || []).reduce((n, g) => n + g.minutes, 0)} → ${(analysis.gaps_after[section] || []).reduce((n, g) => n + g.minutes, 0)} min`} />
          <Stat label="Gaps disappeared / shrunk / new" value={`${analysis.summary.gaps_disappeared} / ${analysis.summary.gaps_shrunk_or_shifted} / ${analysis.summary.gaps_new}`} />
          <Stat label="Existing blocks affected" value={String(analysis.summary.affected_blocks)} sub={analysis.source === 'MANUAL_SIMULATION' ? 'Manual simulation - not a live feed' : analysis.source} />
        </div>}
        {network && <div className="delay-stats">
          <Stat label="Network twin - trains" value={String(network.summary.trains)} sub={`${network.summary.movements} movements simulated`} />
          <Stat label="Direct / propagated" value={`${network.summary.direct} / ${network.summary.propagated}`} />
          <Stat label="Knock-on movements" value={String(network.summary.knock_on_movements)} sub={`${network.summary.knock_on_trains} other train${network.summary.knock_on_trains === 1 ? '' : 's'} pushed`} />
          <Stat label="Max delay in network" value={`${network.summary.max_delay_minutes} min`} />
        </div>}
        {network && network.knock_on.length > 0 && <div><h3>Knock-on effects across the network</h3>{network.knock_on.map(k => <p key={k.service_id} className="metric-note"><b>Train {k.train_no}</b> · {dataset.sections.find(s => s.id === k.section)?.name || k.section} · <span className="pill amber">+{k.knock_on_minutes} min knock-on</span> · pushed by train {k.caused_by_train_no || '—'} ({k.mechanism === 'same_train' ? 'carried along its own route' : 'headway behind a late train'})</p>)}</div>}
      </section>

      <section className="panel">
        <div className="section-header"><div><h2>CP-SAT twin replan</h2><p>Recomputed asset values + simulated delays are sent to the unchanged CP-SAT engine. The result is a separate Draft; the current plan is never modified.</p></div></div>
        {!whatif && <p className="cc-empty">Run a what-if recomputation for the selected asset first.</p>}
        {whatif && !batchTask && <p className="delay-note">{detail?.asset_id}&apos;s latest request is not inside the current 300-request planning batch, so CP-SAT cannot replan it in this scope. Its recomputed predictions above are still valid; widen the batch/scope or pick an asset from the planner batch to replan.</p>}
        {whatif && batchTask && <p className="delay-note">Change ready: task {batchTask.id} risk 30d {pct(whatif.before.risk['30'])} → {pct(whatif.after.risk['30'])}, duration {Math.round(whatif.before.predicted_duration)} → {Math.round(whatif.after.predicted_duration)} min{delays.length ? `, plus ${delays.length} simulated train delay(s)` : ''}. Planning period, crew and safety constraints come from the current Controller scope.</p>}
        <button className="btn primary" disabled={!twinChange || busy} onClick={() => void replan()}>{busy ? 'Solving…' : 'Replan with CP-SAT'}</button>
        {plan?.scenario_source === 'DIGITAL_TWIN' && <div className="delay-stats">
          <Stat label="Twin plan" value={plan.id || 'Draft'} sub={`Solver ${plan.solver_status}`} />
          <Stat label="Blocks scheduled" value={String(plan.blocks?.length || 0)} sub={basePlan ? `Baseline plan had ${basePlan.blocks?.length || 0}` : undefined} />
          <Stat label="Deferred in batch" value={String(plan.unscheduled?.length || 0)} sub="Reasons listed in Maharashtra zones" />
          <Stat label="Twin changes applied" value={String(plan.twin_changes?.length || 0)} />
        </div>}
        {plan?.scenario_source === 'DIGITAL_TWIN' && <p className="delay-note">Review, accept or reject this Draft in Maharashtra zones - accepting it there follows the normal Controller workflow. Solver status is reported exactly as returned (OPTIMAL / FEASIBLE / ROLLING_FEASIBLE / UNKNOWN); batches remain independent 300-request solves.</p>}
      </section>
    </div>

    {plan?.scenario_source === 'DIGITAL_TWIN' && basePlan && <PlanCompare baseline={basePlan} revised={plan} dataset={dataset} />}

    <section className="panel">
      <div className="section-header"><div><h2>Scenario audit & persistence</h2><p>Saved scenarios record base fingerprints, model hash, changed inputs and results. Reopening a scenario whose source data or model changed is flagged stale - its numbers may not reproduce.</p></div><FolderOpen size={18} /></div>
      <div className="delay-controls">
        <label className="pick"><span>Scenario name</span><input aria-label="Scenario name" value={scenarioName} onChange={e => setScenarioName(e.target.value)} placeholder="e.g. Fault on SYN-A-00123 + 17412 delay" /></label>
        <button className="btn" onClick={() => void saveScenario()}><Save size={15} /> Save scenario</button>
        {saved.slice(0, 6).map(s => <button key={s.id} className="btn" onClick={() => void openScenario(s.id)}>{s.id} · {s.name}</button>)}
      </div>
      {saveNote && <p className="delay-note">{saveNote}</p>}
      {opened && <>
        {opened.stale ? <p className="error" role="alert">Stale scenario: the dataset/model fingerprint changed since this scenario was saved ({opened.saved_at}). Inputs were restored for inspection, but results may not reproduce - recompute before replanning.</p> : <p className="delay-note">Scenario {opened.id} reopened; source fingerprint still matches the loaded dataset, so its results remain reproducible.</p>}
        <p className="delay-note">Saved inputs: asset {opened.asset_id || '—'} · overrides {JSON.stringify(opened.overrides || {})} · delays {JSON.stringify(opened.delays || [])} · model {opened.model_sha256 ? String(opened.model_sha256).slice(0, 12) + '…' : '—'} · solver at save: {opened.results?.solver_status || '—'}</p>
      </>}
      <p className="delay-note">Feasibility audit for any twin plan lives on the plan itself: solver status, per-task deferral reasons and the plan validator in Maharashtra zones. Baseline records and approved plans remain unchanged by everything on this tab.</p>
    </section>
  </>;
}
