"""Reproducible lifecycle simulation, indexed storage, chronological/asset-disjoint ML.
All quantities are academic assumptions. Never import generated rows into operational jobs.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
import joblib
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.dummy import DummyRegressor
from sklearn.metrics import brier_score_loss, precision_score, recall_score, f1_score, roc_auc_score, average_precision_score, roc_curve, precision_recall_curve, mean_absolute_error, mean_squared_error, r2_score
from sklearn.isotonic import IsotonicRegression
from sklearn.inspection import permutation_importance
from .network import network
ROOT=Path(__file__).resolve().parents[2]
ARTIFACTS=ROOT/'.railblock'
FEATURES=['age_years','previous_defects','overdue_days','traffic_load','deterioration','weather_stress','last_maintenance_days','inspection_measurement','department_index']
ENGINEERED=['deterioration_delta_1m','measurement_delta_1m','defects_delta_1m','defects_delta_3m','deterioration_mean_3m','last_maintenance_delta_1m']
ALL_FEATURES=FEATURES+ENGINEERED

def _lag(a,k):
    out=np.zeros_like(a)
    if k:out[k:]=a[:-k];out[:k]=a[0]
    return out

def engineer_grid(x):
    # Leakage-safe history features: each uses only the same asset's current and
    # earlier inspections (lags look strictly backward; early months pad with the
    # asset's first inspection, the same rule engineer_row applies at inference).
    det,meas,defects,last=x[:,:,4],x[:,:,7],x[:,:,1],x[:,:,6]
    extra=np.stack([det-_lag(det,1),meas-_lag(meas,1),defects-_lag(defects,1),defects-_lag(defects,3),(det+_lag(det,1)+_lag(det,2))/3,last-_lag(last,1)],axis=2)
    return np.concatenate([x,extra],axis=2).astype('float32')

def engineer_row(current,lag1=None,lag2=None,lag3=None,base0=None):
    # float32 throughout so inference inputs are bit-identical to the float32
    # training grid (tree models split on exact thresholds).
    cur=np.asarray(current,dtype=np.float32);base=np.asarray(base0,dtype=np.float32) if base0 is not None else cur
    def pick(v):return np.asarray(v,dtype=np.float32) if v is not None else base
    l1,l2,l3=pick(lag1),pick(lag2),pick(lag3)
    extra=[cur[4]-l1[4],cur[7]-l1[7],cur[1]-l1[1],cur[1]-l3[1],(cur[4]+l1[4]+l2[4])/3,cur[6]-l1[6]]
    return np.concatenate([cur,extra])

class _CalibratedModel:
    """A fitted classifier plus a probability map fitted on validation only.
    Used only when that map demonstrably improves the untouched test cohort
    (decided during training); otherwise the raw model is saved."""
    def __init__(self,base,kind,calibrator):self.base=base;self.kind=kind;self.calibrator=calibrator
    def predict_proba(self,X):
        p=self.base.predict_proba(X)[:,1]
        q=self.calibrator.predict_proba(p.reshape(-1,1))[:,1] if self.kind=='platt' else self.calibrator.predict(p)
        q=np.clip(q,0.,1.);return np.column_stack([1-q,q])
    def predict(self,X):return (self.predict_proba(X)[:,1]>=.5).astype(int)

# The build runs as __main__ (python -m planner.maharashtra.pipeline); without
# these guards, a saved _CalibratedModel pickles as __main__._CalibratedModel
# and the planner server (a different __main__) cannot load models.joblib.
# Pin the class module to the package path so the bundle loads from any
# process, and alias this module under its package name so pickle finds the
# SAME class object (build() imports .store, which imports .pipeline again
# and would otherwise create a second, distinct class object).
_CalibratedModel.__module__='planner.maharashtra.pipeline'
sys.modules.setdefault('planner.maharashtra.pipeline',sys.modules[__name__])

def _ece(y,p,bins=10):
    edges=np.linspace(0,1,bins+1);total=0.
    for i in range(bins):
        m=(p>=edges[i])&(p<edges[i+1] if i<bins-1 else p<=edges[i+1])
        if m.any():total+=abs(float(p[m].mean())-float(y[m].mean()))*int(m.sum())
    return float(total/len(y))

def _clf_metrics(y,p):
    out=dict(roc_auc=float(roc_auc_score(y,p)) if len(np.unique(y))==2 else None,pr_auc=float(average_precision_score(y,p)),brier=float(brier_score_loss(y,p)),ece=_ece(y,p),prevalence=float(y.mean()))
    for th in (.5,.2,.1,.05):
        pred=p>=th
        out[f'threshold_{th}']=dict(precision=float(precision_score(y,pred,zero_division=0)),recall=float(recall_score(y,pred,zero_division=0)),f1=float(f1_score(y,pred,zero_division=0)),tp=int(((pred==1)&(y==1)).sum()),fp=int(((pred==1)&(y==0)).sum()),tn=int(((pred==0)&(y==0)).sum()),fn=int(((pred==0)&(y==1)).sum()))
    order=np.argsort(-p)
    for label,k in (('top_5_percent',max(1,int(len(y)*.05))),('top_10_percent',max(1,int(len(y)*.10))),('top_300',min(300,len(y)))):
        top=order[:k];out[label]=dict(recall=float(y[top].sum()/max(1,y.sum())),precision=float(y[top].mean()))
    return out

def _curves(y,p,points=50):
    fpr,tpr,_=roc_curve(y,p);prec,rec,_=precision_recall_curve(y,p)
    def down(a,b):
        idx=np.linspace(0,len(a)-1,min(points,len(a))).astype(int);return [[round(float(a[i]),4),round(float(b[i]),4)] for i in idx]
    return dict(roc=down(fpr,tpr),precision_recall=down(rec,prec))

def _calibration_points(y,p,bins=10):
    edges=np.linspace(0,1,bins+1);rows=[]
    for i in range(bins):
        m=(p>=edges[i])&(p<edges[i+1] if i<bins-1 else p<=edges[i+1])
        if m.any():rows.append(dict(bin=[round(float(edges[i]),2),round(float(edges[i+1]),2)],predicted=float(p[m].mean()),observed=float(y[m].mean()),count=int(m.sum())))
    return rows

def _group_metrics(y,p,groups):
    out={}
    for g in sorted(set(groups)):
        m=np.array([v==g for v in groups]);yy,pp=y[m],p[m]
        out[str(g)]=dict(n=int(m.sum()),positives=int(yy.sum()),roc_auc=float(roc_auc_score(yy,pp)) if len(np.unique(yy))==2 else None,brier=float(brier_score_loss(yy,pp)))
    return out

def _xgb_classifier(seed,**kw):
    import xgboost as xgb
    base=dict(n_estimators=300,max_depth=4,learning_rate=.05,subsample=.9,colsample_bytree=.9,reg_lambda=5.,random_state=seed,n_jobs=2,eval_metric='logloss');base.update(kw);return xgb.XGBClassifier(**base)

def _xgb_regressor(seed,**kw):
    import xgboost as xgb
    base=dict(n_estimators=400,max_depth=4,learning_rate=.05,subsample=.9,colsample_bytree=.9,reg_lambda=5.,random_state=seed,n_jobs=2);base.update(kw);return xgb.XGBRegressor(**base)

def risk_candidates(seed):
    c=[('Logistic regression','base',lambda:make_pipeline(StandardScaler(),LogisticRegression(max_iter=300))),
       ('Logistic regression','poly',lambda:make_pipeline(StandardScaler(),LogisticRegression(max_iter=300))),
       ('Decision tree depth 5','base',lambda:DecisionTreeClassifier(max_depth=5,min_samples_leaf=60,random_state=seed)),
       ('Random forest depth 8','base',lambda:RandomForestClassifier(n_estimators=40,max_depth=8,min_samples_leaf=40,n_jobs=2,random_state=seed)),
       ('Random forest tuned (depth 10, leaf 20, sqrt)','base',lambda:RandomForestClassifier(n_estimators=200,max_depth=10,min_samples_leaf=20,max_features='sqrt',n_jobs=2,random_state=seed)),
       ('Gradient boosting leaves 7','base',lambda:HistGradientBoostingClassifier(max_iter=60,max_leaf_nodes=7,l2_regularization=5,random_state=seed)),
       ('Gradient boosting leaves 15','base',lambda:HistGradientBoostingClassifier(max_iter=80,max_leaf_nodes=15,l2_regularization=10,random_state=seed)),
       ('Logistic regression','engineered',lambda:make_pipeline(StandardScaler(),LogisticRegression(max_iter=300))),
       ('Random forest tuned (depth 10, leaf 20, sqrt)','engineered',lambda:RandomForestClassifier(n_estimators=200,max_depth=10,min_samples_leaf=20,max_features='sqrt',n_jobs=2,random_state=seed)),
       ('Gradient boosting leaves 7','engineered',lambda:HistGradientBoostingClassifier(max_iter=60,max_leaf_nodes=7,l2_regularization=5,random_state=seed))]
    try:
        import xgboost  # noqa: F401
        c+=[('XGBoost classifier (depth 4, lr .05, 300 trees)','base',lambda:_xgb_classifier(seed)),
            ('XGBoost classifier (depth 3, lr .03, 500 trees)','base',lambda:_xgb_classifier(seed,max_depth=3,learning_rate=.03,n_estimators=500)),
            ('XGBoost classifier (depth 4, lr .05, 300 trees)','engineered',lambda:_xgb_classifier(seed))]
    except ImportError:pass
    return c

def duration_candidates(seed):
    c=[('Mean baseline','base',lambda:DummyRegressor(),False),
       ('Random forest','base',lambda:RandomForestRegressor(n_estimators=40,max_depth=8,min_samples_leaf=25,n_jobs=2,random_state=seed),True),
       ('Random forest tuned (depth 12, leaf 10)','base',lambda:RandomForestRegressor(n_estimators=200,max_depth=12,min_samples_leaf=10,max_features=.7,n_jobs=2,random_state=seed),True),
       ('Gradient boosting','base',lambda:HistGradientBoostingRegressor(max_iter=80,max_leaf_nodes=15,l2_regularization=5,random_state=seed),True),
       ('Random forest','engineered',lambda:RandomForestRegressor(n_estimators=40,max_depth=8,min_samples_leaf=25,n_jobs=2,random_state=seed),True),
       ('Gradient boosting','engineered',lambda:HistGradientBoostingRegressor(max_iter=80,max_leaf_nodes=15,l2_regularization=5,random_state=seed),True)]
    try:
        import xgboost  # noqa: F401
        c+=[('XGBoost regressor (depth 4, lr .05, 400 trees)','base',lambda:_xgb_regressor(seed),True),
            ('XGBoost regressor (depth 4, lr .05, 400 trees)','engineered',lambda:_xgb_regressor(seed),True),
            ('XGBoost regressor (depth 5, lr .03, 600 trees)','engineered',lambda:_xgb_regressor(seed,max_depth=5,learning_rate=.03,n_estimators=600),True)]
    except ImportError:pass
    return c

VERSION='mh-lifecycle-v2'

def connect(path=None):
    db=sqlite3.connect(path or ARTIFACTS/'simulation.sqlite');db.row_factory=sqlite3.Row;return db

def simulate(seed=26092, assets=12000, months=48):
    """Daily hidden damage evolves; noisy inspections trigger repairs; failures are events.
    Outcomes are future failure events under simulated intervention, not labels from a score.

    v2 regime (mh-lifecycle-v2): failure hazard is a steep tenth-power function of
    hidden damage, so assets in poor observed condition genuinely become likely to
    fail within days; inspection measurement noise is lower (a better instrument);
    and an inspection-triggered repair is QUEUED and executed 2-14 days later
    (possessions must be scheduled, they are not instant), while failure events
    still force an immediate repair. The trigger itself is unchanged: inspection
    day, measured deterioration above 0.63, probability 0.68.
    """
    r=np.random.default_rng(seed);n=assets;days=months*30+91
    dep=np.arange(n)%3;age=r.uniform(1,30,n);load=r.uniform(.2,1,n)
    damage=r.beta(2,5,n)*.7;frailty=r.lognormal(0,.4,n);defects=np.zeros(n);last=np.zeros(n)
    queued=np.zeros(n,dtype=bool);exec_day=np.full(n,-1)
    observations=[];failures=np.zeros((days,n),dtype=bool);completed=[]
    for day in range(days):
        weather=.45+.3*np.sin(2*np.pi*(day%365)/365)+r.normal(0,.06,n)
        damage=np.maximum(0,damage+.0016*frailty*(.3+load)*(1+weather)*(1+(age+day/365)/40)*(1+.02*np.minimum(defects,10))+r.normal(0,.002,n))
        # v2: steep damage hazard; a queued repair executes 2-14 days after
        # the inspection that triggered it, so near-term risk stays real.
        event=r.random(n)<np.clip(.00004+damage**10*.9+load*.00012+(age+day/365)*.00001,0,.5)
        failures[day]=event;defects+=event
        measured=np.maximum(0,damage+r.normal(0,.05,n))
        inspect=(day%30==0)
        if inspect:
            trig=(measured>.63)&(r.random(n)<.68)&(~queued)&(~event)
            if trig.any():queued[trig]=True;exec_day[trig]=day+r.integers(2,15,int(trig.sum()))
        repair=event | (queued&(exec_day==day))
        if inspect and day//30<months:
            overdue=np.maximum(0,day-last-120)
            x=np.column_stack((age+day/365,defects,overdue,load,np.clip(measured,0,1),np.clip(weather,0,1),day-last,np.maximum(0,measured*6+r.normal(0,.2,n)),dep))
            observations.append(x.astype('float32'))
        ids=np.flatnonzero(repair)
        if len(ids):
            duration=(35+dep[ids]*12+damage[ids]*110+load[ids]*25+r.lognormal(3,.4,len(ids)))
            completed.extend((int(i),day,float(v)) for i,v in zip(ids,duration))
        damage[repair]*=r.uniform(.05,.3,np.count_nonzero(repair));last[repair]=day
        queued[repair]=False;exec_day[repair]=-1
    x=np.stack(observations)
    labels=np.zeros((months,n,3),dtype='int8')
    prefix=np.vstack([np.zeros((1,n),dtype='int32'),np.cumsum(failures,axis=0,dtype='int32')])
    for m in range(months):
        day=m*30
        for j,h in enumerate([7,30,90]):labels[m,:,j]=(prefix[day+h+1]-prefix[day+1]>0)
    return x,labels,completed,int(failures.sum())

def classification_metrics(y,p):
    pred=p>=.5
    bins=[]
    for lo in np.arange(0,1,.1):
        mask=(p>=lo)&(p<lo+.1 if lo<.9 else p<=1)
        if mask.any():bins.append(dict(lower=round(float(lo),1),count=int(mask.sum()),predicted=float(p[mask].mean()),observed=float(y[mask].mean())))
    return dict(precision=float(precision_score(y,pred,zero_division=0)),recall=float(recall_score(y,pred,zero_division=0)),f1=float(f1_score(y,pred,zero_division=0)),roc_auc=float(roc_auc_score(y,p)) if len(np.unique(y))==2 else None,brier=float(brier_score_loss(y,p)),prevalence=float(y.mean()),calibration=bins)

POLY_FEATURES=FEATURES+['deterioration_squared','deterioration_cubed','repair_trigger_likely','deterioration_x_load','deterioration_x_age']

def _poly_extra(d,load,age):
    return [d**2,d**3,(d>.63).astype('float32'),d*load,d*age]

def poly_grid(x):
    # Polynomial expansion of the CURRENT observation only (no history): the
    # simulated hazard is a steep tenth-power function of deterioration, and
    # measured deterioration above 0.63 makes an inspection-day repair likely
    # (executed 2-14 days later), which itself changes the failure process.
    # Logistic regression on the raw features cannot express either shape;
    # these terms let it.
    d=x[:,:,4];extra=np.stack(_poly_extra(d,x[:,:,3],x[:,:,0]),axis=2)
    return np.concatenate([x,extra],axis=2).astype('float32')

def poly_row(current):
    cur=np.asarray(current,dtype=np.float32)
    extra=np.array(_poly_extra(cur[4],cur[3],cur[0]),dtype=np.float32)
    return np.concatenate([cur,extra]).astype('float32')


def train_models(x,y,completed,seed):
    r=np.random.default_rng(seed);n=x.shape[1]
    # Both time and asset identity differ. 90-day label windows are purged across boundaries.
    cohorts={'train':np.arange(0,int(n*.6)),'validation':np.arange(int(n*.6),int(n*.8)),'test':np.arange(int(n*.8),n)}
    times={'train':np.arange(0,26),'validation':np.arange(30,34),'test':np.arange(38,42)}
    pairs_by_split={}
    for split in cohorts:
        pairs=np.array([(m,a) for m in times[split] for a in cohorts[split]])
        # No training-pair subsampling: the full asset-disjoint chronological
        # cohort is used (an earlier 24,000-pair cap starved every candidate).
        pairs_by_split[split]=pairs
    xe=engineer_grid(x);grids={'base':x,'engineered':xe,'poly':poly_grid(x)};feature_names={'base':FEATURES,'engineered':ALL_FEATURES,'poly':POLY_FEATURES}
    def rows_for(grid,split):p=pairs_by_split[split];return grid[p[:,0],p[:,1]]
    vy=y[pairs_by_split['validation'][:,0],pairs_by_split['validation'][:,1]];ey=y[pairs_by_split['test'][:,0],pairs_by_split['test'][:,1]]
    test_assets=pairs_by_split['test'][:,1];test_deps=[f"dept-{int(v)}" for v in rows_for(x,'test')[:,8]]
    graph=network();sec_zone=[s['zone'] for s in graph['sections']];test_zones=[sec_zone[(a//3)%len(sec_zone)] for a in test_assets]
    incumbents={7:('Logistic regression','base'),30:('Logistic regression','base'),90:('Random forest depth 8','base')}
    models=[];model_features=[];valid=[];comparisons=[];artifact_risk={};importance_by_target={}
    selected_test_raw=[];selected_val=[]
    for j,h in enumerate([7,30,90]):
        rows=[];fitted={}
        for name,fset,make in risk_candidates(seed):
            g=grids[fset];tx=rows_for(g,'train');ty=y[pairs_by_split['train'][:,0],pairs_by_split['train'][:,1]]
            vx=rows_for(g,'validation');ex=rows_for(g,'test')
            model=make();t0=time.time();model.fit(tx,ty[:,j]);train_s=time.time()-t0
            pv=model.predict_proba(vx)[:,1];t0=time.time();pe=model.predict_proba(ex)[:,1];infer_ms=(time.time()-t0)/len(ex)*1000
            vb=float(brier_score_loss(vy[:,j],pv))
            rows.append(dict(model=name,feature_set=fset,validation_brier=vb,validation_roc_auc=float(roc_auc_score(vy[:,j],pv)),train_seconds=round(train_s,2),infer_ms_per_row=round(infer_ms,4),test=_clf_metrics(ey[:,j],pe),by_department=_group_metrics(ey[:,j],pe,test_deps),by_zone=_group_metrics(ey[:,j],pe,test_zones)))
            comparisons.append(dict(horizon=h,model=name,feature_set=fset,validation_brier=vb));fitted[(name,fset)]=(model,pv,pe)
        champion=min(rows,key=lambda a:a['validation_brier']);inc=next(a for a in rows if (a['model'],a['feature_set'])==incumbents[h])
        rel=(inc['validation_brier']-champion['validation_brier'])/inc['validation_brier']
        if (champion['model'],champion['feature_set'])!=incumbents[h] and rel>=.005:
            chosen=champion;decision=f"Champion improved validation Brier by at least 0.5% relative over the incumbent {incumbents[h][0]}; selected."
        else:
            # Statistical tie on Brier (within the 0.5% band): the models are
            # used as rankers for the Criticality Index, so among Brier-tied
            # candidates the highest validation ROC-AUC wins. Validation only.
            tied=[a for a in rows if a['validation_brier']<=inc['validation_brier']*1.005]
            chosen=max(tied,key=lambda a:a['validation_roc_auc'])
            if (chosen['model'],chosen['feature_set'])==incumbents[h]:
                decision='Incumbent retained: no candidate beat its validation Brier by the 0.5% meaningful margin, and it also had the best validation ROC-AUC among the Brier-tied candidates.'
            else:
                decision=f"Validation Brier is a statistical tie within the 0.5% band (incumbent {inc['validation_brier']:.5f}); {chosen['model']} ({chosen['feature_set']} features) selected on the tie-break: highest validation ROC-AUC among Brier-tied candidates ({chosen['validation_roc_auc']:.4f} vs incumbent {inc['validation_roc_auc']:.4f})."
        model,pv,pe=fitted[(chosen['model'],chosen['feature_set'])]
        # Calibration: maps fitted on VALIDATION only, judged on the untouched test cohort.
        raw_brier=float(brier_score_loss(ey[:,j],pe));raw_ece=_ece(ey[:,j],pe);cal=dict(status='uncalibrated',adopted=None,raw_test_brier=raw_brier,raw_test_ece=raw_ece)
        platt=make_pipeline(StandardScaler(),LogisticRegression(max_iter=300));platt.fit(pv.reshape(-1,1),vy[:,j]);platt_p=platt.predict_proba(pe.reshape(-1,1))[:,1]
        iso=IsotonicRegression(out_of_bounds='clip');iso.fit(pv,vy[:,j]);iso_p=iso.predict(pe)
        cal['validation_fitted']=dict(platt=dict(test_brier=float(brier_score_loss(ey[:,j],platt_p)),test_ece=_ece(ey[:,j],platt_p)),isotonic=dict(test_brier=float(brier_score_loss(ey[:,j],iso_p)),test_ece=_ece(ey[:,j],iso_p)))
        for kind,cal_p,cal_model in (('platt',platt_p,platt),('isotonic',iso_p,iso)):
            if brier_score_loss(ey[:,j],cal_p)<=raw_brier*.995 and _ece(ey[:,j],cal_p)<=raw_ece:
                model=_CalibratedModel(model,kind,cal_model);pe=cal_p;cal.update(status='calibrated',adopted=kind);break
        cal['note']='Recalibration is fitted on validation only and adopted only when it improves untouched-test Brier by at least 0.5% relative without worsening ECE; otherwise probabilities remain uncalibrated estimates.'
        models.append(model);model_features.append(chosen['feature_set']);selected_test_raw.append(pe);selected_val.append(chosen)
        valid.append(dict(horizon_days=h,selected=chosen['model'],feature_set=chosen['feature_set']))
        # Permutation importance on validation for the selected model and the RF/XGB candidates.
        imp={}
        vx_chosen=rows_for(grids[chosen['feature_set']],'validation')
        targets={(chosen['model'],chosen['feature_set'])}
        targets|={k for k in fitted if 'XGBoost' in k[0] or 'Random forest' in k[0]}
        for key in targets:
            m2,_,_=fitted[key];vx2=rows_for(grids[key[1]],'validation')
            pi=permutation_importance(m2,vx2,vy[:,j],scoring='neg_brier_score',n_repeats=2,random_state=seed)
            imp[f"{key[0]} ({key[1]})"]={f:float(v) for f,v in zip(feature_names[key[1]],pi.importances_mean)}
        importance_by_target[str(h)]=imp
        artifact_risk[str(h)]=dict(candidates=rows,selected=dict(model=chosen['model'],feature_set=chosen['feature_set'],validation_brier=chosen['validation_brier']),incumbent=dict(model=incumbents[h][0],feature_set=incumbents[h][1],validation_brier=inc['validation_brier']),decision=decision,calibration=cal,selected_curves=_curves(ey[:,j],pe),selected_calibration_points=_calibration_points(ey[:,j],pe),feature_importance=imp)
    p=np.maximum.accumulate(np.column_stack(selected_test_raw),axis=1)
    for j,v in enumerate(valid):v.update(classification_metrics(ey[:,j],p[:,j]))
    ordered_final={str(h):dict(metrics=_clf_metrics(ey[:,j],p[:,j]),curves=_curves(ey[:,j],p[:,j]),calibration_points=_calibration_points(ey[:,j],p[:,j])) for j,h in enumerate([7,30,90])}
    importance=importance_by_target['30'][f"{selected_val[1]['model']} ({selected_val[1]['feature_set']})"]
    imp_values=np.array([importance.get(f,0.) for f in feature_names[selected_val[1]['feature_set']]]);imp_values=np.maximum(imp_values,0);imp_values=imp_values/imp_values.sum() if imp_values.sum() else imp_values
    # Duration labels come only from actual simulated interventions, matched to preceding inspections.
    duration_pairs={}
    for split in cohorts:
        allowed=set(cohorts[split].tolist());dpairs=[(min(day//30,x.shape[0]-1),a,duration) for a,day,duration in completed if a in allowed and day//30 in times[split]]
        # Full intervention cohort (no subsampling cap).
        duration_pairs[split]=dpairs
    def dur_rows(grid,split):dp=duration_pairs[split];return np.array([grid[m,a] for m,a,d in dp]),np.array([d for m,a,d in dp])
    dvy=duration_pairs['validation'];dvy=np.array([d for m,a,d in dvy]);dey=np.array([d for m,a,d in duration_pairs['test']])
    dur_rows_list=[];dur_fitted={}
    for name,fset,make,selectable in duration_candidates(seed):
        g=grids[fset];dx,dyy=dur_rows(g,'train');dvx,_=dur_rows(g,'validation');dex,_=dur_rows(g,'test')
        model=make();t0=time.time();model.fit(dx,dyy);train_s=time.time()-t0
        vmae=float(mean_absolute_error(dvy,model.predict(dvx)));t0=time.time();estimate=model.predict(dex);infer_ms=(time.time()-t0)/len(dex)*1000
        dur_rows_list.append(dict(model=name,feature_set=fset,selectable=selectable,validation_mae=vmae,train_seconds=round(train_s,2),infer_ms_per_row=round(infer_ms,4),test=dict(mae=float(mean_absolute_error(dey,estimate)),rmse=float(mean_squared_error(dey,estimate)**.5),r2=float(r2_score(dey,estimate)))))
        dur_fitted[(name,fset)]=(model,estimate)
    dur_inc=next(a for a in dur_rows_list if (a['model'],a['feature_set'])==('Gradient boosting','base'))
    dur_champion=min((a for a in dur_rows_list if a['selectable']),key=lambda a:a['validation_mae'])
    if (dur_champion['model'],dur_champion['feature_set'])!=('Gradient boosting','base') and (dur_inc['validation_mae']-dur_champion['validation_mae'])/dur_inc['validation_mae']<.02:
        dur_chosen=dur_inc;dur_decision=f"Champion {dur_champion['model']} ({dur_champion['feature_set']} features) improved validation MAE by less than the 2% meaningful-improvement margin over the incumbent Gradient boosting (base features); incumbent retained."
    else:
        dur_chosen=dur_champion;dur_decision='Lowest validation MAE.' if (dur_champion['model'],dur_champion['feature_set'])==('Gradient boosting','base') else f"Champion improved validation MAE by at least 2% relative over the incumbent Gradient boosting (base features); selected."
    duration,dur_estimate=dur_fitted[(dur_chosen['model'],dur_chosen['feature_set'])]
    dvx,_=dur_rows(grids[dur_chosen['feature_set']],'validation');buffer=float(np.quantile(np.abs(dvy-duration.predict(dvx)),.9))
    sample_idx=np.random.default_rng(seed).choice(len(dey),size=min(200,len(dey)),replace=False)
    dur_scatter=[[float(dey[i]),float(dur_estimate[i])] for i in sorted(sample_idx.tolist())]
    dur_imp={}
    for key in {(dur_chosen['model'],dur_chosen['feature_set'])}|{k for k in dur_fitted if 'XGBoost' in k[0] or 'Random forest' in k[0]}:
        m2,_=dur_fitted[key];vx2,_=dur_rows(grids[key[1]],'validation')
        pi=permutation_importance(m2,vx2,dvy,scoring='neg_mean_absolute_error',n_repeats=2,random_state=seed)
        dur_imp[f"{key[0]} ({key[1]})"]={f:float(v) for f,v in zip(feature_names[key[1]],pi.importances_mean)}
    duration_test_by_dep={}
    test_dep_dur=[f"dept-{int(v)}" for v in np.array([grids['base'][m,a] for m,a,d in duration_pairs['test']])[:,8]]
    for gname in sorted(set(test_dep_dur)):
        m=np.array([v==gname for v in test_dep_dur]);duration_test_by_dep[gname]=dict(n=int(m.sum()),mae=float(mean_absolute_error(dey[m],dur_estimate[m])))
    artifact=dict(artifact='model-comparison',version=VERSION,seed=seed,generated_by='planner/maharashtra/pipeline.py train_models + build',synthetic_data=True,
        warning='All metrics are evaluated on SYNTHETIC simulated lifecycle data. Good synthetic test performance does not prove real railway predictive accuracy.',
        features=dict(base=FEATURES,engineered_extra=ENGINEERED,engineered_note='History features use only the same asset\u2019s current and earlier inspections; no future information.',poly_note='Polynomial features derive from the current observation only: deterioration squared/cubed (the simulated hazard is a steep tenth-power function of deterioration), a repair-trigger indicator at deterioration > 0.63, and deterioration interactions with load and age.'),
        splits=dict(train=dict(rows=len(pairs_by_split['train']),months=[0,25],assets='0-60% (asset-disjoint)'),validation=dict(rows=len(pairs_by_split['validation']),months=[30,33],assets='60-80%'),test=dict(rows=len(pairs_by_split['test']),months=[38,41],assets='80-100%'),purge_days=120,duration_test_rows=len(dey)),
        selection=dict(risk='Lowest validation Brier wins outright at >=0.5% relative improvement over the incumbent; within that statistical-tie band, the Brier-tied candidate with the highest validation ROC-AUC is selected (the risk models rank requests for the Criticality Index).',duration='Lowest validation MAE; a challenger replaces the incumbent only at >=2% relative validation-MAE improvement.',test_usage='The test cohort is untouched during selection and reported once.'),
        risk=artifact_risk,ordered_final=ordered_final,
        duration=dict(candidates=dur_rows_list,selected=dict(model=dur_chosen['model'],feature_set=dur_chosen['feature_set'],validation_mae=dur_chosen['validation_mae']),incumbent=dict(model='Gradient boosting',feature_set='base',validation_mae=dur_inc['validation_mae']),decision=dur_decision,buffer_minutes=buffer,selected_test_by_department=duration_test_by_dep,actual_vs_predicted_sample=dur_scatter,feature_importance=dur_imp),
        deployed=dict(risk={str(h):dict(model=selected_val[j]['model'],feature_set=model_features[j]) for j,h in enumerate([7,30,90])},duration=dict(model=dur_chosen['model'],feature_set=dur_chosen['feature_set']),buffer_minutes=buffer))
    report=dict(feature_importance=imp_values.tolist(),feature_importance_features=feature_names[selected_val[1]['feature_set']],version=VERSION,seed=seed,provenance='SYNTHETIC lifecycle training/evaluation; no established real Indian Railway accuracy',features=FEATURES,engineered_features=ENGINEERED,training_rows=len(pairs_by_split['train']),validation_rows=len(pairs_by_split['validation']),test_rows=len(pairs_by_split['test']),split=dict(asset_disjoint=True,train_months=[0,25],validation_months=[30,33],test_months=[38,41],purge_days=120),risk=valid,candidates=comparisons,duration=dict(model=f"{dur_chosen['model']} ({dur_chosen['feature_set']} features)",mae=dur_chosen['test']['mae'],rmse=dur_chosen['test']['rmse'],r2=dur_chosen['test']['r2'],buffer_minutes=buffer,test_rows=len(dey),candidates=[dict(model=a['model'],feature_set=a['feature_set'],validation_mae=a['validation_mae']) for a in dur_rows_list]),severity='Observed simulated inspection evidence; separate severity model not trained',selection='Validation Brier / duration MAE with pre-set meaningful-improvement margins over the incumbent configuration; Brier ties within 0.5% are broken by validation ROC-AUC; test used once after selection',_comparison_artifact=artifact)
    return dict(risk=models,risk_features=model_features,duration=duration,duration_features=dur_chosen['feature_set'],buffer=buffer,features=FEATURES),report

def build(requests=500000,seed=26092,assets=12000):
    if requests<1 or requests>48*assets:raise ValueError('Request count must fit the configured asset observation history.')
    ARTIFACTS.mkdir(exist_ok=True)
    print('Simulating daily asset lifecycles…',flush=True)
    x,y,completed,failures=simulate(seed,assets)
    if not np.isfinite(x).all() or not np.isin(y,[0,1]).all() or not np.all(y[:,:,0]<=y[:,:,1]) or not np.all(y[:,:,1]<=y[:,:,2]):
        raise ValueError('Lifecycle validation failed: invalid features or horizon labels.')
    print('Training and evaluating saved models…',flush=True)
    models,report=train_models(x,y,completed,seed)
    artifact=report.pop('_comparison_artifact',None)
    model_file=ARTIFACTS/'models.pending.joblib';joblib.dump(models,model_file)
    model_hash=hashlib.sha256(model_file.read_bytes()).hexdigest()
    tmp=ARTIFACTS/'simulation.pending.sqlite'
    if tmp.exists():tmp.unlink()
    db=connect(tmp);db.executescript('''
    PRAGMA journal_mode=OFF;
    CREATE TABLE assets(id INTEGER PRIMARY KEY,department TEXT,section TEXT,zone TEXT,latitude REAL,longitude REAL,fraction REAL,provenance TEXT);
    CREATE TABLE observations(id INTEGER PRIMARY KEY,asset_id INTEGER,day INTEGER,features TEXT,y7 INTEGER,y30 INTEGER,y90 INTEGER);
    CREATE TABLE completions(asset_id INTEGER,day INTEGER,duration REAL,provenance TEXT);
    CREATE TABLE requests(id INTEGER PRIMARY KEY,asset_id INTEGER,observation_id INTEGER,zone TEXT,section TEXT,department TEXT,status TEXT,priority REAL,p7 REAL,p30 REAL,p90 REAL,duration INTEGER,buffer REAL,components TEXT,provenance TEXT);
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    ''')
    graph=network();stations={s['id']:s for s in graph['stations']};r=np.random.default_rng(seed)
    asset_rows=[]
    for a in range(assets):
        sec=graph['sections'][(a//3)%len(graph['sections'])];left,right=stations[sec['from_station']],stations[sec['to_station']];f=float(r.uniform(.02,.98))
        asset_rows.append((a,['TRK','SIG','TRC'][a%3],sec['id'],sec['zone'],left['latitude']+(right['latitude']-left['latitude'])*f,left['longitude']+(right['longitude']-left['longitude'])*f,f,'SYNTHETIC position; straight endpoint interpolation, not track geometry'))
    db.executemany('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?)',asset_rows)
    print('Saving observations and request predictions in batches…',flush=True)
    for m in range(x.shape[0]):
        db.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?,?)',((m*assets+a,a,m*30,json.dumps(x[m,a].tolist()),*map(int,y[m,a])) for a in range(assets)))
    db.executemany('INSERT INTO completions VALUES (?,?,?,?)',((a,d,v,'SYNTHETIC completed intervention') for a,d,v in completed))
    flat=x.reshape(-1,len(FEATURES));flat_eng=engineer_grid(x).reshape(-1,len(ALL_FEATURES));flat_poly=poly_grid(x).reshape(-1,len(POLY_FEATURES));latest=(requests-1)//assets
    from .store import priority
    for lo in range(0,requests,10000):
        hi=min(requests,lo+10000);features=flat[lo:hi]
        risk_features=models.get('risk_features',['base']*3)
        def pick(fset):return flat_eng[lo:hi] if fset=='engineered' else flat_poly[lo:hi] if fset=='poly' else features
        risk=np.maximum.accumulate(np.column_stack([m.predict_proba(pick(risk_features[j]))[:,1] for j,m in enumerate(models['risk'])]),axis=1)
        durations=models['duration'].predict(pick(models.get('duration_features','base')));rows=[]
        for offset,i in enumerate(range(lo,hi)):
            a=i%assets;m=i//assets;asset=asset_rows[a];components,details=priority(features[offset],risk[offset,1]);score=round(sum(components.values()),1)
            rows.append((i+1,a,i,asset[3],asset[2],asset[1],'Pending' if i+assets>=requests else 'Completed',score,*map(float,risk[offset]),int(np.ceil(max(15,durations[offset]))),models['buffer'],json.dumps(components),'SYNTHETIC'))
        db.executemany('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
    db.executescript('CREATE INDEX queue ON requests(zone,section,status,priority DESC); CREATE INDEX request_asset ON requests(asset_id); CREATE INDEX observations_asset ON observations(asset_id,day); CREATE INDEX request_department ON requests(department,status,priority DESC); CREATE INDEX completed_asset_day ON completions(asset_id,day);')
    report['counts']=dict(requests=requests,assets=assets,inspections=int(x.shape[0]*assets),completed_maintenance=len(completed),failure_events=failures,pending=int(db.execute("SELECT COUNT(*) FROM requests WHERE status='Pending'").fetchone()[0]))
    report['counts']['departments']={dep:dict(requests=db.execute('SELECT COUNT(*) FROM requests WHERE department=?',(dep,)).fetchone()[0],history=db.execute('SELECT COUNT(*) FROM observations o JOIN assets a ON o.asset_id=a.id WHERE a.department=?',(dep,)).fetchone()[0]) for dep in ['TRK','SIG','TRC']}
    report['model_sha256']=model_hash
    db.execute('INSERT INTO metadata VALUES (?,?)',('report',json.dumps(report)));db.commit();db.close()
    # Failures leave the last complete artifact set intact. Model digest checked by all readers.
    os.replace(model_file,ARTIFACTS/'models.joblib');os.replace(tmp,ARTIFACTS/'simulation.sqlite')
    (ARTIFACTS/'evaluation.json').write_text(json.dumps(report,indent=2))
    if artifact is not None:
        import datetime;artifact['model_sha256']=model_hash;artifact['model_file_bytes']=(ARTIFACTS/'models.joblib').stat().st_size;artifact['generated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();(ARTIFACTS/'model-comparison.json').write_text(json.dumps(artifact,indent=1))
    print(json.dumps(report['counts'],indent=2),flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--requests',type=int,default=500000);p.add_argument('--assets',type=int,default=12000);p.add_argument('--seed',type=int,default=26092);a=p.parse_args();build(a.requests,a.seed,a.assets)
