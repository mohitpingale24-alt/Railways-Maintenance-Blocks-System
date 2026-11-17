"""Versioned selected-service public references, kept separate from synthetic traffic."""
from datetime import date, timedelta
from .network import network, ROUTES
RESEARCHED='2026-11-17'
DAILY=[0,1,2,3,4,5,6]
# Selected public listings checked for both endpoint times and daily frequency.
# These are historical timetable references, not confirmation of actual running.
PUNE_LONAVALA_CHECKED={
    ('11140','PUNE','LNL'):('01:10','02:05','https://www.confirmtkt.com/train-schedule/11140'),
    ('22158','PUNE','LNL'):('02:00','02:50','https://www.confirmtkt.com/train-schedule/22158'),
    ('17317','PUNE','LNL'):('03:05','04:12','https://tickets.paytm.com/trains/17317-ubl-dr-express'),
    ('17412','PUNE','LNL'):('03:30','04:27','https://www.ixigo.com/trains/17412'),
    ('11020','PUNE','LNL'):('23:40','00:40','https://www.confirmtkt.com/trains/pune-to-lonavala-train-tickets'),
    ('22157','LNL','PUNE'):('01:37','02:50','https://www.confirmtkt.com/train-schedule/22157-csmt-ms-sf-mail'),
}
# Public route listings checked for both frequency and endpoint times.
CHECKED_DAILY={
 ('Pune-Lonavala','PUNE','LNL'):{'11010':('06:05','07:13'),'12124':('07:15','08:06'),'12126':('07:45','08:28')},
 ('Pune-Lonavala','LNL','PUNE'):{'22944':('07:45','08:50'),'22105':('08:00','09:05'),'12127':('08:50','10:00'),'11007':('09:40','11:05'),'11029':('11:10','12:35'),'12123':('19:20','20:25'),'17318':('23:02','00:01')},
 ('Pune-Daund','PUNE','DD'):{'12115':('02:20','03:33'),'22157':('02:55','04:05'),'22944':('08:55','10:30'),'12169':('09:25','10:33'),'11301':('11:45','12:55'),'22159':('16:25','17:30'),'12157':('17:50','18:55'),'11019':('18:00','19:10')},
 ('Pune-Daund','DD','PUNE'):{'22158':('00:35','01:55'),'12116':('01:30','02:45'),'17614':('04:15','05:25'),'22160':('06:55','08:50'),'11014':('08:50','10:15'),'12158':('09:05','10:30'),'22943':('14:10','15:23'),'11302':('14:30','16:05')},
 ('Pune-Miraj','PUNE','MRJ'):{'17318':('00:06','05:00'),'17411':('00:15','05:35'),'11040':('03:40','09:45'),'11029':('12:40','18:52'),'12780':('17:10','22:40')},
 ('Pune-Miraj','MRJ','PUNE'):{'11030':('09:25','15:45'),'11039':('15:50','22:10'),'17317':('21:20','03:00'),'17412':('21:55','03:25'),'12779':('22:45','04:15')},
}
# No unspecified service-day claim: dates are deliberately bounded reference examples.
SERVICES=[
 dict(train_no='92213',name='Western suburban DN',corridor='Churchgate-Virar',origin='CCG',destination='VR',departure='06:00',arrival='07:22',source='https://wr.indianrailways.gov.in/wr-view/HQ/Head%20Quarter/1777701123504-DN%20PTT%20W.E.F.%20%2001.05.2026.pdf',version='PTT78 effective 2026-05-01',service_days=[0,1,2,3,4,5,6]),
 dict(train_no='20111',name='Konkan Kanya',corridor='Panvel-Ratnagiri',origin='PNVL',destination='RN',departure='00:25',arrival='05:00',source='https://indiarailinfo.com/train/pdf/konkan-kanya-sf-express-20111/949/1250/1248',version='2026-06-10 secondary reference',service_days=None),
 dict(train_no='12105',name='Vidarbha Express',corridor='Nashik-Manmad-Bhusawal',origin='NK',destination='MMR',departure='22:25',arrival='23:23',source='https://railtc.in/train/12105-time-table',version='retrieved 2026-11-17',service_days=None),
 dict(train_no='12139',name='Sewagram Express',corridor='Bhusawal-Akola-Nagpur',origin='AK',destination='NGP',departure='01:05',arrival='05:46',source='https://erail.in/train-enquiry/12139',version='retrieved 2026-11-17; erail schedule corroborated by ixigo; replaces secondary listing that showed 05:45',service_days=None),
]
# Verified selected endpoint references; current operating-date completeness remains unconfirmed.
SERVICES.extend([
 dict(train_no='12859',name='Gitanjali Express',corridor='Mumbai-Kalyan',origin='CSMT',destination='KYN',departure='06:00',arrival='06:52',source='https://erail.in/train-enquiry/12859',version='retrieved 2026-11-17; corroborated Paytm schedule',service_days=None),
 dict(train_no='12169',name='Solapur Intercity',corridor='Pune-Solapur',origin='DD',destination='SUR',departure='10:35',arrival='13:15',source='https://www.confirmtkt.com/train-schedule/12169-sur-intercity',version='retrieved 2026-11-17; corroborated ixigo; Pune-Daund interval already catalogued',service_days=[0,1,2,3,4,5,6]),
 dict(train_no='17617',name='Tapovan Express',corridor='Manmad-Sambhajinagar-Nanded',origin='MMR',destination='NED',departure='10:45',arrival='18:00',source='https://erail.in/train-enquiry/17617',version='retrieved 2026-11-17; CPSN corresponds to historical AWB station reference',service_days=None),
 dict(train_no='12859',name='Gitanjali Express',corridor='Nagpur-Gondia',origin='NGP',destination='G',departure='19:00',arrival='20:38',source='https://erail.in/train-enquiry/12859',version='retrieved 2026-11-17; corroborated etrain and Paytm',service_days=None),
 dict(train_no='12105',name='Vidarbha Express',corridor='Nashik-Manmad-Bhusawal',origin='MMR',destination='BSL',departure='23:25',arrival='02:00',source='https://www.confirmtkt.com/trains/manmad-to-bhusawal-train-tickets',version='public listing checked 2026-11-17; actual running unconfirmed',service_days=DAILY),
 dict(train_no='12139',name='Sewagram Express',corridor='Bhusawal-Akola-Nagpur',origin='BSL',destination='AK',departure='22:20',arrival='01:00',source='https://indiarailinfo.com/train/pdf/sewagram-sf-express-12139/1498/203/18',version='public listing checked 2026-11-17; actual running unconfirmed',service_days=DAILY),
 dict(train_no='12655',name='Navjeevan Express',corridor='Nagpur-Wardha-Chandrapur',origin='WR',destination='CD',departure='12:00',arrival='13:29',source='https://www.confirmtkt.com/train-schedule/12655',version='public listing checked 2026-11-17; actual running unconfirmed',service_days=DAILY),
])

CHECKED_SERVICES={
 ('20111','PNVL','RN'):'https://www.confirmtkt.com/train-schedule/20111',
 ('12105','NK','MMR'):'https://www.confirmtkt.com/train-schedule/12105-VIDARBHA-EXPRES',
 ('12139','AK','NGP'):'https://www.confirmtkt.com/train-schedule/12139',
 ('17617','MMR','NED'):'https://www.confirmtkt.com/train-schedule/17617',
 ('12859','CSMT','KYN'):'https://www.confirmtkt.com/train-schedule/12859',
 ('12859','NGP','G'):'https://www.confirmtkt.com/train-schedule/12859',
}
# Published intermediate stop times (arrival, departure) at section-endpoint
# stations for every multi-section service in the catalogue, each taken from
# that train's own erail schedule page (erail.in/train-enquiry/<train_no>),
# checked 2026-11-17 and corroborated against a second listing (ixigo or
# RailYatri; 11030: ixigo + RailYatri, which agree exactly). Only stations
# that are section endpoints in network.py are listed; halts inside a
# section do not split it. Every entry's endpoint times match the service's
# stored departure/arrival, except 12139's Nagpur arrival, corrected in
# SERVICES below from the secondary listing's 05:45 to the schedule's 05:46.
# A multi-section service added without an entry here keeps the full-route
# interval on every section, labelled full_route, never a section time.
SERVICE_STOPS={
 ('17617','MMR','NED'):{'MMR':(None,'10:45'),'AWB':('13:05','13:10'),'PBN':('16:18','16:20'),'NED':('18:00',None)},
 ('12139','AK','NGP'):{'AK':(None,'01:05'),'BD':('02:25','02:30'),'WR':('03:47','03:50'),'NGP':('05:46',None)},
 ('17318','PUNE','MRJ'):{'PUNE':(None,'00:06'),'STR':('02:32','02:35'),'MRJ':('05:00',None)},
 ('17411','PUNE','MRJ'):{'PUNE':(None,'00:15'),'STR':('03:02','03:05'),'MRJ':('05:35',None)},
 ('11040','PUNE','MRJ'):{'PUNE':(None,'03:40'),'STR':('06:32','06:35'),'MRJ':('09:45',None)},
 ('11029','PUNE','MRJ'):{'PUNE':(None,'12:40'),'STR':('15:57','16:00'),'MRJ':('18:52',None)},
 ('12780','PUNE','MRJ'):{'PUNE':(None,'17:10'),'STR':('19:47','19:50'),'MRJ':('22:40',None)},
 ('11030','MRJ','PUNE'):{'MRJ':(None,'09:25'),'STR':('12:07','12:10'),'PUNE':('15:45',None)},
 ('11039','MRJ','PUNE'):{'MRJ':(None,'15:50'),'STR':('18:37','18:40'),'PUNE':('22:10',None)},
 ('17317','MRJ','PUNE'):{'MRJ':(None,'21:20'),'STR':('23:42','23:45'),'PUNE':('03:00',None)},
 ('17412','MRJ','PUNE'):{'MRJ':(None,'21:55'),'STR':('00:12','00:15'),'PUNE':('03:25',None)},
 ('12779','MRJ','PUNE'):{'MRJ':(None,'22:45'),'STR':('00:55','01:00'),'PUNE':('04:15',None)},
}

def minutes(s):
    h,m=map(int,s.split(':'));return h*60+m

def catalogue():
    """Include existing 42 reference services without silently promoting them to verified current traffic."""
    import re
    from pathlib import Path
    text=(Path(__file__).resolve().parents[2]/'data/route-timetables.ts').read_text()
    records=[dict(s,source=CHECKED_SERVICES[(s['train_no'],s['origin'],s['destination'])],version='public listing checked 2026-11-17; actual running unconfirmed',service_days=DAILY) if (s['train_no'],s['origin'],s['destination']) in CHECKED_SERVICES else dict(s) for s in SERVICES]
    for corridor,origin,destination,path,rows in re.findall(r"\['([^']+)','([^']+)','([^']+)','([^']+)',\[(.*?)\]\],",text,re.S):
        for num,name,dep,arr in re.findall(r"\['(\d+)','([^']+)','(\d\d:\d\d)','(\d\d:\d\d)'\]",rows):
            checked=PUNE_LONAVALA_CHECKED.get((num,origin,destination)) if corridor=='Pune-Lonavala' else None
            route_checked=CHECKED_DAILY.get((corridor,origin,destination),{}).get(num)
            if checked is None and route_checked:
                checked=(*route_checked,f'https://www.confirmtkt.com/trains/{path}-train-tickets')
            if checked:
                dep,arr,source=checked
                records.append(dict(train_no=num,name=name,corridor=corridor,origin=origin,destination=destination,departure=dep,arrival=arr,source=source,version='selected public listing checked 2026-11-17; current operations unconfirmed',service_days=DAILY))
            else:
                records.append(dict(train_no=num,name=name,corridor=corridor,origin=origin,destination=destination,departure=dep,arrival=arr,source=f'https://www.confirmtkt.com/trains/{path}-train-tickets',version='existing reference retrieved 2026-09-07; not reverified',service_days=None))
    for record in records:
        stops=SERVICE_STOPS.get((record['train_no'],record['origin'],record['destination']))
        if stops:record['stops']=stops
    graph=network()
    for record in records:
        path=next(p for _,name,p in ROUTES if name==record['corridor'])
        corridor=next(c for c in graph['corridors'] if c['id']==record['corridor'])
        a,b=path.index(record['origin']),path.index(record['destination'])
        record['sections']=corridor['sections'][min(a,b):max(a,b)]
    return records

def movements(start_date,horizon,mode='reference'):
    graph=network(); result=[]
    if mode not in ('reference','synthetic'):raise ValueError('Unknown traffic mode.')
    if mode=='synthetic':
        # Separate deterministic operating scenario. These are never real train numbers.
        for d in range(-1,horizon):
            for sec in graph['sections']:
                for i in range(12):
                    start=d*1440+60+i*117+(int(sec['id'][-2:])*7)%45
                    result.append(dict(service_id=f'SYN-{d}-{sec["id"]}-{i}',train_no=f'SYN-{i}',section=sec['id'],start=start,end=start+15,kind='Passenger' if i%3 else 'Freight',line='Both',provenance='SYNTHETIC operating scenario'))
        return result
    for s in catalogue():
        # A timetable row without verified service days is a source reference only.
        # Do not silently turn it into a daily operating movement or use it to
        # certify that any period is free of trains.
        if s['service_days'] is None:continue
        corridor=next(c for c in graph['corridors'] if c['id']==s['corridor'])
        # Protect only the part between the published endpoints. No passing times are invented.
        path=next(p for z,n,p in ROUTES if n==s['corridor'])
        a,b=path.index(s['origin']),path.index(s['destination']);lo,hi=sorted((a,b));section_ids=corridor['sections'][lo:hi]
        # Resolve each section's interval once, in travel order, on a rolling
        # clock: a stop time earlier than the previous section's end belongs
        # to the next day (e.g. 17412 reaches Satara at 00:12 after a 21:55
        # Miraj departure). Sections with no checked stop times keep the
        # protective full-route interval, labelled full_route.
        rel_start=minutes(s['departure']);rel_end=minutes(s['arrival'])
        if rel_end<=rel_start:rel_end+=1440
        stops=s.get('stops') or {};forward=a<=b;edges={x['id']:x for x in graph['sections']}
        resolved={};cursor=rel_start
        for sec in (section_ids if forward else section_ids[::-1]):
            edge=edges[sec];trav_from=edge['from_station'] if forward else edge['to_station'];trav_to=edge['to_station'] if forward else edge['from_station']
            if {trav_from,trav_to}=={s['origin'],s['destination']}:
                resolved[sec]=(rel_start,rel_end,'section',trav_from,trav_to)
            elif trav_from in stops and trav_to in stops and stops[trav_from][1] and stops[trav_to][0]:
                seg_start=minutes(stops[trav_from][1])
                while seg_start<cursor:seg_start+=1440
                seg_end=minutes(stops[trav_to][0])
                while seg_end<=seg_start:seg_end+=1440
                resolved[sec]=(seg_start,seg_end,'section',trav_from,trav_to);cursor=seg_end
            else:
                resolved[sec]=(rel_start,rel_end,'full_route',trav_from,trav_to)
        for d in range(-1,horizon):
            day=date.fromisoformat(start_date)+timedelta(days=d)
            if day.weekday() not in s['service_days']:continue
            if d*1440+rel_end<=0:continue
            for sec in section_ids:
                seg_start,seg_end,scope,trav_from,trav_to=resolved[sec]
                seg_start+=d*1440;seg_end+=d*1440
                if seg_end<=0:continue
                result.append(dict(service_id=f'REF-{s["train_no"]}-{day}-{sec}',train_no=s['train_no'],section=sec,start=seg_start,end=seg_end,kind='Passenger',line='Both',source=s['source'],version=s['version'],provenance='Public reference with listed service days; current operating date and dispatch restrictions unconfirmed',origin=s['origin'],destination=s['destination'],from_station=trav_from,to_station=trav_to,time_scope=scope,route_start=d*1440+rel_start,route_end=d*1440+rel_end))
    return result

def coverage():
    records=catalogue()
    return [dict(corridor=c['id'],zone=c['zone'],reference_services=sum(s['corridor']==c['id'] for s in records),date_applicable_references=sum(s['corridor']==c['id'] and s['service_days'] is not None for s in records),unverified_service_days=sum(s['corridor']==c['id'] and s['service_days'] is None for s in records),status='INCOMPLETE public reference' if any(s['corridor']==c['id'] for s in records) else 'NO retrieved timetable records',freight_complete=False,valid_for_dispatch=False) for c in network()['corridors']]
