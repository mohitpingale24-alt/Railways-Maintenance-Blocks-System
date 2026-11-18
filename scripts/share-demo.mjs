// Temporary, password-protected tunnel to the running local Controller.
import http from 'node:http';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { spawn } from 'node:child_process';

const password = randomBytes(24).toString('base64url');
const expected = Buffer.from('Basic ' + Buffer.from('demo:' + password).toString('base64'));
const proxy = http.createServer((req, res) => {
  const actual = Buffer.from(req.headers.authorization || '');
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
    res.writeHead(401, { 'WWW-Authenticate': 'Basic realm="RailBlock demo"', 'Cache-Control': 'no-store' });
    return res.end();
  }
  const headers = { ...req.headers, host: 'localhost:3000' };
  delete headers.authorization;
  const upstream = http.request({ hostname: '127.0.0.1', port: 3000, method: req.method, path: req.url, headers }, response => {
    res.writeHead(response.statusCode, { ...response.headers, 'cache-control': 'private, no-store' });
    response.pipe(res);
  });
  upstream.on('error', () => {
    if (!res.headersSent) res.writeHead(502);
    res.end('Local Controller unavailable');
  });
  req.pipe(upstream);
});
proxy.on('error', err => { console.error(err.message); process.exitCode = 1; });
proxy.listen(0, '127.0.0.1', () => {
  const tunnel = spawn('cloudflared', ['tunnel', '--url', 'http://127.0.0.1:' + proxy.address().port], { stdio: 'inherit' });
  console.log('Tunnel login: demo   Password:', password);
  tunnel.on('error', err => { console.error(err.message); proxy.close(); process.exitCode = 1; });
  tunnel.on('exit', code => { proxy.close(); process.exitCode = code ?? 0; });
  for (const signal of ['SIGTERM','SIGINT']) process.once(signal, () => tunnel.kill('SIGTERM'));
});
