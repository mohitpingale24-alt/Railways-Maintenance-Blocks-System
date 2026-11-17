import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, openSync, closeSync, watch } from 'node:fs';

const plannerPython = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
if (!existsSync(plannerPython)) {
  console.error('Install the local planner first: python3 -m venv .venv && .venv/bin/pip install -r planner/requirements.txt');
  process.exit(1);
}
const env = { ...process.env, RAILBLOCK_ENGINE_TOKEN: randomBytes(32).toString('hex') };
const children = [];
let closing = false;
function stop(code=0) {
  if (closing) return;
  closing=true;
  for (const child of children) child.kill('SIGTERM');
  setTimeout(()=>process.exit(code), 800).unref();
}
for (const signal of ['SIGINT','SIGTERM']) process.on(signal, ()=>stop());
mkdirSync('.wrangler/logs',{recursive:true});
function launch(command, args, logName) {
  // Keep service output independent of a chat turn's terminal pipe lifetime.
  const log=openSync(`.wrangler/logs/${logName}.log`,'a');
  const child=spawn(command,args,{env,stdio:['ignore',log,log]});
  closeSync(log);
  children.push(child);
  child.on('error',e=>{console.error(e.message);stop(1)});
  child.on('exit',code=>{if(!closing&&!(child===plannerChild&&plannerRestarting))stop(code||1)});
  return child;
}
let plannerChild=null, plannerRestarting=false, plannerTimer=null;
function launchPlanner() {
  plannerChild=launch(plannerPython,['-u','planner/server.py'],'planner');
  plannerChild.on('exit',()=>{if(plannerRestarting){plannerRestarting=false;launchPlanner()}});
}
launchPlanner();
launch('node',['node_modules/vinext/dist/cli.js','dev','--host','127.0.0.1','--port','3000'],'app');
// The planner is a plain Python process with no hot reload, so editing any
// planner/*.py file leaves the running server answering 404 'Not found'
// for new routes until a manual restart (this bit three times on
// 2026-11-17: /delay/analyze, /model-comparison, then /twin/asset).
// Watch the sources and restart ONLY the planner child on a .py change;
// the app child and the shared engine token stay untouched.
try {
  watch('planner',{recursive:true},(_event,filename)=>{
    if(!filename||filename.includes('__pycache__')||!filename.endsWith('.py'))return;
    clearTimeout(plannerTimer);
    plannerTimer=setTimeout(()=>{if(closing||!plannerChild)return;plannerRestarting=true;console.log(`Planner source changed (${filename}); restarting the planner only.`);plannerChild.kill('SIGTERM')},400);
  });
} catch { /* watcher unavailable on this platform: manual restart still works */ }
console.log('Starting RailBlock AI at http://localhost:3000. Local service logs are in .wrangler/logs/.');
