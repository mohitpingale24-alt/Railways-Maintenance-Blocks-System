"""Project planning geography. Shared physical edges have one owner, many corridor memberships."""
from math import radians, sin, cos, asin, sqrt
MAP_SOURCE = 'https://rb.indianrailways.gov.in/uploads/directorate/civil_engg/SystemMaps/2026/CR%20PUNE%20Divn%20Map%202026.pdf'
ZONES = [{'id':'Z1','name':'Mumbai and Konkan'}, {'id':'Z2','name':'Pune and Western Maharashtra'}, {'id':'Z3','name':'North Maharashtra and Marathwada'}, {'id':'Z4','name':'Vidarbha'}]
# Rounded map reference positions except the explicitly verified Nagpur station coordinate.
NODES = [('CSMT','Mumbai CSMT',18.9402,72.8356),('KYN','Kalyan',19.235,73.130),('CCG','Churchgate',18.935,72.827),('VR','Virar',19.455,72.811),('PNVL','Panvel',18.990,73.120),('RN','Ratnagiri',16.990,73.330),('PUNE','Pune',18.528,73.874),('LNL','Lonavala',18.750,73.408),('DD','Daund',18.465,74.583),('SUR','Solapur',17.665,75.893),('STR','Satara',17.688,74.063),('MRJ','Miraj',16.820,74.640),('NK','Nashik Road',19.948,73.842),('MMR','Manmad',20.249,74.438),('BSL','Bhusawal',21.045,75.788),('AWB','Chhatrapati Sambhajinagar',19.860,75.312),('PBN','Parbhani',19.254,76.773),('NED','Nanded',19.160,77.310),('AK','Akola',20.726,77.006),('BD','Badnera',20.856,77.732),('WR','Wardha',20.729,78.596),('NGP','Nagpur',21.1534,79.0889),('CD','Chandrapur',19.954,79.296),('G','Gondia',21.466,80.193)]
ROUTES = [('Z1','Mumbai-Kalyan',['CSMT','KYN']),('Z1','Churchgate-Virar',['CCG','VR']),('Z1','Panvel-Ratnagiri',['PNVL','RN']),('Z2','Pune-Lonavala',['PUNE','LNL']),('Z2','Pune-Daund',['PUNE','DD']),('Z2','Pune-Solapur',['PUNE','DD','SUR']),('Z2','Pune-Miraj',['PUNE','STR','MRJ']),('Z3','Nashik-Manmad-Bhusawal',['NK','MMR','BSL']),('Z3','Manmad-Sambhajinagar-Nanded',['MMR','AWB','PBN','NED']),('Z4','Bhusawal-Akola-Nagpur',['BSL','AK','BD','WR','NGP']),('Z4','Nagpur-Wardha-Chandrapur',['NGP','WR','CD']),('Z4','Nagpur-Gondia',['NGP','G'])]
VERIFIED = {'NGP':(21.1534,79.0889,'Nagpur_Junction_railway_station'),'PUNE':(18.5289,73.8743,'Pune_Junction_railway_station'),'MMR':(20.2498,74.4384,'Manmad_Junction_railway_station'),'BSL':(21.0469,75.788,'Bhusaval_Junction_railway_station'),'NK':(19.9472,73.8421,'Nasik_Road_railway_station'),'AK':(20.7233,77.0057,'Akola_Junction_railway_station')}
def network():
    stations=[dict(id=c,name=n,latitude=lat,longitude=lon,coordinate_status='verified secondary station reference' if c=='NGP' else 'rounded unverified map reference; not surveyed',coordinate_source='https://en.wikipedia.org/wiki/Nagpur_Junction_railway_station' if c=='NGP' else None) for c,n,lat,lon in NODES]
    for station in stations:
        if station['id'] in VERIFIED:
            lat,lon,page=VERIFIED[station['id']];station.update(latitude=lat,longitude=lon,coordinate_status='verified secondary station reference, retrieved 2026-11-17',coordinate_source='https://en.wikipedia.org/wiki/'+page)
    lookup={s['id']:s for s in stations}; edges={};corridors=[]
    for zone,name,path in ROUTES:
        ids=[]
        for a,b in zip(path,path[1:]):
            key=tuple(sorted((a,b)))
            if key not in edges:
                aa,bb=lookup[a],lookup[b]
                p,q=radians(aa['latitude']),radians(bb['latitude']);dp=q-p;dl=radians(bb['longitude']-aa['longitude'])
                length=6371*2*asin(sqrt(sin(dp/2)**2+cos(p)*cos(q)*sin(dl/2)**2))*1.15
                edges[key]=dict(id=f'MH-SEC-{len(edges)+1:02}',name=f"{aa['name']} – {bb['name']}",from_station=a,to_station=b,zone=zone,corridors=[],km_start=0,km_end=round(length,2),length_status='synthetic distance proxy; not railway chainage',power_zone=f'MH-PZ-{len(edges)+1:02}',lines=['Up','Down'],parallel_line_available=False,route_source=MAP_SOURCE if zone=='Z2' else 'Project corridor definition; detailed topology unverified')
            edge=edges[key];edge['corridors'].append(name);ids.append(edge['id'])
        corridors.append(dict(id=name,name=name,zone=zone,sections=ids))
    return dict(zones=ZONES,corridors=corridors,stations=stations,sections=list(edges.values()))
