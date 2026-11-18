"""Check the deployed saved models against the supplied synthetic database."""
import json
import sys
from pathlib import Path
root = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, root)
sys.path.insert(0, str(Path(root) / 'planner'))
from maharashtra import store
from maharashtra.pipeline import connect

report = store.report()
with connect() as db:
    row = db.execute('SELECT asset_id,observation_id FROM requests WHERE status=? LIMIT 1', ('Pending',)).fetchone()
    observation = db.execute('SELECT features FROM observations WHERE id=?', (row['observation_id'],)).fetchone()
prediction = store.infer(json.loads(observation['features']), row['asset_id'], row['observation_id'])
assert prediction['model_sha256'] == report['model_sha256']
assert all(0 <= value <= 1 for value in prediction['risk'].values())
assert abs(sum(prediction['components'].values()) - prediction['priority']) < 1e-8
print('Saved models, synthetic database and inference validated.')
