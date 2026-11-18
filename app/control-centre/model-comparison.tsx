'use client';
import { useEffect, useMemo, useState } from 'react';
import { Activity, Info, Loader2, TrendingUp } from 'lucide-react';

/* Typed view of the saved model-comparison artifact (planner/maharashtra/
   pipeline.py writes .railblock/model-comparison.json; store.model_comparison()
   wraps it in the response envelope). The fetch boundary validates the JSON
   structurally before narrowing — no unsafe casts or suppression comments. */
type HorizonKey = '7' | '30' | '90';
type ThresholdKey = '0.5' | '0.2' | '0.1' | '0.05';

interface ThresholdMetrics { tp: number; fp: number; tn: number; fn: number; precision: number | null; recall: number | null; f1: number | null }
interface TopMetrics { k: number; positives: number; precision: number | null; recall: number | null }
interface ClfMetrics {
  roc_auc: number | null; pr_auc: number | null; brier: number; ece: number | null; prevalence: number;
  'threshold_0.5': ThresholdMetrics; 'threshold_0.2': ThresholdMetrics; 'threshold_0.1': ThresholdMetrics; 'threshold_0.05': ThresholdMetrics;
  top_5_percent: TopMetrics; top_10_percent: TopMetrics; top_300: TopMetrics;
}
interface GroupMetrics { n: number; positives: number; roc_auc: number | null; brier: number }
interface SelectedRef { model: string; feature_set: string }
interface RiskCandidate {
  model: string; feature_set: string; validation_brier: number; train_seconds: number; infer_ms_per_row: number;
  test: ClfMetrics; by_department: Record<string, GroupMetrics>; by_zone: Record<string, GroupMetrics>;
}
interface CalibrationPoint { predicted: number; observed: number; count: number }
interface RiskBlock {
  decision: string; selected: SelectedRef;
  calibration: { status: string; adopted: string | null; note: string };
  candidates: RiskCandidate[];
  selected_curves: { roc: number[][]; precision_recall: number[][] };
  selected_calibration_points: CalibrationPoint[];
  feature_importance: Record<string, Record<string, number>>;
}
interface OrderedFinal { metrics: ClfMetrics; curves: { roc: number[][]; precision_recall: number[][] }; calibration_points: CalibrationPoint[] }
interface DurationCandidate { model: string; feature_set: string; validation_mae: number; train_seconds: number; test: { mae: number; rmse: number; r2: number } }
interface DurationBlock {
  decision: string; selected: SelectedRef; candidates: DurationCandidate[];
  selected_test_by_department: Record<string, { n: number; mae: number }>;
  actual_vs_predicted_sample: number[][];
  feature_importance: Record<string, Record<string, number>>;
}
interface ComparisonArtifact {
  warning: string; generated_at: string; model_sha256: string; model_file_bytes: number;
  splits: { train: { rows: number; months: number[] }; validation: { rows: number }; test: { rows: number } };
  selection: { risk: string; duration: string; test_usage: string };
  features: { engineered_extra: string[]; engineered_note: string };
  deployed: { risk: Record<HorizonKey, SelectedRef>; duration: SelectedRef; buffer_minutes: number };
  risk: Record<HorizonKey, RiskBlock>;
  ordered_final: Record<HorizonKey, OrderedFinal>;
  duration: DurationBlock;
}
interface ComparisonResponse {
  available: boolean; reason?: string; artifact?: ComparisonArtifact;
  artifact_model_matches_deployed?: boolean; deployed_model_sha256?: string | null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
function isCandidateList(v: unknown): boolean {
  return Array.isArray(v) && v.every(c => isRecord(c) && typeof c.model === 'string' && typeof c.feature_set === 'string' && isRecord(c.test));
}
function parseComparisonResponse(raw: unknown): ComparisonResponse {
  if (!isRecord(raw) || typeof raw.available !== 'boolean') throw new Error('Model comparison response has an unexpected format.');
  if (!raw.available) return { available: false, reason: typeof raw.reason === 'string' ? raw.reason : undefined };
  const a = raw.artifact;
  if (!isRecord(a) || typeof a.warning !== 'string') throw new Error('Model comparison artifact has an unexpected format.');
  const { splits, selection, features, deployed, risk, ordered_final, duration } = a;
  if (!isRecord(splits) || !isRecord(selection) || !isRecord(features) || !isRecord(deployed) || !isRecord(risk)
    || !isRecord(ordered_final) || !isRecord(duration) || !isRecord(duration.selected)) {
    throw new Error('Model comparison artifact has an unexpected format.');
  }
  for (const h of ['7', '30', '90']) {
    const block = risk[h];
    if (!isRecord(block) || !isCandidateList(block.candidates) || !isRecord(block.selected)) {
      throw new Error('Model comparison artifact has an unexpected format.');
    }
  }
  if (!isCandidateList(duration.candidates)) throw new Error('Model comparison artifact has an unexpected format.');
  // Structure validated above; the assertion only narrows the validated JSON.
  return raw as unknown as ComparisonResponse;
}
function errorMessage(raw: unknown, fallback: string): string {
  return isRecord(raw) && typeof raw.error === 'string' ? raw.error : fallback;
}
const number = (n: number) => n.toLocaleString('en-IN');
const fmt = (v: number | null | undefined, d = 4) => v === null || v === undefined || Number.isNaN(v) ? '—' : v.toFixed(d);
const HORIZONS: HorizonKey[] = ['7', '30', '90'];
const THRESHOLDS: ThresholdKey[] = ['0.5', '0.2', '0.1', '0.05'];
const DEPT_LABELS: Record<string, string> = { 'dept-0': 'TRK', 'dept-1': 'SIG', 'dept-2': 'TRC' };

function Bars({ rows, value, label }: { rows: RiskCandidate[]; value: (r: RiskCandidate) => number; label: string }) {
  const max = Math.max(...rows.map(value), 0.0001);
  return <div style={{ display: 'grid', gap: 6 }}>{rows.map((r, i) => { const v = value(r); return <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10 }}><span style={{ width: 230, fontSize: 12 }}>{r.model} <small>({r.feature_set})</small></span><div style={{ flex: 1, background: 'var(--muted,#e5e7eb)', borderRadius: 4, height: 12 }}><div style={{ width: `${Math.max(2, v / max * 100)}%`, height: 12, borderRadius: 4, background: 'var(--primary,#2563eb)' }} /></div><b style={{ width: 70, textAlign: 'right', fontSize: 12 }}>{fmt(v)}</b></div> })}<p className="metric-note">{label}</p></div>;
}

function CurveChart({ title, series, xLabel, yLabel }: { title: string; series: { name: string; points: number[][]; color: string }[]; xLabel: string; yLabel: string }) {
  const W = 520, H = 300, P = 36;
  const line = (pts: number[][]) => pts.map(p => `${(P + p[0] * (W - 2 * P)).toFixed(1)},${(H - P - p[1] * (H - 2 * P)).toFixed(1)}`).join(' ');
  return <div><h3 style={{ fontSize: 13, margin: '4px 0' }}>{title}</h3><svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: 560, background: 'var(--card,#fff)', border: '1px solid var(--border,#e5e7eb)', borderRadius: 8 }}>
    <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="#999" /><line x1={P} y1={P} x2={P} y2={H - P} stroke="#999" />
    <line x1={P} y1={H - P} x2={W - P} y2={P} stroke="#ccc" strokeDasharray="4 4" />
    {series.map((s, i) => <polyline key={i} fill="none" stroke={s.color} strokeWidth="2" points={line(s.points)} />)}
    <text x={W / 2} y={H - 8} textAnchor="middle" fontSize="11">{xLabel}</text><text x={12} y={H / 2} fontSize="11" transform={`rotate(-90 12 ${H / 2})`} textAnchor="middle">{yLabel}</text>
  </svg><p className="metric-note">{series.map(s => s.name).join(' · ')}</p></div>;
}

function CalibrationChart({ points }: { points: CalibrationPoint[] }) {
  const W = 520, H = 300, P = 36;
  return <div><h3 style={{ fontSize: 13, margin: '4px 0' }}>Calibration — selected model (test cohort)</h3><svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: 560, background: 'var(--card,#fff)', border: '1px solid var(--border,#e5e7eb)', borderRadius: 8 }}>
    <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="#999" /><line x1={P} y1={P} x2={P} y2={H - P} stroke="#999" />
    <line x1={P} y1={H - P} x2={W - P} y2={P} stroke="#ccc" strokeDasharray="4 4" />
    {points.map((b, i) => <circle key={i} cx={P + b.predicted * (W - 2 * P)} cy={H - P - b.observed * (H - 2 * P)} r={Math.min(10, 3 + Math.sqrt(b.count) / 8)} fill="#2563eb" opacity="0.75"><title>{`predicted ${fmt(b.predicted, 3)} · observed ${fmt(b.observed, 3)} · n=${b.count}`}</title></circle>)}
    <text x={W / 2} y={H - 8} textAnchor="middle" fontSize="11">Mean predicted probability</text><text x={12} y={H / 2} fontSize="11" transform={`rotate(-90 12 ${H / 2})`} textAnchor="middle">Observed failure rate</text>
  </svg><p className="metric-note">Points on the dashed diagonal are perfectly calibrated; point size grows with bin count.</p></div>;
}

function ImportanceBars({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])); const max = Math.max(...entries.map(e => Math.abs(e[1])), 0.0001);
  return <div style={{ flex: 1, minWidth: 260 }}><h3 style={{ fontSize: 13, margin: '4px 0' }}>{title}</h3>{entries.map(([f, v]) => <div key={f} style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span style={{ width: 170, fontSize: 11 }}>{f}</span><div style={{ flex: 1, background: 'var(--muted,#e5e7eb)', borderRadius: 4, height: 10 }}><div style={{ width: `${Math.abs(v) / max * 100}%`, height: 10, borderRadius: 4, background: '#2563eb' }} /></div><span style={{ width: 56, fontSize: 11, textAlign: 'right' }}>{fmt(v, 3)}</span></div>)}</div>;
}

export default function ModelComparison() {
  const [resp, setResp] = useState<ComparisonResponse | null>(null); const [error, setError] = useState('');
  const [horizon, setHorizon] = useState<HorizonKey>('30'); const [threshold, setThreshold] = useState<ThresholdKey>('0.1'); const [curveMode, setCurveMode] = useState<'selected' | 'ordered'>('ordered');
  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const r = await fetch('/api/model-comparison', { cache: 'no-store' });
        const raw: unknown = await r.json();
        if (!r.ok) throw new Error(errorMessage(raw, 'Unable to load model comparison.'));
        const parsed = parseComparisonResponse(raw);
        if (live) setResp(parsed);
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : 'Unable to load model comparison.');
      }
    })();
    return () => { live = false };
  }, []);
  const artifact = resp?.artifact;
  const selectedRow = useMemo(() => {
    if (!artifact) return null;
    const sel = artifact.risk[horizon].selected;
    return artifact.risk[horizon].candidates.find(c => c.model === sel.model && c.feature_set === sel.feature_set) ?? null;
  }, [artifact, horizon]);
  if (error) return <section className="panel cc-empty"><p>{error}</p></section>;
  if (!resp) return <section className="panel cc-empty"><Loader2 className="spin" />Loading model comparison…</section>;
  if (!resp.available || !artifact) return <section className="panel"><div className="section-header"><div><h2><Activity size={18} /> ML Model Comparison</h2><p>{resp.reason || 'No comparison artifact is available yet.'}</p></div></div></section>;
  const dep = artifact.deployed; const splits = artifact.splits;
  const block = artifact.risk[horizon];
  const ordered = artifact.ordered_final[horizon];
  const useOrdered = curveMode === 'ordered';
  const modeLabel = useOrdered ? 'Ordered final probabilities' : 'Raw model probabilities';
  const m = useOrdered ? ordered.metrics : selectedRow?.test;
  const matrix = m?.[`threshold_${threshold}`];
  const curves = useOrdered ? ordered.curves : block.selected_curves;
  const calPts = useOrdered ? ordered.calibration_points : block.selected_calibration_points;
  return <>
    <section className="panel controller-data-note"><b>Synthetic-data evaluation — read this first</b><p>{artifact.warning} Every number below comes from the saved evaluation artifact written by the training pipeline on the same asset-disjoint, chronological test cohort; nothing is typed in by hand. Risk probabilities feed the six-factor Criticality Index (30-day risk) and predicted durations feed CP-SAT.</p></section>
    <div className="cc-stats four">
      <div className="cc-stat"><span>Training rows<Activity size={17} /></span><strong>{number(splits.train.rows)}</strong><small>Months {splits.train.months.join('–')} · assets 0–60%</small></div>
      <div className="cc-stat"><span>Validation rows<Activity size={17} /></span><strong>{number(splits.validation.rows)}</strong><small>Used for model selection only</small></div>
      <div className="cc-stat"><span>Test rows<TrendingUp size={17} /></span><strong>{number(splits.test.rows)}</strong><small>Untouched until final reporting · 120-day purge</small></div>
      <div className="cc-stat"><span>Deployed bundle<Info size={17} /></span><strong>{artifact.model_file_bytes ? `${(artifact.model_file_bytes / 1024).toFixed(0)} KB` : '—'}</strong><small>{resp.artifact_model_matches_deployed ? 'Artifact matches the deployed model hash' : 'Artifact does NOT match the deployed bundle — retrain to refresh'} · {artifact.generated_at ? new Date(artifact.generated_at).toLocaleString('en-IN') : ''}</small></div>
    </div>
    {!resp.artifact_model_matches_deployed && <p className="error" role="alert">This comparison artifact was generated for model hash {artifact.model_sha256?.slice(0, 12)}…, but the currently deployed bundle is {resp.deployed_model_sha256?.slice(0, 12)}…. Retrain (npm run data:maharashtra) so the comparison and the deployed models describe the same bundle.</p>}
    <section className="panel mc-panel" aria-labelledby="confusion-title">
      <div className="section-header"><div>
        <h2 id="confusion-title">Failure prediction confusion matrix</h2>
        <p>{horizon}-day {block.selected.model} · {modeLabel} · Synthetic test observations</p>
      </div></div>
      <fieldset className="department-tabs" aria-label="Prediction horizon">
        {HORIZONS.map(h => <button key={h} type="button" aria-pressed={horizon === h} className={horizon === h ? 'active' : ''} onClick={() => setHorizon(h)}>{h}-day</button>)}
      </fieldset>
      <fieldset className="department-tabs" aria-label="Probability source">
        <button type="button" aria-pressed={useOrdered} className={useOrdered ? 'active' : ''} onClick={() => setCurveMode('ordered')}>Ordered final</button>
        <button type="button" aria-pressed={!useOrdered} className={!useOrdered ? 'active' : ''} onClick={() => setCurveMode('selected')}>Selected model raw</button>
      </fieldset>
      <p className="metric-note">An observation is flagged when its predicted failure probability reaches the selected threshold.</p>
      <fieldset className="department-tabs" aria-label="Failure probability threshold">
        {THRESHOLDS.map(t => <button key={t} type="button" aria-pressed={threshold === t} className={threshold === t ? 'active' : ''} onClick={() => setThreshold(t)}>≥ {Number(t) * 100}%</button>)}
      </fieldset>
      {matrix ? <>
        <div className="table-scroll"><table className="zone-queue-table" style={{ maxWidth: 600 }}>
          <caption>{number(matrix.tp + matrix.fn + matrix.fp + matrix.tn)} observations · {horizon} days · threshold {Number(threshold) * 100}%</caption>
          <thead><tr><th scope="col">Observed outcome</th><th scope="col">Predicted failure</th><th scope="col">Predicted no failure</th></tr></thead>
          <tbody>
            <tr><th scope="row">Actual failure</th><td>{number(matrix.tp)} (TP)</td><td>{number(matrix.fn)} (FN)</td></tr>
            <tr><th scope="row">Actual no failure</th><td>{number(matrix.fp)} (FP)</td><td>{number(matrix.tn)} (TN)</td></tr>
          </tbody>
        </table></div>
        <p className="metric-note">Precision {fmt(matrix.precision, 3)} · Recall {fmt(matrix.recall, 3)} · F1 {fmt(matrix.f1, 3)}</p>
      </> : <p className="metric-note">No confusion matrix was saved for this selection.</p>}
    </section>
    <section className="panel mc-panel"><div className="section-header"><div><h2>Curves and calibration · {horizon}-day horizon</h2><p>{modeLabel}</p></div></div>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          <CurveChart title="ROC curve (test)" series={[{ name: `${horizon}-day selected`, points: curves.roc.map(p => [p[0], p[1]]), color: '#2563eb' }]} xLabel="False positive rate" yLabel="True positive rate" />
          <CurveChart title="Precision–recall curve (test)" series={[{ name: `${horizon}-day selected`, points: curves.precision_recall.map(p => [p[0], p[1]]), color: '#059669' }]} xLabel="Recall" yLabel="Precision" />
          <CalibrationChart points={calPts} />
        </div>
          {m && <p className="metric-note">ROC-AUC {fmt(m.roc_auc)} · PR-AUC {fmt(m.pr_auc)} · Brier {fmt(m.brier, 5)} · ECE {fmt(m.ece)} · prevalence {fmt(m.prevalence, 4)}</p>}
          {selectedRow && <>
            <h3 style={{ fontSize: 13 }}>Selected model by department and zone (test)</h3>
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
              <div className="table-scroll"><table className="zone-queue-table"><thead><tr><th>Department</th><th>n</th><th>Positives</th><th>ROC-AUC</th><th>Brier</th></tr></thead><tbody>{Object.entries(selectedRow.by_department).map(([g, v]) => <tr key={g}><td>{DEPT_LABELS[g] || g}</td><td>{number(v.n)}</td><td>{number(v.positives)}</td><td>{fmt(v.roc_auc)}</td><td>{fmt(v.brier, 5)}</td></tr>)}</tbody></table></div>
              <div className="table-scroll"><table className="zone-queue-table"><thead><tr><th>Zone</th><th>n</th><th>Positives</th><th>ROC-AUC</th><th>Brier</th></tr></thead><tbody>{Object.entries(selectedRow.by_zone).map(([g, v]) => <tr key={g}><td>{g}</td><td>{number(v.n)}</td><td>{number(v.positives)}</td><td>{fmt(v.roc_auc)}</td><td>{fmt(v.brier, 5)}</td></tr>)}</tbody></table></div>
            </div></>}
    </section>
    <section className="panel mc-panel"><div className="section-header"><div><h2><TrendingUp size={18} /> How models were chosen</h2><p>{artifact.selection.risk} {artifact.selection.duration} {artifact.selection.test_usage}</p></div></div>
      <p className="metric-note">Deployed now — 7-day: <b>{dep.risk['7'].model}</b> ({dep.risk['7'].feature_set} features) · 30-day: <b>{dep.risk['30'].model}</b> ({dep.risk['30'].feature_set}) · 90-day: <b>{dep.risk['90'].model}</b> ({dep.risk['90'].feature_set}) · Duration: <b>{dep.duration.model}</b> ({dep.duration.feature_set}) with a {fmt(dep.buffer_minutes, 1)}-minute validation p90 buffer. Engineered history features evaluated: {artifact.features.engineered_extra.join(', ')}. {artifact.features.engineered_note}</p></section>

    {HORIZONS.map(h => {
      const block = artifact.risk[h]; const rows = [...block.candidates].sort((a, b) => a.validation_brier - b.validation_brier); const isSel = (c: RiskCandidate) => c.model === block.selected.model && c.feature_set === block.selected.feature_set;
      return <section className="panel mc-panel" key={h}><div className="section-header"><div><h2>{h}-day failure risk</h2><p>{block.decision}</p></div><span className="pill green">Deployed: {block.selected.model} ({block.selected.feature_set})</span></div>
        <div className="table-scroll"><table className="zone-queue-table"><thead><tr><th>Model</th><th>Features</th><th>Val Brier ↓</th><th>Test ROC-AUC</th><th>Test PR-AUC</th><th>Test Brier</th><th>ECE</th><th>Recall top 10%</th><th>Train (s)</th><th>Infer (ms/row)</th><th>Status</th></tr></thead><tbody>
          {rows.map((c, i) => <tr key={i} style={isSel(c) ? { fontWeight: 600 } : undefined}><td>{c.model}</td><td>{c.feature_set}</td><td>{fmt(c.validation_brier, 5)}</td><td>{fmt(c.test.roc_auc)}</td><td>{fmt(c.test.pr_auc)}</td><td>{fmt(c.test.brier, 5)}</td><td>{fmt(c.test.ece)}</td><td>{fmt(c.test.top_10_percent.recall, 3)}</td><td>{fmt(c.train_seconds, 1)}</td><td>{fmt(c.infer_ms_per_row, 3)}</td><td>{isSel(c) ? <span className="pill green">Deployed</span> : <span className="pill gray">Candidate</span>}</td></tr>)}
        </tbody></table></div>
        <Bars rows={rows} value={c => c.test.roc_auc ?? 0} label={`Test ROC-AUC by candidate — ${h}-day horizon (same untouched test cohort).`} />
        <p className="metric-note">Calibration: <b>{block.calibration.status}</b>{block.calibration.adopted ? ` (${block.calibration.adopted}, fitted on validation only)` : ''}. {block.calibration.note}</p>
      </section>
    })}


    <section className="panel mc-panel"><div className="section-header"><div><h2>Feature importance (permutation, validation)</h2><p>How much validation Brier worsens when a feature is shuffled. Duplicated signals split importance between them — overdue_days is exactly max(0, last_maintenance_days − 120), and inspection_measurement is a rescaled copy of the same noisy reading as deterioration.</p></div></div>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>{Object.entries(artifact.risk[horizon].feature_importance).map(([name, data]) => <ImportanceBars key={name} title={name} data={data} />)}</div>
    </section>

    <section className="panel mc-panel"><div className="section-header"><div><h2>Maintenance duration prediction</h2><p>{artifact.duration.decision}</p></div><span className="pill green">Deployed: {artifact.duration.selected.model} ({artifact.duration.selected.feature_set})</span></div>
      <div className="table-scroll"><table className="zone-queue-table"><thead><tr><th>Model</th><th>Features</th><th>Val MAE ↓</th><th>Test MAE</th><th>Test RMSE</th><th>Test R²</th><th>Train (s)</th><th>Status</th></tr></thead><tbody>
        {[...artifact.duration.candidates].sort((a, b) => a.validation_mae - b.validation_mae).map((c, i) => { const sel = c.model === artifact.duration.selected.model && c.feature_set === artifact.duration.selected.feature_set; return <tr key={i} style={sel ? { fontWeight: 600 } : undefined}><td>{c.model}</td><td>{c.feature_set}</td><td>{fmt(c.validation_mae, 2)}</td><td>{fmt(c.test.mae, 2)}</td><td>{fmt(c.test.rmse, 2)}</td><td>{fmt(c.test.r2, 3)}</td><td>{fmt(c.train_seconds, 1)}</td><td>{sel ? <span className="pill green">Deployed</span> : <span className="pill gray">Candidate</span>}</td></tr> })}
      </tbody></table></div>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginTop: 12 }}>
        <div><h3 style={{ fontSize: 13, margin: '4px 0' }}>Actual vs predicted duration (test sample, minutes)</h3>
          <svg viewBox="0 0 520 300" style={{ width: '100%', maxWidth: 560, background: 'var(--card,#fff)', border: '1px solid var(--border,#e5e7eb)', borderRadius: 8 }}>
            <line x1={36} y1={264} x2={484} y2={264} stroke="#999" /><line x1={36} y1={36} x2={36} y2={264} stroke="#999" /><line x1={36} y1={264} x2={484} y2={36} stroke="#ccc" strokeDasharray="4 4" />
            {artifact.duration.actual_vs_predicted_sample.map((p, i) => <circle key={i} cx={36 + Math.min(1, p[0] / 420) * 448} cy={264 - Math.min(1, p[1] / 420) * 228} r="2.5" fill="#2563eb" opacity="0.6" />)}
            <text x={260} y={292} textAnchor="middle" fontSize="11">Actual minutes</text><text x={12} y={150} fontSize="11" transform="rotate(-90 12 150)" textAnchor="middle">Predicted minutes</text></svg>
          <p className="metric-note">Seeded sample of {artifact.duration.actual_vs_predicted_sample.length} held-out interventions; the dashed line is perfect prediction. Axes capped at 420 minutes.</p></div>
        <div style={{ flex: 1, minWidth: 260 }}><h3 style={{ fontSize: 13, margin: '4px 0' }}>Deployed duration model — test MAE by department</h3>
          <div className="table-scroll"><table className="zone-queue-table"><thead><tr><th>Department</th><th>n</th><th>MAE (min)</th></tr></thead><tbody>{Object.entries(artifact.duration.selected_test_by_department).map(([g, v]) => <tr key={g}><td>{DEPT_LABELS[g] || g}</td><td>{number(v.n)}</td><td>{fmt(v.mae, 2)}</td></tr>)}</tbody></table></div>
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>{Object.entries(artifact.duration.feature_importance).map(([name, data]) => <ImportanceBars key={name} title={name} data={data} />)}</div></div>
      </div>
    </section>
  </>;
}
