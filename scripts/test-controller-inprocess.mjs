// Exercises the actual web route, authentication and SQLite persistence in memory.
// Only the transport to the Python service is replaced with an in-process handler bridge.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { DatabaseSync } from 'node:sqlite';
import { build } from 'esbuild';
import { resolve } from 'node:path';

const testDir=mkdtempSync(resolve(tmpdir(),'railblock-api-'));
const testDb=resolve(testDir,'integration.sqlite');
const sql=new DatabaseSync(testDb);
process.on('exit',()=>{sql.close();rmSync(testDir,{recursive:true,force:true})});
for(const name of readdirSync('drizzle').filter(n=>n.endsWith('.sql')).sort())sql.exec(readFileSync('drizzle/'+name,'utf8'));
class Statement {
 constructor(query,args=[]){this.query=query;this.args=args}
 bind(...args){return new Statement(this.query,args)}
 async first(){return sql.prepare(this.query).get(...this.args)??null}
 async all(){return {results:sql.prepare(this.query).all(...this.args)}}
 async run(){const stmt=sql.prepare(this.query);if(stmt.columns().length)return {results:stmt.all(...this.args),meta:{changes:0}};const r=stmt.run(...this.args);return {meta:{changes:Number(r.changes)}}}
}
globalThis.testEnv={DB:{prepare:q=>new Statement(q),batch:async items=>{sql.exec('BEGIN');try{const out=[];for(const item of items)out.push(await item.run());sql.exec('COMMIT');return out}catch(e){sql.exec('ROLLBACK');throw e}}},RAILBLOCK_ENGINE_TOKEN:'in-process-test-token'};
async function bundleRoute(path){const result=await build({entryPoints:[path],bundle:true,write:false,platform:'node',format:'esm',target:'node22',plugins:[{name:'bindings',setup(b){b.onResolve({filter:/^cloudflare:workers$/},()=>({path:'cloudflare',namespace:'test'}));b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:'export const env=globalThis.testEnv',loader:'js'}));b.onResolve({filter:/^@\//},args=>{const p=resolve(args.path.slice(2));return {path:existsSync(p)?p:p+'.ts'}});}}]});
return import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));}
const route=await bundleRoute('app/api/planner/route.ts');
let forceMonthlyFallback=false;
const nativeFetch=globalThis.fetch;
globalThis.fetch=async (input,opts={})=>{
 const url=new URL(input instanceof Request?input.url:input);if(url.hostname!=='127.0.0.1'||url.port!=='8008')return nativeFetch(input,opts);
 const bridge=`import io,json,os,sys\nfrom types import SimpleNamespace\nsys.path.insert(0,'planner')\nos.environ['RAILBLOCK_ENGINE_TOKEN']='in-process-test-token'\nimport server\nx=json.load(sys.stdin); raw=(x.get('body') or '').encode(); result=[]\nf=SimpleNamespace(path=x['path'],headers={'X-Engine-Token':'in-process-test-token','Content-Length':str(len(raw))},rfile=io.BytesIO(raw),respond=lambda v,status=200:result.append((status,v)))\nimport engine\noriginal=engine.solve\ndef controlled_solve(data,config,*args,**kwargs):\n if x.get('force_fallback') and config['horizon']==30 and kwargs.get('period') is None: return dict(solver_status='UNKNOWN',blocks=[],unscheduled=[],seconds=0)\n return original(data,config,*args,**kwargs)\nengine.solve=controlled_solve\n(server.Handler.do_POST if raw else server.Handler.do_GET)(f)\nprint(json.dumps(result[0],allow_nan=False))`;
 const child=spawnSync('.venv/bin/python',['-c',bridge],{input:JSON.stringify({path:url.pathname+url.search,body:opts.body,force_fallback:forceMonthlyFallback}),encoding:'utf8',maxBuffer:20_000_000,timeout:240_000});
 if(child.status!==0)throw Error('Python engine bridge failed: '+child.stderr);
 const [status,data]=JSON.parse(child.stdout.trim().split('\n').at(-1));return Response.json(data,{status});
};
const root='http://localhost:3000';
const call=(method,path,body,token)=>new Request(root+path,{method,headers:{...(body?{'Content-Type':'application/json','Origin':root}:{}),...(token?{Cookie:'railops_session='+token}:{})},...(body?{body:JSON.stringify(body)}:{})});
const anon=await route.GET(call('GET','/api/planner'));assert.equal(anon.status,403);
// Seed and use the actual session lookup and hashing logic, backed by a private SQLite DB.
await route.GET(call('GET','/api/planner'));
const hash=async x=>Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(x))).toString('hex');
for(const [token,id] of [['controller-test','CONTROL-01'],['head-test','HEAD-TRK']])sql.prepare('INSERT INTO sessions(token_hash,account_id,expires_at) VALUES(?,?,?)').run(await hash(token),id,Date.now()+3600000);
assert.equal((await route.GET(call('GET','/api/planner',null,'head-test'))).status,403);
let response=await route.POST(call('POST','/api/planner',{action:'unexpected'},'controller-test'));assert.equal(response.status,400);
response=await route.POST(call('POST','/api/planner',{action:'generate',config:{horizon:7,start_date:'2026-11-18',incidents:[]}},'controller-test'));assert.equal(response.status,400);assert.match((await response.json()).error,/zone/i);
const config={zone:'Z1',corridor:'Churchgate-Virar',section:'MH-SEC-02',start_date:'2026-11-18',horizon:7,traffic_mode:'reference',incidents:[]};
response=await route.POST(call('POST','/api/planner',{action:'generate',config},'controller-test'));let body=await response.json();assert.equal(response.status,200,JSON.stringify(body));assert.ok(body.id);assert.ok(body.blocks.length);
const id=body.id;const originalBlocks=body.blocks;response=await route.GET(call('GET','/api/planner?id='+id,null,'controller-test'));assert.equal(response.status,200,await response.clone().text());const reloaded=await response.json();assert.equal(reloaded.id,id);assert.deepEqual(reloaded.blocks,originalBlocks);
const saved=sql.prepare('SELECT payload FROM planner_plans WHERE id=?').get(id).payload;const stale=JSON.parse(saved);stale.input_fingerprint='outdated';sql.prepare('UPDATE planner_plans SET payload=? WHERE id=?').run(JSON.stringify(stale),id);response=await route.GET(call('GET','/api/planner?id='+id,null,'controller-test'));assert.equal(response.status,409);sql.prepare('UPDATE planner_plans SET payload=? WHERE id=?').run(saved,id);
response=await route.POST(call('POST','/api/planner',{action:'accept',plan_id:id,revision:1,reason:'In-process review'},'controller-test'));body=await response.json();assert.equal(response.status,200,JSON.stringify(body));assert.equal(body.status,'Accepted');
assert.equal((await route.POST(call('POST','/api/planner',{action:'reject',plan_id:id,revision:1,reason:'Stale'},'controller-test'))).status,409);
response=await route.POST(call('POST','/api/planner',{action:'reject',plan_id:id,revision:2,reason:'In-process archive'},'controller-test'));assert.equal(response.status,200);assert.equal((await response.json()).status,'Rejected');
assert.equal(sql.prepare('SELECT count(*) n FROM planner_plans WHERE id=?').get(id).n,1);assert.equal(sql.prepare('SELECT count(*) n FROM planner_feedback WHERE plan_id=?').get(id).n,2);
console.log('PASS in-process web authorization, real SQLite persistence, engine generation, reload, approval, rejection and invalid input');

const appRoute=await bundleRoute('app/api/app/route.ts');
let login=await appRoute.POST(call('POST','/api/app',{action:'login',id:'CONTROL-01',password:'incorrect'}));assert.equal(login.status,401);
login=await appRoute.POST(call('POST','/api/app',{action:'login',id:'CONTROL-01',password:'RailOps@123'}));assert.equal(login.status,200);const liveToken=/railops_session=([^;]+)/.exec(login.headers.get('set-cookie'))[1];
assert.ok(sql.prepare('SELECT 1 FROM sessions WHERE token_hash=?').get(await hash(liveToken)));
assert.equal((await appRoute.GET(call('GET','/api/app',null,liveToken))).status,200);
console.log('PASS actual login/password verification, session persistence and Controller overview');
for(const account of ['HEAD-TRK','W0001']){
 const r=await appRoute.POST(call('POST','/api/app',{action:'login',id:account,password:'RailOps@123'}));assert.equal(r.status,200);const token=/railops_session=([^;]+)/.exec(r.headers.get('set-cookie'))[1];
 for(const method of ['GET','POST']) assert.equal((await route[method](call(method,'/api/planner',method==='POST'?{action:'generate',config}:null,token))).status,403);
}
assert.equal((await route.POST(call('POST','/api/planner',{action:'generate',config}))).status,403);
console.log('PASS anonymous, department-head and worker planning isolation');
for(const value of [null,[],{}, {action:'generate'}, {action:'generate',config:{...config,start_date:'2026-02-30'}}, {action:'generate',config:{...config,priority_weights:{failure:100}}}, {action:'generate',config:{...config,priority_weights:{asset:15,safety:25,urgency:15,failure:'25',overdue:10,operational:10}}}]){
 const request=new Request(root+'/api/planner',{method:'POST',headers:{'Content-Type':'application/json',Cookie:'railops_session='+liveToken},body:JSON.stringify(value)});assert.equal((await route.POST(request)).status,400);
}
assert.equal((await route.POST(new Request(root+'/api/planner',{method:'POST',headers:{'Content-Type':'application/json',Cookie:'railops_session='+liveToken},body:'{'}))).status,400);
assert.equal((await route.POST(new Request(root+'/api/planner',{method:'POST',headers:{'Content-Type':'text/plain'},body:'{}'}))).status,415);
assert.equal((await route.POST(new Request(root+'/api/planner',{method:'POST',headers:{'Content-Type':'application/json',Origin:'https://other.invalid'},body:'{}'}))).status,403);
for(const query of ['start_date=invalid','resource=requests&department=WRONG','priority_weights=not-json','resource=requests&priority_weights=[]']) assert.equal((await route.GET(call('GET','/api/planner?zone=Z1&'+query,null,liveToken))).status,400);
console.log('PASS malformed JSON, missing fields, dates, departments, weights, content type and origin validation');
const readDataset=async cfg=>{
 const query=new URLSearchParams(Object.entries(cfg).map(([k,v])=>[k,typeof v==='string'?v:JSON.stringify(v)]));
 const r=await route.GET(call('GET','/api/planner?'+query,null,liveToken));assert.equal(r.status,200,await r.clone().text());return (await r.json()).dataset;
};
const dayData=await readDataset({...config,planning_start:360,planning_end:1320});
assert.equal(dayData.planning_period.start_minutes,360);assert.equal(dayData.planning_period.end_minutes,1320);
assert.ok(dayData.candidate_windows.every(w=>w.start%1440>=360&&w.end%1440<=1320));
const nextDate=await readDataset({...config,start_date:'2026-11-19'});assert.notEqual(dayData.fingerprint,nextDate.fingerprint);assert.equal(nextDate.candidate_windows[0].date,'2026-11-19');
for(const zone of ['Z1','Z2','Z3','Z4']){const d=await readDataset({...config,zone,corridor:'all',section:'all'});assert.equal(d.tasks.length,300);assert.ok(d.tasks.every(t=>t.zone===zone));assert.ok(d.tasks.every(t=>Math.abs(Object.values(t.components).reduce((a,b)=>a+b,0)-t.priority)<1e-8));}
console.log('PASS all four zone scopes, six-factor totals, date and planning-period forwarding');
const task=dayData.tasks.find(t=>t.safety_action==='NORMAL');
const overrides={[task.id]:{crew:4,required_duration:600}};
const changed=await readDataset({...config,task_overrides:overrides});assert.equal(changed.tasks.find(t=>t.id===task.id).crew,4);assert.ok(changed.tasks.find(t=>t.id===task.id).duration>=600);
const infer=await route.POST(call('POST','/api/planner',{action:'infer',features:task.observation_features,asset_id:task.asset_id,observation_id:task.inspection_history.observation_id},liveToken));assert.equal(infer.status,200);const prediction=await infer.json();for(const h of ['7','30','90'])assert.ok(Math.abs(prediction.risk[h]-task.risk[h])<1e-7,'Saved float32 vs inferred probability drift: '+h);assert.equal(Math.ceil(prediction.predicted_duration),task.predicted_duration);
console.log('PASS saved-model 7/30/90-day inference, engineered duration, crew and work overrides');
const post=async command=>{const r=await route.POST(call('POST','/api/planner',command,liveToken));const d=await r.json();assert.equal(r.status,200,JSON.stringify(d));return d;};
const twinPlan=await post({action:'twin_replan',config:{...config,planning_start:0,planning_end:1440,task_overrides:overrides},changes:[{task_id:task.id,overrides:{elapsed_days:30}}]});
assert.equal(twinPlan.config.planning_end,1440);assert.equal(twinPlan.scenario_source,'DIGITAL_TWIN');assert.equal(twinPlan.validation.violations,0);
const twinReload=await route.GET(call('GET','/api/planner?id='+twinPlan.id,null,liveToken));assert.equal(twinReload.status,200,await twinReload.clone().text());assert.deepEqual((await twinReload.json()).blocks,twinPlan.blocks);
const twinAccepted=await post({action:'accept',plan_id:twinPlan.id,revision:1,reason:'Isolated integration audit'});assert.equal(twinAccepted.status,'Accepted');
assert.equal((await route.POST(call('POST','/api/planner',{action:'twin_replan',config,changes:[{task_id:task.id,risk:prediction.risk,predicted_duration:15,duration_buffer:0}]},liveToken))).status,400);
console.log('PASS server-reinferred twin plan, full-day persistence/reload/acceptance, forged prediction rejection');
forceMonthlyFallback=true;
const began=performance.now();const monthly=await post({action:'generate',config:{...config,horizon:30}});forceMonthlyFallback=false;
assert.equal(monthly.solver_status,'ROLLING_FEASIBLE');assert.equal(monthly.dataset_snapshot.tasks.length,300);assert.ok(monthly.blocks.length);assert.equal(monthly.validation.violations,0);
const assigned=monthly.blocks.flatMap(b=>b.tasks);assert.equal(new Set(assigned).size,assigned.length);assert.equal(assigned.length,monthly.metrics.tasks);assert.equal(monthly.blocks.length,monthly.metrics.blocks);
for(const b of monthly.blocks){assert.ok(b.start>=b.candidate_slot.start&&b.end<=b.candidate_slot.end);assert.equal(b.duration,b.end-b.start);}
const disk=new DatabaseSync(testDb,{readOnly:true});const persisted=JSON.parse(disk.prepare('SELECT payload FROM planner_plans WHERE id=?').get(monthly.id).payload);disk.close();assert.deepEqual(persisted.blocks,monthly.blocks);assert.equal(persisted.solver_status,monthly.solver_status);
console.log(`PASS controlled global UNKNOWN -> ${monthly.solver_status}, 300 requests, ${assigned.length} tasks / ${monthly.blocks.length} blocks, ${(performance.now()-began).toFixed(0)} ms, persisted to disk`);
const [firstReview,staleReview]=await Promise.all([route.POST(call('POST','/api/planner',{action:'accept',plan_id:monthly.id,revision:1,reason:'First concurrent review'},liveToken)),route.POST(call('POST','/api/planner',{action:'reject',plan_id:monthly.id,revision:1,reason:'Competing review'},liveToken))]);assert.deepEqual([firstReview.status,staleReview.status].sort((a,b)=>a-b),[200,409]);
assert.equal(sql.prepare('SELECT count(*) n FROM planner_feedback WHERE plan_id=?').get(monthly.id).n,1);
console.log('PASS concurrent revision conflict; exactly one decision and feedback persisted');
for(const path of ['app/api/model-comparison/route.ts','app/api/models/route.ts','app/api/operations/route.ts']){
 const endpoint=await bundleRoute(path);assert.ok([401,403].includes((await endpoint.GET(call('GET','/api/test'))).status));const r=await endpoint.GET(call('GET','/api/test',null,liveToken));assert.equal(r.status,200,await r.clone().text());console.log('PASS authenticated read '+path);
}
console.log('PASS integration complete; real web route functions + SQLite + Python handlers; socket/HTTP transport is not simulated as verified');
