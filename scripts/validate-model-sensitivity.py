"""Measure saved-model sensitivity on held-out synthetic observations.

One feature is varied at a time between holdout deciles; all other features are
held fixed. These artificial pairs can be outside the joint training distribution.
They are diagnostics, not a calibration test or causal intervention estimate.
"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'planner'))
from maharashtra.pipeline import ARTIFACTS, FEATURES, connect
from maharashtra.store import artifact_model, report
from model_validation_inputs import model_inputs

r = report()
with connect() as db:
    rows = db.execute('SELECT asset_id,day,features FROM observations WHERE asset_id>=? AND day BETWEEN 1140 AND 1230 ORDER BY day,asset_id', (int(r['counts']['assets']*.8),)).fetchall()
x = np.array([json.loads(row['features']) for row in rows], dtype='float32')
artifact = artifact_model((ARTIFACTS/'models.joblib').stat().st_mtime_ns)
models = artifact['risk']
def predict(a):
    inputs = model_inputs(rows, a)
    return np.maximum.accumulate(np.column_stack([m.predict_proba(inputs[fset])[:, 1] for m,fset in zip(models,artifact.get('risk_features',['base']*3))]), axis=1)
out = {'model_sha256': r['model_sha256'], 'rows': len(x), 'method': 'Held-out synthetic observations; vary one current feature at its 10th and 90th percentiles while holding other current features and recorded prior inspections fixed. Derived features are recomputed. Maximum accumulation across horizons matches serving. Hypothetical pairs may be outside the joint distribution.', 'comparisons': {}}
for feature in ['deterioration', 'last_maintenance_days']:
    i = FEATURES.index(feature)
    low, high = np.quantile(x[:,i], [.1,.9])
    a=x.copy(); b=x.copy(); a[:,i]=low; b[:,i]=high
    pa,pb=predict(a),predict(b)
    assert np.isfinite(pa).all() and np.isfinite(pb).all() and (pa>=0).all() and (pb<=1).all()
    diff=pb-pa
    out['comparisons'][feature]={'low':float(low),'high':float(high),'horizons':{str(h):{'mean_low':float(pa[:,j].mean()),'mean_high':float(pb[:,j].mean()),'mean_delta_percentage_points':float(diff[:,j].mean()*100),'fraction_increased':float((diff[:,j]>1e-8).mean()),'fraction_decreased':float((diff[:,j]<-1e-8).mean()),'fraction_unchanged':float((abs(diff[:,j])<=1e-8).mean())} for j,h in enumerate([7,30,90])}}
print(json.dumps(out,indent=2))
