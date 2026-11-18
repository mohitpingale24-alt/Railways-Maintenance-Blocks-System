"""Reconstruct saved-model audit inputs from existing inspection history only."""
import json
import numpy as np
from maharashtra.pipeline import connect, engineer_row, poly_row


def model_inputs(rows, current=None):
    """Keep historical inspections fixed when auditing a changed current input."""
    base = np.asarray(current if current is not None else
                      [json.loads(row['features']) for row in rows], dtype='float32')
    assets = [row['asset_id'] for row in rows]
    days = [row['day'] for row in rows]
    with connect() as db:
        history = {(row['asset_id'], row['day']): json.loads(row['features'])
                   for row in db.execute(
                       'SELECT asset_id,day,features FROM observations '
                       'WHERE asset_id BETWEEN ? AND ? AND day BETWEEN ? AND ?',
                       (min(assets), max(assets), max(0, min(days)-90), max(days)))}
    lags = [np.asarray([history[(asset, max(0, day-months*30))]
                        for asset, day in zip(assets, days)], dtype='float32')
            for months in (1, 2, 3)]
    return {'base': base,
            'engineered': engineer_row(base.T, *(lag.T for lag in lags)).T,
            'poly': poly_row(base.T).T}
