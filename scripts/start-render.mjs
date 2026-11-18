// Single-instance Render demo: keep the Python engine private to this container.
import { spawn, spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';

const token = randomBytes(32).toString('hex');
const persist = process.env.RAILBLOCK_STATE_DIR || '/app/.wrangler/state';
const config = 'dist/server/wrangler.json';
const env = { ...process.env, PYTHONPATH: '/app', CI: '1', WRANGLER_SEND_METRICS: 'false', WRANGLER_WRITE_LOGS: 'false', RAILBLOCK_ENGINE_TOKEN: token, RAILBLOCK_ENGINE_URL: 'http://127.0.0.1:18808' };
const migration = spawnSync('node', ['node_modules/wrangler/bin/wrangler.js', 'd1', 'migrations', 'apply', 'DB', '--local', '--config', config, '--persist-to', persist], { env, stdio: 'inherit' });
if (migration.status !== 0) process.exit(migration.status || 1);
const engine = spawn('.venv/bin/python', ['-u', 'planner/server.py'], { env: { ...env, PORT: '18808', RAILBLOCK_BIND_HOST: '127.0.0.1' }, stdio: 'inherit' });
const worker = spawn('node', ['node_modules/wrangler/bin/wrangler.js', 'dev', '--local', '--config', config, '--persist-to', persist, '--ip', '0.0.0.0', '--port', process.env.PORT || '10000', '--var', `RAILBLOCK_ENGINE_TOKEN:${token}`, '--var', 'RAILBLOCK_ENGINE_URL:http://127.0.0.1:18808'], { env, stdio: 'inherit' });
let stopping = false;
function stop(code) { if (stopping) return; stopping = true; engine.kill('SIGTERM'); worker.kill('SIGTERM'); process.exitCode = code; }
for (const child of [engine, worker]) { child.on('error', error => { console.error(error.message); stop(1); }); child.on('exit', code => stop(code || 1)); }
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => stop(0));
