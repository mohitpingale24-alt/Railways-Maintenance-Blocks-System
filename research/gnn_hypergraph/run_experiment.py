"""Graph-signal experiment for RailBlock AI.

Question: does information from CONNECTED assets (same section / nearby on the
line) improve failure prediction beyond the asset's own 9 features? This is the
information a GNN would propagate (1-hop aggregation). If aggregated neighbour
features carry no signal on the pipeline's own asset-disjoint chronological
split, a GNN cannot either. If they do, the gain is real and worth a GNN.

Data: proj9 simulation.sqlite (identical deterministic simulation, seed 26092).
Split (mirrors planner/maharashtra/pipeline.py): train months 0-25 assets
0-60%, validation months 30-33 assets 60-80%, test months 38-41 assets 80-100%.
Metrics: ROC-AUC, PR-AUC, Brier, Recall@10% (recall within top 10% scored).
"""
import sqlite3, json, time
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss

DB = '/home/hatch/workspace/railblock_audit/proj9/.railblock/simulation.sqlite'
c = sqlite3.connect(DB)

assets = c.execute('SELECT id, section, fraction FROM assets ORDER BY id').fetchall()
n_assets = len(assets)
section_of = np.array([a[1] for a in assets])
fraction_of = np.array([a[2] for a in assets])

rows = c.execute('SELECT asset_id, day, features, y7, y30, y90 FROM observations').fetchall()
# sqlite `day` is the simulation day; the pipeline grid is monthly (day // 30).
n_months = max(r[1] for r in rows) // 30 + 1
X = np.zeros((n_months, n_assets, 9), dtype=np.float64)
Y = np.zeros((n_months, n_assets, 3), dtype=np.int64)
for aid, day, feats, y7, y30, y90 in rows:
    X[day // 30, aid] = json.loads(feats)
    Y[day // 30, aid] = (y7, y30, y90)
print(f'grid: {n_months} months x {n_assets} assets')

DET, DEF, LOAD, MEAS = 4, 1, 3, 7  # feature indices

# ---- graph (neighbourhood) features, same month, excluding self ----
sections = sorted(set(section_of.tolist()))
sec_index = {s: np.where(section_of == s)[0] for s in sections}
G = np.zeros((n_months, n_assets, 6), dtype=np.float64)
for m in range(n_months):
    det = X[m, :, DET]; dfc = X[m, :, DEF]
    for s, idx in sec_index.items():
        d = det[idx]; f = dfc[idx]; n = len(idx)
        mean_d = (d.sum() - d) / (n - 1)
        mean_f = (f.sum() - f) / (n - 1)
        frac_high = ((d > 0.63).sum() - (d > 0.63)) / (n - 1)
        mx = d.max()
        second = np.where(d == mx, -np.inf, d).max() if n > 1 else d
        max_excl = np.where(d == mx, second, mx)
        G[m, idx, 0] = mean_d          # section mean deterioration (excl self)
        G[m, idx, 1] = max_excl        # section worst deterioration (excl self)
        G[m, idx, 2] = frac_high       # fraction of section assets above repair trigger
        G[m, idx, 3] = mean_f          # section mean previous defects
    # nearest neighbours along the line (by fraction within section, k=6)
    for s, idx in sec_index.items():
        order = idx[np.argsort(fraction_of[idx])]
        dvals = det[order]
        for pos, a in enumerate(order):
            lo, hi = max(0, pos - 3), min(len(order), pos + 4)
            neigh = np.delete(dvals[lo:hi], pos - lo)
            G[m, a, 4] = neigh.mean() if len(neigh) else dvals[pos]
            G[m, a, 5] = neigh.max() if len(neigh) else dvals[pos]
print('graph features built')

def split(months, lo, hi):
    a = np.arange(int(n_assets * lo), int(n_assets * hi))
    pairs = np.array([(m, x) for m in months for x in a])
    return pairs

splits = {
    'train': split(range(0, 26), 0.0, 0.6),
    'validation': split(range(30, 34), 0.6, 0.8),
    'test': split(range(38, 42), 0.8, 1.0),
}

def rows_for(grid, pairs):
    return grid[pairs[:, 0], pairs[:, 1]]

def recall_at_k(y, p, frac=0.10):
    k = max(1, int(len(y) * frac))
    top = np.argsort(-p)[:k]
    return float(y[top].sum() / max(1, y.sum()))

def metrics(y, p):
    return dict(roc_auc=round(float(roc_auc_score(y, p)), 4),
                pr_auc=round(float(average_precision_score(y, p)), 4),
                brier=round(float(brier_score_loss(y, p)), 4),
                recall_at_10pct=round(recall_at_k(y, p), 4))

XG = np.concatenate([X, G], axis=2)
results = {}
for j, h in enumerate([7, 30, 90]):
    for label, grid in [('base9', X), ('base9+graph6', XG)]:
        tx = rows_for(grid, splits['train']); ty = Y[splits['train'][:, 0], splits['train'][:, 1], j]
        ex = rows_for(grid, splits['test']); ey = Y[splits['test'][:, 0], splits['test'][:, 1], j]
        for name, make in [('LogReg', lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))),
                           ('HistGBM', lambda: HistGradientBoostingClassifier(random_state=26092))]:
            t0 = time.time()
            model = make(); model.fit(tx, ty)
            p = model.predict_proba(ex)[:, 1]
            results[f'{h}d|{label}|{name}'] = dict(**metrics(ey, p), seconds=round(time.time() - t0, 1))
            print(f'{h}d {label:14s} {name:8s}', results[f'{h}d|{label}|{name}'], flush=True)

json.dump(results, open('/home/hatch/workspace/gnn_experiment/results.json', 'w'), indent=1)
print('\nDeployed reference (retained bundle, Mac): ROC-AUC 7d 0.6607 / 30d 0.6428 / 90d 0.6060')
