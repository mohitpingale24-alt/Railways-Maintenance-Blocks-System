"""Integration tests against the actual generated store and saved model artifacts."""
import copy
import math
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from maharashtra import store
from maharashtra.network import network
from maharashtra.timetable import catalogue, movements, coverage
from maharashtra.pipeline import connect, ARTIFACTS
from engine import generate, validate, windows, effective_tasks, compatible

class MaharashtraTests(unittest.TestCase):
    def test_counts_relationships_and_all_department_history(self):
        report=store.report();self.assertEqual(report['counts']['requests'],500000)
        with connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests WHERE observation_id NOT IN (SELECT id FROM observations)').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests WHERE asset_id NOT IN (SELECT id FROM assets)').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests WHERE priority<0 OR priority>100 OR p7>p30 OR p30>p90').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM requests WHERE provenance<>'SYNTHETIC'").fetchone()[0],0)
            expression='+'.join("json_extract(components,'$."+key+"')" for key in store.WEIGHTS)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests WHERE ABS(priority-('+expression+'))>0.000001').fetchone()[0],0)
            for dep in ['TRK','SIG','TRC']:
                self.assertGreater(report['counts']['departments'][dep]['history'],100000)

    def test_unique_sections_and_valid_filters(self):
        g=network();edges=[tuple(sorted((s['from_station'],s['to_station']))) for s in g['sections']]
        self.assertEqual(len(edges),len(set(edges)))
        for c in g['corridors']:
            for sid in c['sections']:
                self.assertEqual(next(s for s in g['sections'] if s['id']==sid)['zone'],c['zone'])
        with self.assertRaises(ValueError):store.selection(dict(zone='Z1',corridor='Pune-Daund'))
        with self.assertRaises(ValueError):store.selection(dict(zone='Z2',section='UNKNOWN'))
        self.assertEqual(len(coverage()),12)
        self.assertEqual(len(catalogue()),53)

    def test_zone_department_summary_and_weighted_batch_queue(self):
        summary=store.zone_summary();self.assertEqual(len(summary['zones']),4)
        self.assertEqual(sum(z['requests'] for z in summary['zones']),500000)
        for z in summary['zones']:
            self.assertEqual(sum(z['departments'].values()),z['pending'])
            self.assertGreater(z['pending'],0)
        params=dict(zone='Z1',corridor='Mumbai-Kalyan',section='all')
        batch=store.dataset(params)
        weights=dict(asset=0,safety=0,urgency=0,failure=100,overdue=0,operational=0)
        rows,total,batch_total=store.query(params,15,0,True,weights,0)
        self.assertEqual(total,batch['counts']['selected_pending'])
        self.assertEqual(len(rows),15)
        self.assertGreater(batch_total,15)
        self.assertTrue(all(t['id'] in {x['id'] for x in batch['tasks']} for t in rows))
        self.assertTrue(all(abs(t['priority']-sum(t['components'].values()))<1e-9 for t in rows))
        sig_rows,_,sig_batch_total=store.query(dict(params,department='SIG'),15,0,True,weights,300)
        second=store.dataset(dict(params,batch_offset=300))
        self.assertGreater(sig_batch_total,0)
        self.assertTrue(all(t['department']=='SIG' and t['id'] in {x['id'] for x in second['tasks']} for t in sig_rows))

    def test_scores_and_actual_inference(self):
        with connect() as db:
            row=db.execute('SELECT o.features FROM observations o WHERE id=499999').fetchone()
        import json
        features=json.loads(row[0]);first=store.infer(features)
        self.assertAlmostEqual(first['priority'],sum(first['components'].values()))
        for component in first['score_details'].values():self.assertAlmostEqual(component['points'],round(component['normalized']*component['weight'],1))
        self.assertLessEqual(first['risk']['7'],first['risk']['30']);self.assertLessEqual(first['risk']['30'],first['risk']['90'])
        features[4]=.98;features[7]=5.8;features[6]+=150;features[2]+=150
        second=store.infer(features)
        self.assertNotEqual(first['priority'],second['priority']);self.assertNotEqual(first['risk'],second['risk'])
        self.assertGreater(first['duration_buffer'],0)
        rows,total,_=store.query(dict(zone='Z1',search='inspection'),limit=15,pending=True)
        self.assertGreater(total,0);self.assertTrue(all('inspection' in t['task_type'].lower() for t in rows))
        with self.assertRaises(ValueError):store.infer([float('nan')]*9)

    def test_timetable_provenance_and_date_recomputation(self):
        refs=movements('2026-11-18',7)
        self.assertTrue(all('source' in m and m['train_no'].isdigit() for m in refs))
        known_days={s['train_no'] for s in catalogue() if s['service_days'] is not None}
        self.assertTrue(all(m['train_no'] in known_days for m in refs))
        synthetic=movements('2026-11-18',7,'synthetic')
        self.assertTrue(all(m['train_no'].startswith('SYN-') for m in synthetic))
        self.assertTrue(all(not c['valid_for_dispatch'] and not c['freight_complete'] for c in coverage()))
        self.assertNotEqual(refs[0]['service_id'],movements('2026-11-19',7)[0]['service_id'])
        selected=store.dataset(dict(zone='Z1',corridor='Churchgate-Virar',start_date='2026-11-18',horizon=7,traffic_mode='reference'))
        section=selected['sections'][0]['id']
        first_slot=next(w for w in selected['candidate_windows'] if w['section']==section and w['day']==0)
        self.assertEqual(first_slot['end'],355)  # 5-minute headway before the 06:00 reference movement
        self.assertIn('92213',first_slot['boundary_trains'])
        self.assertEqual(first_slot['available_duration'],first_slot['end']-first_slot['start'])
        self.assertEqual(first_slot['conflicting_trains'],[])
        self.assertTrue(first_slot['eligibility_conditions'])

    def test_multi_section_service_uses_published_section_stop_times(self):
        refs=[m for m in movements('2026-11-18',1) if m['train_no']=='17617' and m['start']>=0]
        by_section={m['section']:(m['start'],m['end'],m['time_scope'],m['from_station'],m['to_station']) for m in refs}
        # erail schedule checked 2026-11-17: MMR dep 10:45, AWB arr 13:05 / dep 13:10, PBN arr 16:18 / dep 16:20, NED arr 18:00.
        self.assertEqual(by_section['MH-SEC-11'],(645,785,'section','MMR','AWB'))
        self.assertEqual(by_section['MH-SEC-12'],(790,978,'section','AWB','PBN'))
        self.assertEqual(by_section['MH-SEC-13'],(980,1080,'section','PBN','NED'))
        # 12139 Akola dep 01:05, Badnera arr 02:25 / dep 02:30, Wardha arr 03:47 / dep 03:50, Nagpur arr 05:46 (erail, checked 2026-11-17).
        sewagram={m['section']:(m['start'],m['end'],m['time_scope']) for m in movements('2026-11-18',1) if m['service_id'].startswith('REF-12139-2026-11-18-') and m['origin']=='AK'}
        self.assertEqual(sewagram['MH-SEC-15'],(65,145,'section'))
        self.assertEqual(sewagram['MH-SEC-16'],(150,227,'section'))
        self.assertEqual(sewagram['MH-SEC-17'],(230,346,'section'))
        # Overnight reverse direction: 17412 Miraj dep 21:55, Satara arr 00:12 / dep 00:15 the next day, Pune arr 03:25. The Satara-Pune leg must not land a day early.
        mahalaxmi={m['section']:(m['start'],m['end']) for m in movements('2026-11-18',1) if m['service_id'].startswith('REF-17412-2026-11-18-') and m['origin']=='MRJ'}
        self.assertEqual(mahalaxmi['MH-SEC-08'],(1315,1452))
        self.assertEqual(mahalaxmi['MH-SEC-07'],(1455,1645))

    def test_every_multi_section_daily_service_has_checked_stop_times(self):
        for record in catalogue():
            if record['service_days'] is not None and len(record['sections'])>1:
                with self.subTest(train=record['train_no'],route=f"{record['origin']}-{record['destination']}"):
                    self.assertIn('stops',record)
        # With every multi-section service covered, no reference movement may
        # present a full-route interval as a section time.
        self.assertTrue(all(m['time_scope']=='section' for m in movements('2026-11-18',1)))

    def test_pune_lonavala_overnight_references_protect_candidate_windows(self):
        data=store.dataset(dict(zone='Z2',corridor='Pune-Lonavala',start_date='2026-11-18',horizon=7,traffic_mode='reference'))
        section=data['sections'][0]['id']
        traffic=[m for m in data['traffic'] if m['section']==section and m['start']<360 and m['end']>0]
        self.assertTrue({'11020','11140','22157','22158','17317','17412'} <= {m['train_no'] for m in traffic})
        overnight=next(m for m in traffic if m['train_no']=='11020')
        self.assertEqual((overnight['start'],overnight['end']),(-20,40))
        slots=[w for w in data['candidate_windows'] if w['section']==section and w['day']==0]
        self.assertTrue(slots)
        for slot in slots:
            for movement in traffic:
                self.assertTrue(slot['end']<=movement['start']-5 or slot['start']>=movement['end']+5)
        self.assertNotIn((0,360),[(w['start'],w['end']) for w in slots])

    def test_all_corridors_have_references_and_exclude_known_movements(self):
        refs=coverage()
        self.assertEqual(len(refs),12)
        self.assertTrue(all(c['date_applicable_references']>0 for c in refs))
        for c in refs:
            with self.subTest(corridor=c['corridor']):
                data=store.dataset(dict(zone=c['zone'],corridor=c['corridor'],start_date='2026-11-18',horizon=7,traffic_mode='reference'))
                self.assertTrue(data['traffic'])
                self.assertTrue(all(any(m['section']==s['id'] for m in data['traffic']) for s in data['sections']))
                for slot in data['candidate_windows']:
                    for movement in data['traffic']:
                        if movement['section']==slot['section']:
                            self.assertTrue(slot['end']<=movement['start']-5 or slot['start']>=movement['end']+5)

    def test_all_zones_and_solver_constraints(self):
        for z in ['Z1','Z2','Z3','Z4']:
            cfg=dict(zone=z,start_date='2026-11-18',horizon=7,corridor='all',section='all',traffic_mode='reference',incidents=[])
            d=store.dataset({k:v for k,v in cfg.items() if k!='incidents'})
            self.assertGreater(d['counts']['selected_pending'],0)
            self.assertTrue(all(t['zone']==z for t in d['tasks']))
            # Bounded test solve, still using real persisted predictions and movement inputs.
            d['tasks']=([t for t in d['tasks'] if t['safety_action']=='NORMAL'][:25]+[t for t in d['tasks'] if t['safety_action']!='NORMAL'][:5])
            p=generate(d,cfg)
            self.assertEqual(validate(d,p),[])
            self.assertGreater(len(p['blocks']),0)
            ids={t for b in p['blocks'] for t in b['tasks']};held={t['id'] for t in d['tasks'] if t['safety_action']=='STOP_AND_REVIEW'}
            self.assertFalse(ids&held)
            self.assertEqual(ids|{u['id'] for u in p['unscheduled']},{t['id'] for t in d['tasks']})
            if p['blocks']:
                b=p['blocks'][0];changed=copy.deepcopy(d)
                changed['movements'].append(dict(section=b['section'],start=b['start'],end=b['end'],kind='Freight',service_id='TEST'))
                self.assertTrue(validate(changed,p))
                self.assertNotEqual(windows(d,b['section'],b['duration'],0,7,[]),windows(changed,b['section'],b['duration'],0,7,[]))
                broken=copy.deepcopy(p);broken['blocks'][0]['end']+=1
                self.assertTrue(validate(d,broken))

    def test_competing_priority_and_duration_recomputation(self):
        cfg=dict(zone='Z2',corridor='Pune-Lonavala',start_date='2026-11-18',horizon=7,incidents=[])
        d=store.dataset({k:v for k,v in cfg.items() if k!='incidents'})
        t=copy.deepcopy(next(t for t in d['tasks'] if t['safety_action']=='NORMAL'))
        t.update(id='HIGH',priority=95,duration=180,due_day=0,dependencies=[],machine=None)
        low=copy.deepcopy(t);low.update(id='LOW',priority=5)
        d['tasks']=[low,t];d['movements']=[]
        emergency=effective_tasks(d,dict(cfg,incidents=[{'kind':'urgent','task_id':'HIGH'}]))
        self.assertEqual(emergency[1]['priority'],95)
        power=copy.deepcopy(t);power.update(department='TRC',power_required=True,separation_confirmed=False)
        self.assertFalse(compatible(t,power))
        p=generate(d,cfg)
        self.assertEqual({tid for b in p['blocks'] for tid in b['tasks']},{'HIGH'})
        self.assertEqual(validate(d,p),[])
        d['tasks'][1]['duration']=400
        changed=generate(d,cfg)
        self.assertEqual({tid for b in changed['blocks'] for tid in b['tasks']},{'LOW'})

    def test_monthly_scope_resource_fingerprint_and_saved_predictions(self):
        import json
        cfg=dict(zone='Z4',corridor='Nagpur-Gondia',section='all',start_date='2026-11-18',horizon=30,resource_overrides={})
        d=store.dataset(cfg);changed=store.dataset(dict(cfg,resource_overrides={'TRK':0}))
        self.assertNotEqual(d['fingerprint'],changed['fingerprint'])
        unchanged=store.dataset(dict(cfg,horizon='30'))
        self.assertEqual(d['fingerprint'],unchanged['fingerprint'])
        d['tasks']=[t for t in d['tasks'] if t['safety_action']=='NORMAL'][:15]
        p=generate(d,dict(cfg,incidents=[]));self.assertEqual(validate(d,p),[]);self.assertGreater(len(p['blocks']),0)
        with connect() as db:
            row=db.execute('SELECT r.*,o.features FROM requests r JOIN observations o ON r.observation_id=o.id WHERE r.id=500000').fetchone()
        result=store.infer(json.loads(row['features']),row['asset_id'],row['observation_id'])
        self.assertAlmostEqual(result['risk']['30'],row['p30'],places=7)
        self.assertEqual(int(math.ceil(result['predicted_duration'])),row['duration'])

    def test_no_gap_and_resource_shortage_defers(self):
        cfg=dict(zone='Z2',start_date='2026-11-18',horizon=7,incidents=[])
        d=store.dataset({k:v for k,v in cfg.items() if k!='incidents'});d['tasks']=[t for t in d['tasks'] if t['safety_action']=='NORMAL'][:12]
        for s in d['sections']:d['movements'].append(dict(section=s['id'],start=0,end=7*1440))
        self.assertEqual(generate(d,cfg)['blocks'],[])
        d['movements']=[];d['resources'].update(TRK=0,SIG=0,TRC=0)
        self.assertEqual(generate(d,cfg)['blocks'],[])

if __name__=='__main__':unittest.main()
