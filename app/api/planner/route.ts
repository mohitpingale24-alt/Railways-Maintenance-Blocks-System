import { env } from 'cloudflare:workers';
import { db, seed, session } from '@/lib/server';
import type {Plan,Block,Metrics} from '@/lib/planner-types';
const isRecord=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);

export const dynamic='force-dynamic';
const json=(data:unknown,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store'}});
type Row={id:string;status:string;revision:number;payload:string;created_at:string;created_by:string;name?:string};
class EngineRequestError extends Error { constructor(message:string,readonly status:number){super(message)} }
async function engine<T>(path:string,body?:unknown):Promise<T>{
 let res:Response;
 try{res=await fetch('http://127.0.0.1:8008'+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-Engine-Token':(env as unknown as {RAILBLOCK_ENGINE_TOKEN:string}).RAILBLOCK_ENGINE_TOKEN||''},...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(240000)})}
 catch(e){console.error('Local planning connection:',(e as Error).message);throw new Error('The local planning engine is unavailable. Restart with npm run dev.')}
 const data:unknown=await res.json();if(!res.ok)throw new EngineRequestError(isRecord(data)&&typeof data.error==='string'?data.error:'Planning failed.',res.status);return data as T;
}
async function authorize(req:Request){await seed();const user=await session(req);return user?.role==='controller'?user:null}
const unpack=(r:Row)=>({...(JSON.parse(r.payload) as Plan),id:r.id,status:r.status,revision:r.revision,created_at:r.created_at});
const withoutFingerprint=(p:Plan):Plan=>{const copy={...p};delete (copy as Partial<Plan>).input_fingerprint;return copy};
export async function GET(req:Request){
 try{
 if(!await authorize(req))return json({error:'Control Centre access required.'},403);
 const id=new URL(req.url).searchParams.get('id');
 if(id){const row=await db().prepare('SELECT * FROM planner_plans WHERE id=?').bind(id).first<Row>();if(!row)return json({error:'Plan not found.'},404);const plan=unpack(row);const check=await engine<{fresh:boolean}>('/plan-freshness',{plan:{config:plan.config,input_fingerprint:plan.input_fingerprint}});if(!check.fresh)return json({error:'This saved plan uses older or unverifiable timetable or planning inputs. Generate a new plan.'},409);return json(plan)}
 const params=new URL(req.url).searchParams;
 const query=new URLSearchParams();for(const key of ['zone','corridor','section','start_date','horizon','traffic_mode','department','search','limit','offset','status','batch_offset','priority_weights','resource_overrides','task_overrides','planning_start','planning_end','full_scope']){const value=params.get(key);if(value!==null)query.set(key,value)}
 if(params.get('resource')==='feedback'){const {results:feedback}=await db().prepare('SELECT * FROM planner_feedback ORDER BY created_at DESC LIMIT 100').all();return json({feedback})}
 if(params.get('resource')==='zone-summary')return json(await engine('/zone-summary'));
 if(params.get('resource')==='assets')return json(await engine('/assets?'+query.toString()));
 if(params.get('resource')==='requests')return json(await engine('/requests?'+query.toString()));
 const dataset=await engine('/dataset?'+query.toString());
 const {results:plans}=await db().prepare('SELECT id,status,revision,created_at FROM planner_plans ORDER BY created_at DESC LIMIT 30').all();
 const {results:feedback}=await db().prepare('SELECT * FROM planner_feedback ORDER BY created_at DESC LIMIT 100').all();
 return json({dataset,plans,feedback});
 }catch(e){return json({error:(e as Error).message},e instanceof EngineRequestError&&e.status===400?400:503)}
}
export async function POST(req:Request){
 try{
 if(req.headers.get('origin')&&req.headers.get('origin')!==new URL(req.url).origin)return json({error:'Request origin not allowed.'},403);
 if(!req.headers.get('content-type')?.includes('application/json'))return json({error:'JSON required.'},415);
 const user=await authorize(req);if(!user)return json({error:'Control Centre access required.'},403);
 const body:unknown=await req.json();if(!isRecord(body))return json({error:'JSON body must be an object.'},400);
 const action=typeof body.action==='string'?body.action:'';
 if(action==='infer')return json(await engine('/maintenance/infer',{features:body.features,asset_id:body.asset_id,observation_id:body.observation_id}));
 if(action==='delay_analyze'){
 if(!isRecord(body.config)||typeof body.config.zone!=='string'||!body.config.zone.trim())return json({error:'Choose a Maharashtra planning zone before analyzing a delay.'},400);
 if(body.blocks!==undefined&&!Array.isArray(body.blocks))return json({error:'Plan blocks must be a list.'},400);
 return json(await engine('/delay/analyze',{config:body.config,blocks:body.blocks||[]}));
 }
 if(action==='twin_asset'){
 if(typeof body.asset_id!=='string'||!body.asset_id.trim())return json({error:'Choose an asset first.'},400);
 return json(await engine('/twin/asset',{asset_id:body.asset_id}));
 }
 if(action==='twin_infer'){
 if(typeof body.asset_id!=='string'||!body.asset_id.trim())return json({error:'Choose an asset first.'},400);
 if(body.overrides!==undefined&&(typeof body.overrides!=='object'||body.overrides===null||Array.isArray(body.overrides)))return json({error:'What-if inputs must be an object.'},400);
 return json(await engine('/twin/infer',{asset_id:body.asset_id,overrides:body.overrides||{}}));
 }
 if(action==='twin_replan'){
 if(!isRecord(body.config)||typeof body.config.zone!=='string'||!body.config.zone.trim())return json({error:'Choose a Maharashtra planning zone before a twin replan.'},400);
 if(!Array.isArray(body.changes)||!body.changes.length)return json({error:'Recompute at least one asset in the twin before replanning.'},400);
 let twinPrevious:Plan|null=null;
 if(typeof body.previous_plan_id==='string'&&body.previous_plan_id){const prow=await db().prepare('SELECT * FROM planner_plans WHERE id=?').bind(body.previous_plan_id).first<Row>();if(!prow)return json({error:'Previous plan not found.'},404);twinPrevious=withoutFingerprint(unpack(prow))}
 const plan=await engine<Plan>('/twin/replan',{config:body.config,changes:body.changes,previous:twinPrevious});
 if(twinPrevious)plan.parent_id=twinPrevious.id;
 const id='PLAN-'+crypto.randomUUID().slice(0,8).toUpperCase();const now=new Date().toISOString();
 await db().prepare("INSERT INTO planner_plans (id,created_at,created_by,status,revision,payload) VALUES (?,?,?,'Draft',1,?)").bind(id,now,user.id,JSON.stringify(plan)).run();
 return json({...plan,id,status:'Draft',revision:1,created_at:now});
 }
 if(action==='twin_save'||action==='twin_list'||action==='twin_open'){
 await db().prepare('CREATE TABLE IF NOT EXISTS twin_scenarios (id TEXT PRIMARY KEY,created_at TEXT,created_by TEXT,name TEXT,payload TEXT)').run();
 if(action==='twin_list'){const {results}=await db().prepare('SELECT id,created_at,created_by,name FROM twin_scenarios ORDER BY created_at DESC LIMIT 50').all();return json({scenarios:results})}
 if(action==='twin_open'){const row=await db().prepare('SELECT * FROM twin_scenarios WHERE id=?').bind(typeof body.scenario_id==='string'?body.scenario_id:'').first<Row>();if(!row)return json({error:'Twin scenario not found.'},404);return json({scenario:JSON.parse(row.payload),id:row.id,created_at:row.created_at,name:row.name})}
 if(!isRecord(body.scenario))return json({error:'Nothing to save.'},400);
 const id='TWIN-'+crypto.randomUUID().slice(0,8).toUpperCase();const now=new Date().toISOString();const name=(typeof body.scenario.name==='string'?body.scenario.name:'Untitled twin scenario').slice(0,120);
 await db().prepare('INSERT INTO twin_scenarios (id,created_at,created_by,name,payload) VALUES (?,?,?,?,?)').bind(id,now,user.id,name,JSON.stringify(body.scenario)).run();
 return json({id,created_at:now,name});
 }
 if(action==='generate'||action==='reoptimize'){
 if(action==='generate'&&(!isRecord(body.config)||typeof body.config.zone!=='string'||!body.config.zone.trim()))return json({error:'Choose a Maharashtra planning zone before generating a plan.'},400);
 let previous:Plan|null=null;
 if(action==='reoptimize'){
 const old=await db().prepare('SELECT * FROM planner_plans WHERE id=?').bind(typeof body.plan_id==='string'?body.plan_id:'').first<Row>();
 if(!old)return json({error:'Choose an existing plan.'},404);
 if(old.status==='Rejected')return json({error:'Rejected plans are archived. Generate a new plan.'},409);
 if(old.revision!==body.revision)return json({error:'This plan changed. Reload before re-optimizing.'},409);
 previous=unpack(old);
 }
 let retain:Plan|null=null;
 if(action==='generate'&&typeof body.previous_plan_id==='string'&&body.previous_plan_id){const prow=await db().prepare('SELECT * FROM planner_plans WHERE id=?').bind(body.previous_plan_id).first<Row>();if(!prow)return json({error:'Previous plan not found.'},404);if(prow.status==='Rejected')return json({error:'Rejected plans are archived. Generate a new plan.'},409);retain=withoutFingerprint(unpack(prow))}
 const {results:feedback}=await db().prepare("SELECT f.decision,f.block_id,p.payload FROM planner_feedback f JOIN planner_plans p ON p.id=f.plan_id WHERE f.decision IN ('Accepted','Rejected') ORDER BY f.created_at DESC LIMIT 100").all<{decision:string;block_id:string|null;payload:string}>();
 const votes:Record<string,number[]>={};
 for(const f of feedback){const payload=JSON.parse(f.payload) as Plan;const blocks=[...payload.blocks,...(payload.rejected_blocks||[])];for(const b of blocks.filter(b=>!f.block_id||b.id===f.block_id)){(votes[b.section]??=[]).push(f.decision==='Accepted'?1:-1)}}
 const preferences=Object.fromEntries(Object.entries(votes).map(([s,v])=>[s,v.reduce((a,b)=>a+b,0)/(v.length+5)]));
 const config=previous?{...previous.config,incidents:[...previous.config.incidents,body.incident]}:body.config;
 const plan=await engine<Plan>('/generate',{config,previous:previous??retain,preferences});
 if(previous)plan.parent_id=previous.id;
 if(retain)plan.parent_id=retain.id;
 const id='PLAN-'+crypto.randomUUID().slice(0,8).toUpperCase();const now=new Date().toISOString();
 if(previous){
 const saved=await db().prepare("INSERT INTO planner_plans (id,created_at,created_by,status,revision,payload) SELECT ?,?,?,'Draft',1,? WHERE EXISTS (SELECT 1 FROM planner_plans WHERE id=? AND revision=? AND status<> 'Rejected')").bind(id,now,user.id,JSON.stringify(plan),previous.id,previous.revision).run();
 if(!saved.meta.changes)return json({error:'The parent plan changed while solving. Reload it and retry.'},409);
 }else await db().prepare("INSERT INTO planner_plans (id,created_at,created_by,status,revision,payload) VALUES (?,?,?,'Draft',1,?)").bind(id,now,user.id,JSON.stringify(plan)).run();
 return json({...plan,id,status:'Draft',revision:1,created_at:now});
 }
 if(!['accept','reject','modify','reject_block'].includes(action))return json({error:'Unknown planner action.'},400);
 const row=await db().prepare('SELECT * FROM planner_plans WHERE id=?').bind(typeof body.plan_id==='string'?body.plan_id:'').first<Row>();
 if(!row)return json({error:'Plan not found.'},404);
 if(row.revision!==body.revision)return json({error:'This plan changed. Reload before saving your decision.'},409);
 if(row.status==='Rejected')return json({error:'Rejected plans are archived. Generate a new plan.'},409);
 const plan=JSON.parse(row.payload) as Plan;const reason=typeof body.reason==='string'?body.reason.trim():'';
 if(!reason||reason.length>1000)return json({error:'Add a review reason (up to 1,000 characters).'},400);
 let decision='Accepted',status='Accepted';
 if(action==='reject'){decision='Rejected';status='Rejected'}
 if(action==='modify'){
 const block=plan.blocks.find(b=>b.id===body.block_id);if(!block)return json({error:'Block not found.'},404);
 if(typeof body.start!=='number'||!Number.isInteger(body.start))return json({error:'Choose a valid whole-minute start.'},400);
 block.start=body.start;block.end=body.start+block.duration;block.decision='Modified';block.frozen=false;
 block.reasons=['Start time changed by the controller. Hard constraints were rechecked; opportunity and forecast scores require a new planning run.'];delete block.opportunity;delete block.expected_freight;delete block.candidate_slot;
 decision='Modified';status='Draft';plan.comparison=null;plan.changes=[];
 }
 if(action==='reject_block'){
 const block=plan.blocks.find(b=>b.id===body.block_id);if(!block)return json({error:'Block not found.'},404);
 (plan.rejected_blocks??=[]).push({...block,decision:'Rejected'});
 plan.blocks=plan.blocks.filter(b=>b.id!==block.id);
 plan.unscheduled.push(...block.tasks.map((id:string)=>({id,reason:'Controller rejected this block: '+reason})));
 decision='Rejected';status='Draft';plan.comparison=null;
 }
 if(body.action!=='reject'){
 const check=await engine<{errors:string[];metrics:Metrics;blocks:Block[]}>('/validate',{plan});if(check.errors.length)return json({error:'Cannot save: '+check.errors.slice(0,3).join(' ')},409);
 plan.metrics=check.metrics;if(check.blocks)plan.blocks=check.blocks;plan.validation={...plan.validation,checked_blocks:plan.blocks.length};
 }
 if(action==='accept')plan.blocks=plan.blocks.map(b=>({...b,decision:'Accepted'}));
 // The insert is conditional on this update's new revision and exact payload. A stale review cannot create feedback.
 const payload=JSON.stringify(plan),now=new Date().toISOString();
 const results=await db().batch([
 db().prepare('UPDATE planner_plans SET payload=?,status=?,revision=revision+1 WHERE id=? AND revision=?').bind(payload,status,row.id,row.revision),
 db().prepare('INSERT INTO planner_feedback (id,plan_id,block_id,decision,reason,created_by,created_at) SELECT ?,?,?,?,?,?,? WHERE changes()=1').bind(crypto.randomUUID(),row.id,body.block_id||null,decision,reason,user.id,now)
 ]);
 if(!results[0].meta.changes)return json({error:'Another controller review was saved first. Reload this plan.'},409);
 return json({...plan,id:row.id,status,revision:row.revision+1,created_at:row.created_at});
 }catch(e){return json({error:(e as Error).message},e instanceof EngineRequestError?e.status:400)}
}
