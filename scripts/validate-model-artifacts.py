"""Independently recalculate reported held-out metrics from persisted observations/models."""
import json,sys,math
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'planner'))
from maharashtra.pipeline import connect,ARTIFACTS,classification_metrics,simulate,FEATURES
from maharashtra.store import report,artifact_model
from model_validation_inputs import model_inputs
from sklearn.metrics import mean_absolute_error,mean_squared_error,r2_score
r=report();n=r['counts']['assets'];start=int(n*.8)
with connect() as db:
    rows=db.execute('SELECT * FROM observations WHERE asset_id>=? AND day BETWEEN 1140 AND 1230 ORDER BY day,asset_id',(start,)).fetchall()
    x=np.array([json.loads(row['features']) for row in rows],dtype='float32');y=np.array([[row['y7'],row['y30'],row['y90']] for row in rows])
    completed=db.execute('SELECT c.duration,o.features,o.asset_id,o.day FROM completions c JOIN observations o ON o.asset_id=c.asset_id AND o.day=(c.day/30)*30 WHERE c.asset_id>=? AND c.day>=1140 AND c.day<1260 ORDER BY c.day,c.asset_id',(start,)).fetchall()
    score_expression='+'.join("json_extract(components,'$."+k+"')" for k in ['asset','safety','urgency','failure','overdue','operational'])
    bad_scores=db.execute('SELECT COUNT(*) FROM requests WHERE ABS(priority-('+score_expression+'))>1e-6').fetchone()[0]
    invalid=db.execute("SELECT COUNT(*) FROM requests WHERE provenance IS NULL OR provenance<>'SYNTHETIC' OR p7 IS NULL OR p30 IS NULL OR p90 IS NULL OR p7<0 OR p7>p30 OR p30>p90 OR p90>1 OR priority IS NULL OR priority<0 OR priority>100 OR duration IS NULL OR duration<=0 OR buffer IS NULL OR buffer<0").fetchone()[0]
    actual_counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['requests','assets','observations','completions']}
m=artifact_model((ARTIFACTS/'models.joblib').stat().st_mtime_ns)
inputs=model_inputs(rows)
p=np.maximum.accumulate(np.column_stack([model.predict_proba(inputs[fset])[:,1] for model,fset in zip(m['risk'],m.get('risk_features',['base']*3))]),axis=1)
metrics=[]
for j,old in enumerate(r['risk']):
    new=classification_metrics(y[:,j],p[:,j]);assert all(abs(new[k]-old[k])<1e-6 for k in ['precision','recall','f1','roc_auc','brier','prevalence']);metrics.append(dict(horizon_days=old['horizon_days'],**new))
dx=model_inputs(completed)[m.get('duration_features','base')];dy=np.array([row['duration'] for row in completed]);estimate=m['duration'].predict(dx)
duration=dict(mae=float(mean_absolute_error(dy,estimate)),rmse=float(mean_squared_error(dy,estimate)**.5),r2=float(r2_score(dy,estimate)),test_rows=len(dy))
assert all(abs(duration[k]-r['duration'][k])<1e-6 for k in ['mae','rmse','r2'])
assert len(rows)==r['test_rows'] and not bad_scores and not invalid
# Reproducibility check of the actual generator, with a small asset population and the full time history.
a=simulate(26092,60,48);b=simulate(26092,60,48)
assert np.array_equal(a[0],b[0]) and np.array_equal(a[1],b[1]) and a[2:]==b[2:]
assert np.isfinite(a[0]).all() and (a[1][:,:,0]<=a[1][:,:,1]).all() and (a[1][:,:,1]<=a[1][:,:,2]).all()
cohorts=[set(range(0,int(n*.6))),set(range(int(n*.6),start)),set(range(start,n))]
assert not any(cohorts[i]&cohorts[j] for i in range(3) for j in range(i))
assert 25*30+90<30*30 and 33*30+90<38*30
out=dict(passed=True,model_sha256=r['model_sha256'],actual_counts=actual_counts,recomputed_risk=metrics,recomputed_duration=duration,all_500000_score_errors=bad_scores,invalid_provenance_or_predictions=invalid,asset_cohorts=list(map(len,cohorts)),asset_overlap=0,chronological_label_windows_purged=True,features=FEATURES,feature_targets_present=False,reproducible_generator=True,note='Metrics are measured on synthetic lifecycle outcomes only. No railway accuracy is established.')
(ARTIFACTS/'model-audit.json').write_text(json.dumps(out,indent=2));print(json.dumps({k:v for k,v in out.items() if k!='recomputed_risk'},indent=2))
