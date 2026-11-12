const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const scale = Number(process.argv[2] || 1), file = process.argv[3] || 'slides5.html', pre = process.argv[4] || 'v5s';
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1080, height: 1400 }, deviceScaleFactor: scale });
  await p.goto('file://' + process.cwd() + '/' + file);
  await p.evaluate(() => document.fonts.ready);
  await p.evaluate(() => Promise.all([...document.images].map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; }))));
  if (await p.evaluate(() => typeof window.place === 'function'))
    await p.evaluate(I3 => window.place(I3), JSON.parse(fs.readFileSync('info_3.json', 'utf8')));
  await p.waitForTimeout(400);
  // IDS picks the sections to shoot (default 1-6); "1L" shoots <section id="s1L"> to <pre>1L.png
  for (const n of (process.env.IDS || '1,2,3,4,5,6').split(',')) await (await p.$('#s' + n)).screenshot({ path: `${pre}${n}.png` });
  await b.close();
})();
