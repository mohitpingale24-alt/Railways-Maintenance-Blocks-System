"""Evaluate served probabilities on untouched synthetic test assets and dates.

Bootstrap resamples assets, retaining their repeated observations; this accounts
for within-asset dependence but not other shared simulation effects. Quantile bins
are descriptive and sparse event counts limit conclusions.
"""
import json, sys
from pathlib import Path
import numpy as np
from sklearn.metrics import brier_score_loss, log_loss
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'planner'))
from maharashtra.pipeline import ARTIFACTS, connect
from maharashtra.store import artifact_model, report

r=report(); n=r['counts']['assets']; start=int(.8*n)
with connect() as db:
    test=db.execute('SELECT asset_id,features,y7,y30,y90 FROM observations WHERE asset_id>=? AND day BETWEEN 1140 AND 1230 ORDER BY asset_id,day',(start,)).fetchall()
    val=db.execute('SELECT AVG(y7),AVG(y30),AVG(y90) FROM observations WHERE asset_id>=? AND asset_id<? AND day BETWEEN 900 AND 990',(int(.6*n),start)).fetchone()
x=np.array([json.loads(row['features']) for row in test],dtype=np.float32)
y=np.array([[row['y7'],row['y30'],row['y90']] for row in test])
assets=np.array([row['asset_id'] for row in test]); unique,inverse=np.unique(assets,return_inverse=True)
m=artifact_model((ARTIFACTS/'models.joblib').stat().st_mtime_ns)['risk']
raw=np.column_stack([model.predict_proba(x)[:,1] for model in m]); served=np.maximum.accumulate(raw,axis=1)
assert len(x)==r['test_rows'] and np.isfinite(served).all() and (served>=0).all() and (served<=1).all()
assert (np.diff(served,axis=1)>=0).all() and len(unique)==n-start
rng=np.random.default_rng(92713); repetitions=600
# One random bootstrap weight per asset; each of its observations travels together.
weights=rng.multinomial(len(unique),np.full(len(unique),1/len(unique)),size=repetitions)
def interval(mask, values):
    counts=np.bincount(inverse,weights=mask,minlength=len(unique))
    totals=np.bincount(inverse,weights=mask*values,minlength=len(unique))
    denominators=weights@counts; numerators=weights@totals
    valid=denominators>0
    return [float(v) for v in np.quantile(numerators[valid]/denominators[valid],[.025,.975])]
out={'model_sha256':r['model_sha256'],'provenance':'Synthetic holdout only; no railway calibration established','observations':len(x),'assets':len(unique),'bootstrap':{'type':'asset-cluster percentile','replicates':repetitions,'seed':92713},'horizons':[]}
for j,h in enumerate([7,30,90]):
    p=served[:,j]; actual=y[:,j]; bins=[]
    # Stable rank bins give five equally sized groups, without presenting exact cutoffs as decision thresholds.
    for idx in np.array_split(np.argsort(p,kind='stable'),5):
        mask=np.zeros(len(x),dtype=bool); mask[idx]=True
        bins.append({'n':len(idx),'events':int(actual[idx].sum()),'prediction_min':float(p[idx].min()),'prediction_max':float(p[idx].max()),'mean_predicted':float(p[idx].mean()),'observed_rate':float(actual[idx].mean()),'observed_rate_95pct_cluster_bootstrap':interval(mask,actual)})
    allrows=np.ones(len(x),dtype=bool)
    out['horizons'].append({'days':h,'events':int(actual.sum()),'mean_predicted':float(p.mean()),'observed_rate':float(actual.mean()),'observed_minus_predicted_95pct_cluster_bootstrap':interval(allrows,actual-p),'brier':float(brier_score_loss(actual,p)),'baseline_brier_validation_prevalence':float(brier_score_loss(actual,np.full(len(x),val[j]))),'validation_prevalence_baseline':float(val[j]),'log_loss':float(log_loss(actual,p,labels=[0,1])),'raw_brier_before_horizon_ordering':float(brier_score_loss(actual,raw[:,j])),'ordering_changed_rows':int((abs(p-raw[:,j])>1e-12).sum()),'quantile_bins':bins})
(ARTIFACTS/'calibration-audit.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
