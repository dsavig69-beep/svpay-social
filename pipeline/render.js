const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const scale = Number(process.argv[2] || 1), file = process.argv[3] || 'slides5.html', pre = process.argv[4] || 'v5s';
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1080, height: 1400 }, deviceScaleFactor: scale });
  await p.goto('file://' + process.cwd() + '/' + file);
  await p.evaluate(() => document.fonts.ready);
  await p.evaluate(() => Promise.all([...document.images].map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; }))));
  await p.evaluate(I3 => window.place(I3), JSON.parse(fs.readFileSync('info_3.json', 'utf8')));
  await p.waitForTimeout(400);
  for (const n of [1, 2, 3, 4, 5, 6]) await (await p.$('#s' + n)).screenshot({ path: `${pre}${n}.png` });
  await b.close();
})();
