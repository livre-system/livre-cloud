import asyncio, json, os, sys, pg8000.dbapi, httpx
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

V = json.load(open('/tmp/railway-vars.json'))
os.environ.setdefault('DATABASE_URL', V['DATABASE_URL'])
DB = urlsplit(V['DATABASE_URL'])

async def fetch_all(c, h, name, path, params, key):
    x = await c.get(path, params={**params, 'page': 1}, headers=h)
    x.raise_for_status(); b=x.json(); p=b.get('page', b)
    total=int(p.get('totalPages', 1)); rows=list(p.get('content', []))
    sem=asyncio.Semaphore(12)
    async def one(n):
        async with sem:
            z=await c.get(path, params={**params, 'page': n}, headers=h)
            z.raise_for_status(); q=z.json(); q=q.get('page', q)
            return q.get('content', [])
    for batch in range(2, total+1, 12):
        result=await asyncio.gather(*(one(n) for n in range(batch, min(batch+12,total+1))))
        for part in result: rows.extend(part)
        print(name, len(rows), '/', total, flush=True)
    return rows, key

async def fetch():
    async with httpx.AsyncClient(base_url='https://api.apps.magiis.com', timeout=45, limits=httpx.Limits(max_connections=12)) as c:
        r=await c.post('/auth/login', headers={'RoleToAttempt':'ROLE_CARRIER'}, json={'username':V['MAGIIS_WEB_USERNAME'],'password':V['MAGIIS_WEB_PASSWORD']}); r.raise_for_status(); h={'Authorization':'Bearer '+r.json()['token']}
        out={}
        out['passengers']=await fetch_all(c,h,'passengers','/carriers/2421/clients/paginated',{'size':100,'column':'code','sort':'ASC','find':'','isPaxVip':'false'},'passengerUserId')
        out['vehicles']=await fetch_all(c,h,'vehicles','/carriers/2421/vehicles/paginated',{'size':100,'column':'id','sort':'ASC','find':''},'vehicleId')
        out['travels']=await fetch_all(c,h,'travels','/carriers/2421/travels/paginated',{'size':100,'column':'travelDate','sort':'ASC','state':'','platform':'false','find':'','programmed':'','travelDateFrom':'2020-01-01','travelDateTo':'2026-09-12','isPaxVip':'false'},'travelId')
        x=await c.get('/carriers/2421/drivers/driversForCombo',headers=h); x.raise_for_status(); out['drivers']=(x.json(),'driverUserId')
        return out

data=asyncio.run(fetch())
import psycopg
conn=psycopg.connect(host='maglev.proxy.rlwy.net', port=29591, dbname=DB.path.lstrip('/'), user=DB.username, password=DB.password, sslmode='disable', connect_timeout=20)
with conn.cursor() as q:
    q.execute("update magiis_crm_sync_runs set status='failed',finished_at=now(),error='superseded' where status='running'")
    q.execute("insert into magiis_crm_sync_runs(status) values ('running') returning id")
    rid=q.fetchone()[0]
conn.commit(); counts={}
for name,(items,key) in data.items():
    valid=[row for row in items if row.get(key) is not None]
    with conn.cursor() as q:
        if name=='travels':
            values=[(int(row[key]),json.dumps(row,separators=(',',':')),row.get('travelDate') or row.get('creationDate'),row.get('state'),row.get('driverId')) for row in valid]
            q.executemany("insert into magiis_crm_travels(magiis_id,raw_payload,travel_date,state,driver_magiis_id) values (%s,%s::jsonb,%s,%s,%s) on conflict(magiis_id) do update set raw_payload=excluded.raw_payload,travel_date=excluded.travel_date,state=excluded.state,driver_magiis_id=excluded.driver_magiis_id,last_seen_at=now()", values)
        else:
            values=[(int(row[key]),json.dumps(row,separators=(',',':'))) for row in valid]
            q.executemany(f"insert into magiis_crm_{name}(magiis_id,raw_payload) values (%s,%s::jsonb) on conflict(magiis_id) do update set raw_payload=excluded.raw_payload,last_seen_at=now()", values)
    conn.commit(); counts[name]=len(valid); print('persisted',name,len(valid),flush=True)
with conn.cursor() as q:
    q.execute("update magiis_crm_sync_runs set status='completed',finished_at=now(),resources=%s::jsonb where id=%s",(json.dumps(counts),rid))
conn.commit(); print('completed',rid,counts)

# La proyección semántica es best-effort: el CRM ya quedó persistido aunque
# OpenAI, pgvector o la memoria no estén disponibles en esta ejecución.
try:
    from app.memory.magiis import sync_magiis_memory
    memory_result = sync_magiis_memory()
    print('memory_projection', json.dumps(memory_result, ensure_ascii=False), flush=True)
except Exception as exc:
    print('memory_projection_failed', type(exc).__name__, flush=True)

conn.close()
