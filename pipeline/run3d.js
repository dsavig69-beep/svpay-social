// 3D heroes: renders each slide's scene file in headless Chromium (WebGL through SwiftShader) at 2160x2700.
//
//   [SCENE=scene-riverside.html] node run3d.js <job> [<job> ...] [--passes <dir>]
//   job: "1", or "1:out_name:ry=-0.2&sz=7" (slide, output name, URL knobs)
//
// --passes <dir> also writes the 3D measurement passes the script tier reads (checks/image), named after the job's
// output name (normally posts/<folder>/passes/<out_name>.<pass>.png): id, idj, cover, normal, shadow and
// <out_name>.scene.json. See passes3d.js. render.js's manifest records which hero each slide uses, so the checker
// finds a slide's 3D passes through it.
const { chromium } = require('playwright');
const http = require('http'); const fs = require('fs'); const path = require('path');
const types = { '.html':'text/html', '.js':'text/javascript', '.png':'image/png', '.woff2':'font/woff2', '.woff':'font/woff', '.json':'application/json' };
const root = process.cwd();
const SCENE = process.env.SCENE || 'scene.html';   // a post's own scene file, e.g. SCENE=scene-riverside.html
const argv = process.argv.slice(2);
const pi = argv.indexOf('--passes'); const passesDir = pi >= 0 ? argv.splice(pi, 2)[1] : null;
// Every scene file ends with `window.__done = true;`. The passes need the scene, renderer and camera, so the server
// exposes them on window just before that line (older scene files included, which is how past renders are re-measured).
const EXPOSE = 'try { window.__THREE = THREE; window.__scene = scene; window.__renderer = renderer; window.__cam = cam; } catch (e) { console.log("INFO passes: " + e.message); }\n';
const srv = http.createServer((req, res) => {
  const f = path.join(root, decodeURIComponent(req.url.split('?')[0]));
  fs.readFile(f, (e, d) => {
    if (e) { res.writeHead(404); return res.end(); }
    if (path.basename(f) === path.basename(SCENE)) d = Buffer.from(d.toString().replace('window.__done = true;', EXPOSE + 'window.__done = true;'));
    res.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' }); res.end(d);
  });
}).listen(8767, async () => {
  const b = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--force-color-profile=srgb'] });
  const jobs = argv; let tries = 0;
  for (let ji = 0; ji < jobs.length; ji++) { const job = jobs[ji];          // "1"  or  "1:out_name:ry=-0.2&sz=7"
    const [n, outName, extra] = job.split(':');
    const p = await b.newPage({ viewport: { width: 2160, height: 2700 } });
    const logs = [];
    p.on('pageerror', e => { logs.push('ERR ' + e.message); console.log(job, 'ERR', e.message); });
    p.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') logs.push(m.type() + ' ' + m.text()); if (m.type() === 'error' || m.text().startsWith('INFO')) console.log(job, m.text()); });
    p.on('requestfailed', r => logs.push('failed ' + r.url()));
    const t0 = Date.now();
    await p.goto(`http://localhost:8767/${SCENE}?slide=${n}${extra ? '&' + extra : ''}`);
    try { await p.waitForFunction(() => window.__done === true, null, { timeout: 600000 }); } catch (e) { console.log(job, 'TIMEOUT'); await p.close(); continue; }
    const info = await p.evaluate(() => window.__info || null);
    const out = (outName || `hero_${n}`) + '.png';
    const data = await p.evaluate(() => document.querySelector('canvas').toDataURL('image/png'));
    const buf = Buffer.from(data.split(',')[1], 'base64');
    if (buf.length < 400000) { console.log(job, 'BLANK render, retrying'); await p.close(); if (++tries < 3) jobs.push(job); continue; }
    fs.writeFileSync(out, buf);
    if (info) fs.writeFileSync(`info_${n}.json`, JSON.stringify(info));
    console.log('done', job, out, ((Date.now() - t0) / 1000).toFixed(1) + 's', info ? JSON.stringify(info) : '');
    if (passesDir) {
      const t1 = Date.now(); fs.mkdirSync(passesDir, { recursive: true });
      const src = fs.readFileSync(path.join(__dirname, 'passes3d.js'), 'utf8');
      const res = await p.evaluate(`(async () => { ${src}; return await passes3d(${JSON.stringify({})}); })()`);
      const base = path.join(passesDir, path.basename(out, '.png'));
      for (const [k, v] of Object.entries(res.images)) fs.writeFileSync(`${base}.${k}.png`, Buffer.from(v.split(',')[1], 'base64'));
      res.scene.job = job; res.scene.scene_file = SCENE; res.scene.hero = out; res.scene.render_seconds = +((t1 - t0) / 1000).toFixed(1); res.scene.console = logs;
      fs.writeFileSync(`${base}.scene.json`, JSON.stringify(res.scene));
      console.log('passes', job, Object.keys(res.images).join(','), ((Date.now() - t1) / 1000).toFixed(1) + 's');
    }
    await p.close();
  }
  await b.close(); srv.close();
});
