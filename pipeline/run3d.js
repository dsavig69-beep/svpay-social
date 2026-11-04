const { chromium } = require('playwright');
const http = require('http'); const fs = require('fs'); const path = require('path');
const types = { '.html':'text/html', '.js':'text/javascript', '.png':'image/png', '.woff2':'font/woff2', '.woff':'font/woff', '.json':'application/json' };
const root = process.cwd();
const srv = http.createServer((req, res) => {
  const f = path.join(root, decodeURIComponent(req.url.split('?')[0]));
  fs.readFile(f, (e, d) => { if (e) { res.writeHead(404); return res.end(); } res.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' }); res.end(d); });
}).listen(8767, async () => {
  const b = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const jobs = process.argv.slice(2); let tries = 0;
  for (let ji = 0; ji < jobs.length; ji++) { const job = jobs[ji];          // "1"  or  "1:out_name:ry=-0.2&sz=7"
    const [n, outName, extra] = job.split(':');
    const p = await b.newPage({ viewport: { width: 2160, height: 2700 } });
    p.on('pageerror', e => console.log(job, 'ERR', e.message));
    p.on('console', m => { if (m.type() === 'error' || m.text().startsWith('INFO')) console.log(job, m.text()); });
    const t0 = Date.now();
    await p.goto(`http://localhost:8767/scene.html?slide=${n}${extra ? '&' + extra : ''}`);
    try { await p.waitForFunction(() => window.__done === true, null, { timeout: 600000 }); } catch (e) { console.log(job, 'TIMEOUT'); await p.close(); continue; }
    const info = await p.evaluate(() => window.__info || null);
    const out = (outName || `hero_${n}`) + '.png';
    const data = await p.evaluate(() => document.querySelector('canvas').toDataURL('image/png'));
    const buf = Buffer.from(data.split(',')[1], 'base64');
    if (buf.length < 400000) { console.log(job, 'BLANK render, retrying'); await p.close(); if (++tries < 3) jobs.push(job); continue; }
    fs.writeFileSync(out, buf);
    if (info) fs.writeFileSync(`info_${n}.json`, JSON.stringify(info));
    console.log('done', job, out, ((Date.now() - t0) / 1000).toFixed(1) + 's', info ? JSON.stringify(info) : '');
    await p.close();
  }
  await b.close(); srv.close();
});
