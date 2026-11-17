"""Focused timetable, saved-model and in-process engine endpoint checks."""
import io
import json
import math
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault('RAILBLOCK_ENGINE_TOKEN', 'in-process-test-token')
import server
from engine import windows, generate, validate
from maharashtra import store, timetable


class TimetableWorkflowTests(unittest.TestCase):
    def endpoint(self, path, body=None, authorized=True):
        token=server.TOKEN if authorized else 'incorrect-token'
        raw=json.dumps(body).encode() if body is not None else b''
        result=[]
        fake=SimpleNamespace(path=path, headers={'X-Engine-Token':token,'Content-Length':str(len(raw))},
                             rfile=io.BytesIO(raw), respond=lambda value,status=200:result.append((status,value)))
        (server.Handler.do_POST if body is not None else server.Handler.do_GET)(fake)
        return result[0]

    def test_weekday_overnight_and_unknown_days(self):
        sample=dict(train_no='TEST-WEEKLY',corridor='Pune-Lonavala',origin='PUNE',destination='LNL',
                    departure='23:50',arrival='00:20',service_days=[0],source='test fixture',version='test fixture')
        unknown=dict(sample,train_no='TEST-UNKNOWN',service_days=None)
        with patch.object(timetable,'catalogue',return_value=[sample,unknown]):
            monday=timetable.movements('2026-11-16',2)
            tuesday=timetable.movements('2026-11-17',2)
        self.assertEqual([(x['start'],x['end']) for x in monday],[(1430,1460)])
        self.assertEqual([(x['start'],x['end']) for x in tuesday],[(-10,20)])
        self.assertTrue(all(x['train_no']=='TEST-WEEKLY' for x in monday+tuesday))

    def test_overlapping_movements_headway_and_no_gap(self):
        data={'movements':[{'section':'S','start':45,'end':100},{'section':'S','start':90,'end':180},
                           {'section':'OTHER','start':0,'end':360}]}
        self.assertEqual(windows(data,'S',30,0,1,[]),[(0,10),(185,330)])
        self.assertEqual(windows({'movements':[{'section':'S','start':0,'end':360}]},'S',1,0,1,[]),[])

    def test_inprocess_engine_api_selected_section_models_and_plan(self):
        status,_=self.endpoint('/dataset?zone=Z2',authorized=False)
        self.assertEqual(status,403)
        params={'zone':'Z1','corridor':'Mumbai-Kalyan','section':'MH-SEC-01','start_date':'2026-11-18','horizon':7,'traffic_mode':'reference'}
        status,data=self.endpoint('/dataset?zone=Z1&corridor=Mumbai-Kalyan&section=MH-SEC-01&start_date=2026-11-18&horizon=7&traffic_mode=reference')
        self.assertEqual(status,200)
        self.assertEqual({s['id'] for s in data['sections']},{params['section']})
        self.assertTrue(data['traffic'])
        self.assertTrue(all(m['section']==params['section'] and m.get('source') for m in data['traffic']))
        self.assertTrue(all(not c['valid_for_dispatch'] and not c['freight_complete'] for c in data['coverage']))
        t=next(t for t in data['tasks'] if t['safety_action']=='NORMAL')
        # Identify the stored observation so the engineered-history
        # duration model rebuilds the same lag features the pipeline used
        # when it stored this task's prediction; a features-only call uses
        # the no-history fallback and cannot reproduce it.
        status,pred=self.endpoint('/maintenance/infer',{'features':t['observation_features'],'asset_id':int(t['asset_id'].split('-')[-1]),'observation_id':t['inspection_history']['observation_id']})
        self.assertEqual(status,200)
        for horizon in ('7','30','90'):self.assertAlmostEqual(pred['risk'][horizon],t['risk'][horizon],places=6)
        self.assertEqual(math.ceil(pred['predicted_duration']),t['predicted_duration'])
        self.assertAlmostEqual(t['priority'],sum(t['components'].values()),places=6)
        data['tasks']=[t for t in data['tasks'] if t['safety_action']=='NORMAL'][:12]
        cfg=dict(params,incidents=[])
        with patch.object(server,'selected_data',return_value=data):
            status,plan=self.endpoint('/generate',{'config':cfg})
        self.assertEqual(status,200,plan)
        self.assertIn(plan['solver_status'],('FEASIBLE','OPTIMAL'))
        self.assertEqual(validate(data,plan),[])
        self.assertEqual(plan['allocation_status'],'CONDITIONAL/SIMULATED')
        self.assertTrue(plan['blocks'])
        for b in plan['blocks']:
            self.assertEqual(b['section'],params['section'])
            self.assertIsNotNone(b['candidate_slot'])
            self.assertEqual(b['candidate_slot']['conflicting_trains'],[])
            self.assertTrue(any(w['section']==b['section'] and w['start']<=b['start'] and b['end']<=w['end'] for w in data['candidate_windows']))

if __name__=='__main__':unittest.main()
