// Text over the 3D heroes: shoots each <section id="sN"> of a slides file at 2160x2700.
//
//   node render.js <scale> <slides.html> <out prefix> [--passes <dir>] [--names 1:slide-1,1L:slide-1-single,...]
//
// --passes writes the measurement passes the script tier reads (checks/image), next to each slide in <dir>
// (normally posts/<folder>/passes/), named after the slide file (--names maps a section id to it; default slide-<id>):
//   <name>.bg.png         the slide with every text node transparent (the background under the text)
//   <name>.mask.png       text only: black text on white, everything else hidden
//   <name>.unclipped.png  the text mask with overflow visible on every element (ink a box would clip shows here)
//   <name>.manifest.json  every text element: intended text, role, lines and boxes, computed font, size, color,
//                         clip box, the platform fonts Chromium used; plus panels, images, inline SVGs and font status
// Roles come from data-role="headline|body|footnote|source|label|number|cta|footer|legal" when set, else from the
// element's class and size. Boxes are in output pixels (CSS px x scale).
const { chromium } = require('playwright');
const fs = require('fs'); const path = require('path');
const argv = process.argv.slice(2);
const flag = (k) => { const i = argv.indexOf(k); if (i < 0) return null; const v = argv[i + 1]; argv.splice(i, 2); return v; };
const passesDir = flag('--passes'); const namesArg = flag('--names');
(async () => {
  const scale = Number(argv[0] || 1), file = argv[1] || 'slides5.html', pre = argv[2] || 'v5s';
  const names = {}; for (const kv of (namesArg || '').split(',').filter(Boolean)) { const [k, v] = kv.split(':'); names[k] = v; }
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1080, height: 1400 }, deviceScaleFactor: scale });
  const failed = [];
  p.on('requestfailed', r => failed.push(r.url().split('/').pop()));
  p.on('response', r => { if (r.status() >= 400) failed.push(r.url().split('/').pop()); });
  await p.goto('file://' + process.cwd() + '/' + file);
  await p.evaluate(() => document.fonts.ready);
  await p.evaluate(() => Promise.all([...document.images].map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; }))));
  // place(I3, all): I3 is info_3.json (the Nov 4 deck's labels), all holds every info_N.json the 3D run wrote
  const all = {}; for (const n of [1, 2, 3, 4, 5, 6]) if (fs.existsSync(`info_${n}.json`)) all[n] = JSON.parse(fs.readFileSync(`info_${n}.json`, 'utf8'));
  if (await p.evaluate(() => typeof window.place === 'function'))
    await p.evaluate(([I3, A]) => window.place(I3, A), [all[3] || null, all]);
  await p.waitForTimeout(400);
  // IDS picks the sections to shoot (default 1-6); "1L" shoots <section id="s1L"> to <pre>1L.png
  const ids = (process.env.IDS || '1,2,3,4,5,6').split(',');
  for (const n of ids) await (await p.$('#s' + n)).screenshot({ path: `${pre}${n}.png` });
  if (passesDir) {
    fs.mkdirSync(passesDir, { recursive: true });
    const cdp = await p.context().newCDPSession(p);
    for (const n of ids) {
      const name = names[n] || `slide-${n}`;
      const sec = await p.$('#s' + n);
      const man = await p.evaluate(collectManifest, { sel: '#s' + n, scale });
      man.slide = name; man.section = 's' + n; man.file = file; man.failed_requests = [...new Set(failed)];
      man.capture = { scale, viewport: [1080, 1400], width: Math.round(man.size[0]), height: Math.round(man.size[1]),
                      chromium: b.version(), fonts_ready_before_capture: true };
      // the platform fonts Chromium actually used for each element's own text (QR14, T-O10)
      const { root } = await cdp.send('DOM.getDocument', { depth: -1 });
      const { nodeIds } = await cdp.send('DOM.querySelectorAll', { nodeId: root.nodeId, selector: `#s${n} [data-mid]` });
      const fontsBy = {};
      for (const id of nodeIds) {
        const { attributes } = await cdp.send('DOM.getAttributes', { nodeId: id });
        const mid = attributes[attributes.indexOf('data-mid') + 1];
        try { const r = await cdp.send('CSS.getPlatformFontsForNode', { nodeId: id }); fontsBy[mid] = r.fonts; } catch (e) { fontsBy[mid] = null; }
      }
      for (const el of man.text) el.platform_fonts = (el.mids || []).flatMap(m => fontsBy[m] || []);
      fs.writeFileSync(path.join(passesDir, `${name}.manifest.json`), JSON.stringify(man, null, 1));
      for (const [pass, css] of Object.entries(PASS_CSS)) {
        await p.evaluate(([c, sel]) => { const s = document.createElement('style'); s.id = '__pass'; s.textContent = c; document.head.appendChild(s);
          if (c.includes('/*unclipped*/')) for (const e of document.querySelectorAll(sel + ', ' + sel + ' *')) e.style.setProperty('overflow', 'visible', 'important');
          for (const e of document.querySelectorAll(sel + ' *')) if (getComputedStyle(e).webkitBackgroundClip === 'text' || getComputedStyle(e).backgroundClip === 'text') e.setAttribute('data-clip-text', '1'); },
          [css, '#s' + n]);
        await p.waitForTimeout(60);
        await sec.screenshot({ path: path.join(passesDir, `${name}.${pass}.png`) });
        await p.evaluate((sel) => { document.getElementById('__pass').remove(); if (document.querySelector(sel)) for (const e of document.querySelectorAll(sel + ', ' + sel + ' *')) e.style.removeProperty('overflow'); }, '#s' + n);
      }
      console.log('passes', name);
    }
  }
  await b.close();
})();

// CSS for each pass. [data-clip-text] marks gradient text (background-clip: text), whose fill is the background.
const TXT = '[data-mid], [data-mid] *';
const PASS_CSS = {
  bg: `${TXT}{color:transparent!important;-webkit-text-fill-color:transparent!important;text-shadow:none!important;-webkit-text-stroke:0!important;text-decoration-color:transparent!important}
       [data-clip-text]{background-image:none!important;background:none!important}`,
  mask: `section *{background:none!important;background-image:none!important;border-color:transparent!important;box-shadow:none!important;
         text-shadow:none!important;backdrop-filter:none!important;-webkit-backdrop-filter:none!important;outline:none!important;filter:none!important}
         section{background:#fff!important} section img, section svg, section canvas, section video{visibility:hidden!important}
         section *{color:#000!important;-webkit-text-fill-color:#000!important;text-decoration-color:#000!important}`,
  unclipped: `/*unclipped*/section *{background:none!important;background-image:none!important;border-color:transparent!important;box-shadow:none!important;
         text-shadow:none!important;backdrop-filter:none!important;-webkit-backdrop-filter:none!important;outline:none!important;filter:none!important;clip-path:none!important}
         section{background:#fff!important} section img, section svg, section canvas, section video{visibility:hidden!important}
         section *{color:#000!important;-webkit-text-fill-color:#000!important;text-decoration-color:#000!important}`,
};

// Runs in the page: one record per text block (the nearest block-level element that holds text), in output px.
function collectManifest({ sel, scale }) {
  const sec = document.querySelector(sel); const R0 = sec.getBoundingClientRect();
  const box = r => ({ x: (r.left - R0.left) * scale, y: (r.top - R0.top) * scale, w: r.width * scale, h: r.height * scale });
  const isBlock = e => !['inline', 'contents'].includes(getComputedStyle(e).display) || e === sec;
  const ownText = e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
  const blocks = new Set();
  for (const e of sec.querySelectorAll('*')) {
    if (!ownText(e) || ['SCRIPT', 'STYLE'].includes(e.tagName)) continue;
    let b = e; while (!isBlock(b) && b.parentElement) b = b.parentElement;
    blocks.add(b);
  }
  let mid = 0; const text = [];
  const clipOf = e => {   // the visible box: the section and every ancestor that clips its overflow
    let c = { l: R0.left, t: R0.top, r: R0.right, b: R0.bottom };
    for (let a = e.parentElement; a && a !== sec.parentElement; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (a === sec || cs.overflow !== 'visible' || cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        const r = a.getBoundingClientRect(); c = { l: Math.max(c.l, r.left), t: Math.max(c.t, r.top), r: Math.min(c.r, r.right), b: Math.min(c.b, r.bottom) };
      }
    }
    return box({ left: c.l, top: c.t, width: c.r - c.l, height: c.b - c.t });
  };
  const effOpacity = e => { let o = 1; for (let a = e; a && a !== document.body; a = a.parentElement) o *= +getComputedStyle(a).opacity; return o; };
  const sizes = [];
  for (const b of blocks) {
    const mids = [];
    for (const e of [b, ...b.querySelectorAll('*')]) if (ownText(e)) { e.setAttribute('data-mid', String(mid)); mids.push(String(mid)); mid++; }
    // lines: walk every character, group by line box; runs keep each inline element's own style
    const chars = []; const walker = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const st = getComputedStyle(n.parentElement); const tt = st.textTransform;
      for (let i = 0; i < n.textContent.length; i++) {
        const rg = document.createRange(); rg.setStart(n, i); rg.setEnd(n, i + 1);
        const rs = [...rg.getClientRects()].filter(r => r.width > 0 || r.height > 0); if (!rs.length) continue;
        let ch = n.textContent[i]; if (tt === 'uppercase') ch = ch.toUpperCase();
        chars.push({ ch, r: rs[0], el: n.parentElement });
      }
    }
    const lines = [];
    for (const c of chars) {
      const mid = c.r.top + c.r.height / 2;
      let L = lines.find(l => Math.abs(l.mid - mid) < Math.max(4, c.r.height * 0.35) && c.r.left >= l.right - c.r.height);
      if (!L) { L = { mid, chars: [], left: c.r.left, right: c.r.right, top: c.r.top, bottom: c.r.bottom }; lines.push(L); }
      L.chars.push(c); L.left = Math.min(L.left, c.r.left); L.right = Math.max(L.right, c.r.right); L.top = Math.min(L.top, c.r.top); L.bottom = Math.max(L.bottom, c.r.bottom);
    }
    lines.sort((a, b2) => a.top - b2.top || a.left - b2.left);
    const lineRecs = lines.map(l => ({ text: l.chars.map(c => c.ch).join('').replace(/\s+/g, ' ').trim(), ...box({ left: l.left, top: l.top, width: l.right - l.left, height: l.bottom - l.top }),
      chars: l.chars.filter(c => c.ch.trim()).map(c => [c.ch, ...Object.values(box(c.r)).map(v => Math.round(v * 10) / 10)]) })).filter(l => l.text);
    if (!lineRecs.length) continue;
    const cs = getComputedStyle(b);
    const runs = [];
    for (const e of [b, ...b.querySelectorAll('[data-mid]')]) {
      if (!ownText(e)) continue; const s = getComputedStyle(e);
      runs.push({ mid: e.getAttribute('data-mid'), text: [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim(),
        family: s.fontFamily, weight: +s.fontWeight, style: s.fontStyle, size: parseFloat(s.fontSize) * scale, letter_spacing: s.letterSpacing === 'normal' ? 0 : parseFloat(s.letterSpacing) * scale,
        line_height: s.lineHeight === 'normal' ? null : parseFloat(s.lineHeight) * scale, color: s.color, fill: s.webkitTextFillColor, gradient_text: (s.webkitBackgroundClip === 'text' || s.backgroundClip === 'text') ? s.backgroundImage : null,
        opacity: effOpacity(e), blend: s.mixBlendMode, text_shadow: s.textShadow, stroke: s.webkitTextStrokeWidth, transform: s.transform, features: s.fontFeatureSettings, variant_numeric: s.fontVariantNumeric,
        text_transform: s.textTransform, decoration: s.textDecorationLine, hyphens: s.hyphens });
    }
    const fs0 = runs.length ? Math.max(...runs.map(r => r.size)) : parseFloat(cs.fontSize) * scale;
    sizes.push(fs0);
    const cls = (b.className && b.className.baseVal === undefined ? b.className : '') + ' ' + (b.parentElement ? b.parentElement.className || '' : '');
    const full = lineRecs.map(l => l.text).join(' ');
    let role = b.getAttribute('data-role') || b.closest('[data-role]')?.getAttribute('data-role');
    if (!role) {
      if (/\bsrc\b|source/.test(cls) || /^(Sources?|Source:)/.test(full)) role = 'source';
      else if (/not affiliated|Figures are approximate|Example:|example only/i.test(full) && fs0 < 40 * scale) role = 'footnote';
      else if (/\burl\b/.test(cls) || /^www\.svpay\.app$/.test(full.trim())) role = 'footer';
      else if (/\bcount\b/.test(cls) || /^\d+\s*\/\s*\d+$/.test(full.trim())) role = 'counter';
      else if (/\bpill\b|\bbtn\b|button/.test(cls)) role = 'cta';
      else if (/\btag\b|\bchip\b|\bkick\b/.test(cls) || (cs.textTransform === 'uppercase' && fs0 <= 26 * scale)) role = 'label';
      else if (fs0 >= 56 * scale) role = 'headline';
      else role = 'body';
    }
    const r = b.getBoundingClientRect();
    text.push({ id: text.length, mids, role, text: full, lines: lineRecs, box: box({ left: Math.min(...lines.map(l => l.left)), top: Math.min(...lines.map(l => l.top)),
      width: Math.max(...lines.map(l => l.right)) - Math.min(...lines.map(l => l.left)), height: Math.max(...lines.map(l => l.bottom)) - Math.min(...lines.map(l => l.top)) }),
      container: box(r), clip: clipOf(b), align: cs.textAlign, white_space: cs.whiteSpace, size: fs0,
      family: cs.fontFamily, weight: +cs.fontWeight, color: cs.color, runs, html_layer: true });
  }
  const panels = [...sec.querySelectorAll('*')].filter(e => { const s = getComputedStyle(e);
    return (s.backdropFilter && s.backdropFilter !== 'none') || e.classList.contains('glass') || e.classList.contains('card'); })
    .map(e => { const s = getComputedStyle(e); return { ...box(e.getBoundingClientRect()), radius: parseFloat(s.borderTopLeftRadius) * scale, backdrop: s.backdropFilter,
      background: s.backgroundColor, border: s.borderTopWidth + ' ' + s.borderTopColor, box_shadow: s.boxShadow, cls: String(e.className) }; });
  const images = [...sec.querySelectorAll('img')].map(i => ({ src: i.getAttribute('src'), cls: String(i.className), complete: i.complete, natural: [i.naturalWidth, i.naturalHeight],
    ...box(i.getBoundingClientRect()) }));
  const svgs = [...sec.querySelectorAll('svg')].map(s => ({ ...box(s.getBoundingClientRect()), html: s.outerHTML.replace(/\s+/g, ' ') }));
  const fonts = [...document.fonts].map(f => ({ family: f.family.replace(/"/g, ''), weight: f.weight, style: f.style, status: f.status }));
  const fontFiles = [...document.styleSheets].flatMap(ss => { try { return [...ss.cssRules]; } catch (e) { return []; } })
    .filter(r => r.constructor.name === 'CSSFontFaceRule').map(r => ({ family: r.style.getPropertyValue('font-family').replace(/"/g, ''), weight: r.style.getPropertyValue('font-weight'),
      style: r.style.getPropertyValue('font-style') || 'normal', src: (r.style.getPropertyValue('src').match(/url\("?([^")]+)"?\)/) || [])[1] }));
  const hero = images.find(i => /\bhero\b/.test(i.cls));
  return { size: [R0.width * scale, R0.height * scale], text, panels, images, svgs, hero: hero ? hero.src : null,
    fonts: { status: document.fonts.status, faces: fonts, files: fontFiles }, images_complete: images.every(i => i.complete && i.natural[0] > 0) };
}
