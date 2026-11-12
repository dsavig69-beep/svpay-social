// In-page code for run3d.js --passes: renders the 3D measurement passes from the finished scene and returns them.
// It needs window.__THREE, __scene, __renderer and __cam (run3d.js exposes them in every scene file it serves).
//
// Passes (all at the drawing-buffer size, no anti-aliasing, no tone mapping):
//   id      the visible surface: R = part (one per extruded shape, 1-254), G = 1 front/back cap, 2 bevel and side wall,
//           3 other surface, B = object (mesh, 1-254). Background, floor and floor reflections are left out.
//   idj     the same ID pass from a camera moved sideways by 1e-4 of its distance and with near x 1.1 (R-Z03)
//   cover   each part's full silhouette ignoring the other parts, one bit per part (R bits = parts 1-8, G 9-16, B 17-24,
//           A 25-32; cover2.png holds parts 33-64)
//   shadow  the floor's shadow alone, 255 = lit, 0 = full shadow (the floor at opacity 1, casters invisible)
//   normal  view-space normals of the parts, RGB = n x 0.5 + 0.5
//   scene   camera, lights, shadow camera, renderer settings, and per part: the recognized character, the role,
//           the extrude settings, the outline and hole points and the bevel-expanded outline three.js builds from them
async function passes3d(opt) {
  const THREE = window.__THREE, scene = window.__scene, renderer = window.__renderer, cam = window.__cam;
  const W = renderer.domElement.width, H = renderer.domElement.height;
  const out = { images: {}, scene: {} };
  scene.updateMatrixWorld(true); cam.updateMatrixWorld(true);

  // ---------------------------------------------------------------- what is in the scene
  const meshes = []; scene.traverse(o => { if (o.isMesh) meshes.push(o); });
  const under = (o, f) => { for (let a = o.parent; a; a = a.parent) if (f(a)) return true; return false; };
  const kindOf = m => under(m, a => a === cam) ? 'background' : (m.material && m.material.isShadowMaterial) ? 'floor'
    : under(m, a => a.scale && a.scale.y < 0) ? 'reflection' : 'object';
  const objects = meshes.filter(m => kindOf(m) === 'object' && m.visible && !under(m, a => a.visible === false));

  // glyph library: the shapes of each character in the 3D font, to recognize what an extruded shape is
  let FONT = null;
  try {
    const opentype = (await import('opentype')).default;
    const url = opt.font || 'node_modules/@fontsource/inter-tight/files/inter-tight-latin-800-normal.woff';
    FONT = opentype.parse(await (await fetch(url)).arrayBuffer()); out.scene.font_url = url;
  } catch (e) { out.scene.font_error = String(e); }
  const shapesOf = (path) => { const sp = new THREE.ShapePath();
    for (const c of path.commands) {
      if (c.type === 'M') sp.moveTo(c.x, -c.y); else if (c.type === 'L') sp.lineTo(c.x, -c.y);
      else if (c.type === 'Q') sp.quadraticCurveTo(c.x1, -c.y1, c.x, -c.y); else if (c.type === 'C') sp.bezierCurveTo(c.x1, -c.y1, c.x2, -c.y2, c.x, -c.y);
    }
    return sp.toShapes(false); };
  const sig = (shape, seg = 12) => { const p = shape.extractPoints(seg); const xs = p.shape.map(v => v.x), ys = p.shape.map(v => v.y);
    const area = Math.abs(THREE.ShapeUtils.area(p.shape)) - p.holes.reduce((a, h) => a + Math.abs(THREE.ShapeUtils.area(h)), 0);
    return { w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys), area, holes: p.holes.length, cx: (Math.max(...xs) + Math.min(...xs)) / 2 }; };
  const LIB = [];
  if (FONT) for (const ch of '0123456789$%,.?!-+/ABCDEFGHIJKLMNOPQRSTUVWXYZ') for (const s of shapesOf(FONT.getPath(ch, 0, 0, 100))) LIB.push({ ch, ...sig(s) });
  const recognize = s => { const g = sig(s); let best = null;
    for (const L of LIB) { if (L.holes !== g.holes) continue;
      const e = Math.abs(L.w - g.w) / Math.max(1, L.w) + Math.abs(L.h - g.h) / Math.max(1, L.h) + Math.abs(L.area - g.area) / Math.max(1, L.area);
      if (e < 0.02 && (!best || e < best.e)) best = { ch: L.ch, e }; }
    return best ? best.ch : null; };

  // three.js ExtrudeGeometry's bevel vector (r160 getBevelVec), to rebuild the expanded outline its side walls follow
  function bevelVec(p, prev, next) {
    let tx, ty, shrink; const px = p.x - prev.x, py = p.y - prev.y, nx = next.x - p.x, ny = next.y - p.y;
    const plsq = px * px + py * py, col = px * ny - py * nx;
    if (Math.abs(col) > Number.EPSILON) {
      const pl = Math.sqrt(plsq), nl = Math.sqrt(nx * nx + ny * ny);
      const psx = prev.x - py / pl, psy = prev.y + px / pl, nsx = next.x - ny / nl, nsy = next.y + nx / nl;
      const sf = ((nsx - psx) * ny - (nsy - psy) * nx) / (px * ny - py * nx);
      tx = psx + px * sf - p.x; ty = psy + py * sf - p.y; const tl = tx * tx + ty * ty;
      if (tl <= 2) return [tx, ty]; shrink = Math.sqrt(tl / 2);
    } else {
      let eq = false;
      if (px > Number.EPSILON) { if (nx > Number.EPSILON) eq = true; } else if (px < -Number.EPSILON) { if (nx < -Number.EPSILON) eq = true; } else if (Math.sign(py) === Math.sign(ny)) eq = true;
      if (eq) { tx = -py; ty = px; shrink = Math.sqrt(plsq); } else { tx = px; ty = py; shrink = Math.sqrt(plsq / 2); }
    }
    return [tx / shrink, ty / shrink];
  }
  const expand = (pts, bs) => pts.map((p, i) => { const v = bevelVec(p, pts[(i - 1 + pts.length) % pts.length], pts[(i + 1) % pts.length]); return [p.x + v[0] * bs, p.y + v[1] * bs]; });
  const r4 = v => Math.round(v * 1e4) / 1e4;

  // ---------------------------------------------------------------- parts: one per extruded shape
  const parts = []; const objRecs = [];
  const toPx = v => { const p = v.clone().project(cam); return [(p.x * 0.5 + 0.5) * W, (-p.y * 0.5 + 0.5) * H, p.z]; };
  objects.forEach((m, oi) => {
    const geo = m.geometry; geo.computeBoundingBox();
    const rec = { index: oi + 1, name: m.name || '', geometry: geo.type, user: m.userData || {}, parts: [], material: [], parent: m.parent ? m.parent.uuid : null, parent_is_scene: m.parent === scene };
    for (const mt of (Array.isArray(m.material) ? m.material : [m.material])) rec.material.push({ type: mt.type, transmission: mt.transmission || 0, transparent: !!mt.transparent,
      opacity: mt.opacity, side: mt.side, tone_mapped: mt.toneMapped, map_anisotropy: mt.map ? mt.map.anisotropy : null, map_min_filter: mt.map ? mt.map.minFilter : null,
      normal_map: !!mt.normalMap, has_tangents: !!geo.attributes.tangent, env_map_intensity: mt.envMapIntensity });
    rec.matrix = m.matrixWorld.toArray().map(r4); const wb = new THREE.Box3().setFromObject(m); rec.world_box = [wb.min.toArray().map(r4), wb.max.toArray().map(r4)];
    let pieces = [{ geo, shape: null }];
    if (geo.type === 'ExtrudeGeometry' && geo.parameters && geo.parameters.shapes) {
      const shapes = Array.isArray(geo.parameters.shapes) ? geo.parameters.shapes : [geo.parameters.shapes];
      const opts = Object.assign({}, geo.parameters.options); rec.extrude = { depth: opts.depth, bevelEnabled: opts.bevelEnabled !== false, bevelThickness: opts.bevelThickness,
        bevelSize: opts.bevelSize, bevelOffset: opts.bevelOffset || 0, bevelSegments: opts.bevelSegments, curveSegments: opts.curveSegments || 12, steps: opts.steps || 1 };
      const whole = new THREE.ExtrudeGeometry(shapes, opts); whole.computeBoundingBox();
      const b0 = whole.boundingBox, b1 = geo.boundingBox; const k = (b1.max.x - b1.min.x) / Math.max(1e-9, b0.max.x - b0.min.x);
      const ky = (b1.max.y - b1.min.y) / Math.max(1e-9, b0.max.y - b0.min.y);
      if (Math.abs(k - ky) / k < 0.01) {
        const t = new THREE.Vector3().copy(b1.min).sub(b0.min.clone().multiplyScalar(k)); rec.rebuild = { scale: r4(k), translate: t.toArray().map(r4) };
        pieces = shapes.map(s => { const g = new THREE.ExtrudeGeometry(s, opts); g.scale(k, k, k); g.translate(t.x, t.y, t.z); return { geo: g, shape: s }; });
      } else rec.rebuild = { failed: 'non-uniform transform' };
    }
    pieces.forEach(pc => {
      const pi = parts.length + 1; const pr = { part: pi, object: oi + 1 };
      if (pc.shape) {
        const s = pc.shape; pr.char = recognize(s); pr.curve_types = [...new Set(s.curves.map(c => c.type))];
        const seg = rec.extrude.curveSegments; const sp = s.extractPoints(seg); let contour = sp.shape; const holes = sp.holes;
        if (!THREE.ShapeUtils.isClockWise(contour)) { contour = contour.slice().reverse(); for (let h = 0; h < holes.length; h++) if (THREE.ShapeUtils.isClockWise(holes[h])) holes[h] = holes[h].slice().reverse(); }
        const bs = rec.extrude.bevelEnabled ? (rec.extrude.bevelSize || 0) + (rec.extrude.bevelOffset || 0) : 0;
        pr.outline = contour.map(v => [r4(v.x), r4(v.y)]); pr.holes = holes.map(h => h.map(v => [r4(v.x), r4(v.y)]));
        pr.expanded = expand(contour, bs).map(v => v.map(r4)); pr.expanded_holes = holes.map(h => expand(h, bs).map(v => v.map(r4))); pr.bevel_reach = bs;
      }
      const pm = new THREE.Mesh(pc.geo); pm.matrixAutoUpdate = false; pm.matrix.copy(m.matrixWorld); pm.matrixWorld.copy(m.matrixWorld);
      pc.geo.computeBoundingBox(); const bb = pc.geo.boundingBox.clone().applyMatrix4(m.matrixWorld);
      const pts = []; for (const x of [bb.min.x, bb.max.x]) for (const y of [bb.min.y, bb.max.y]) for (const z of [bb.min.z, bb.max.z]) pts.push(toPx(new THREE.Vector3(x, y, z)));
      pr.px_box = [Math.min(...pts.map(p => p[0])), Math.min(...pts.map(p => p[1])), Math.max(...pts.map(p => p[0])), Math.max(...pts.map(p => p[1]))].map(Math.round);
      pr.world_box = [bb.min.toArray().map(r4), bb.max.toArray().map(r4)];
      const pos = pc.geo.attributes.position; let miny = Infinity; const v = new THREE.Vector3();
      for (let i = 0; i < pos.count; i += Math.max(1, Math.floor(pos.count / 4000))) { v.fromBufferAttribute(pos, i).applyMatrix4(m.matrixWorld); miny = Math.min(miny, v.y); }
      pr.min_world_y = r4(miny);
      parts.push(Object.assign(pr, { mesh: pm, src: m })); rec.parts.push(pi);
    });
    objRecs.push(rec);
  });
  // characters and roles: shapes of one recognized character are one glyph; an object made only of digits and the
  // $ % , . that belong to its number is a numeral; a userData.role set by the scene wins
  for (const rec of objRecs) {
    const ps = rec.parts.map(i => parts[i - 1]).filter(p => p.outline);
    const xs = p => { const a = p.outline.map(v => v[0]); return (Math.min(...a) + Math.max(...a)) / 2; };
    const chars = ps.filter(p => p.char).sort((a, b) => xs(a) - xs(b));
    let text = ''; let lastX = null, lastCh = null;
    for (const p of chars) { const x = xs(p); if (!(lastCh === p.char && Math.abs(x - lastX) < 40)) text += p.char; lastX = x; lastCh = p.char; }
    rec.text = rec.user.text || text; rec.unrecognized_parts = ps.filter(p => !p.char).length + rec.parts.length - ps.length;
    const numeric = /\d/.test(rec.text) && /^[$]?[\d.,]+%?$/.test(rec.text);
    rec.role = rec.user.role || (rec.unrecognized_parts === 0 && ps.length ? (numeric ? 'numeral' : 'glyph') : 'other');
  }

  // ---------------------------------------------------------------- rendering helpers
  const keep = { bg: scene.background, env: scene.environment, clear: renderer.getClearColor(new THREE.Color()), alpha: renderer.getClearAlpha(), tone: renderer.toneMapping };
  const vis = new Map(); meshes.forEach(m => vis.set(m, m.visible));
  const rt = new THREE.WebGLRenderTarget(W, H, { samples: 0, type: THREE.UnsignedByteType, depthBuffer: true });
  const scratch = new THREE.Scene();
  const read = () => { const buf = new Uint8Array(W * H * 4); renderer.readRenderTargetPixels(rt, 0, 0, W, H, buf); return buf; };
  const png = (buf, opaque = true) => { const c = document.createElement('canvas'); c.width = W; c.height = H; const x = c.getContext('2d'); const img = x.createImageData(W, H);
    for (let y = 0; y < H; y++) { const s = (H - 1 - y) * W * 4; img.data.set(buf.subarray(s, s + W * 4), y * W * 4); }
    if (opaque) for (let i = 3; i < img.data.length; i += 4) img.data[i] = 255;
    x.putImageData(img, 0, 0); return c.toDataURL('image/png'); };
  const lin = (r, g, b) => new THREE.Color().setRGB(r / 255, g / 255, b / 255, THREE.LinearSRGBColorSpace);
  const flat = c => new THREE.MeshBasicMaterial({ color: c, toneMapped: false });
  function renderParts(matFor, camera = cam, clear = [0, 0, 0, 0]) {
    while (scratch.children.length) scratch.remove(scratch.children[0]);
    for (const p of parts) { if (!p.draw) continue; const m = p.mesh; m.material = matFor(p); scratch.add(m); }
    renderer.setRenderTarget(rt); renderer.setClearColor(new THREE.Color().setRGB(clear[0], clear[1], clear[2], THREE.LinearSRGBColorSpace), clear[3]); renderer.clear(true, true, true);
    renderer.render(scratch, camera); renderer.setRenderTarget(null); return read();
  }
  const idMat = p => { const isEx = p.src.geometry.type === 'ExtrudeGeometry';
    return isEx ? [flat(lin(p.part, 1, p.object)), flat(lin(p.part, 2, p.object))] : flat(lin(p.part, 3, p.object)); };
  parts.forEach(p => p.draw = true);

  // id
  out.images.id = png(renderParts(idMat));
  // idj: camera nudged sideways and near plane moved
  const cj = cam.clone(); const dist = cam.position.length(); const right = new THREE.Vector3().setFromMatrixColumn(cam.matrixWorld, 0);
  cj.position.addScaledVector(right, 1e-4 * dist); cj.near = cam.near * 1.1; cj.updateProjectionMatrix(); cj.updateMatrixWorld(true);
  out.images.idj = png(renderParts(idMat, cj));
  // normal
  out.images.normal = png(renderParts(() => new THREE.MeshNormalMaterial()));
  // cover: each part alone, one bit per part
  const nCover = Math.ceil(parts.length / 32);
  for (let c = 0; c < Math.max(1, nCover); c++) {
    const acc = new Uint8Array(W * H * 4);
    for (let j = 0; j < 32; j++) {
      const p = parts[c * 32 + j]; if (!p) break;
      parts.forEach(q => q.draw = q === p);
      const buf = renderParts(() => flat(lin(255, 255, 255)));
      const ch = j >> 3, bit = 1 << (j & 7);
      for (let i = 0; i < W * H; i++) if (buf[i * 4] > 127) acc[i * 4 + ch] |= bit;
    }
    // a PNG keeps all four bytes exactly only when alpha is not premultiplied: store alpha bits in a second image
    const rgb = new Uint8Array(acc); for (let i = 3; i < rgb.length; i += 4) rgb[i] = 255;
    out.images[c ? 'cover' + (c + 1) : 'cover'] = png(rgb);
    if (parts.length > c * 32 + 24) { const a = new Uint8Array(W * H * 4); for (let i = 0; i < W * H; i++) { a[i * 4] = acc[i * 4 + 3]; a[i * 4 + 3] = 255; } out.images[(c ? 'cover' + (c + 1) : 'cover') + 'a'] = png(a); }
  }
  parts.forEach(p => p.draw = true);
  // shadow: the receiving floor at opacity 1 over white, the casters drawn depth-only
  {
    meshes.forEach(m => m.visible = false); const floors = meshes.filter(m => kindOf(m) === 'floor');
    const saved = new Map();
    for (const f of floors) { saved.set(f, f.material); f.visible = true; f.material = new THREE.ShadowMaterial({ opacity: 1, color: 0x000000, transparent: true, depthWrite: false }); }
    for (const o of objects) { o.visible = true; saved.set(o, o.material); o.material = new THREE.MeshBasicMaterial({ colorWrite: false }); }
    scene.background = null;
    renderer.setRenderTarget(rt); renderer.setClearColor(new THREE.Color(1, 1, 1), 1); renderer.clear(true, true, true); renderer.render(scene, cam); renderer.setRenderTarget(null);
    out.images.shadow = png(read());
    for (const [m, mt] of saved) m.material = mt;
    meshes.forEach(m => m.visible = vis.get(m)); scene.background = keep.bg;
  }

  // ---------------------------------------------------------------- scene.json
  const gl = renderer.getContext(); const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  const name = (re, v) => Object.keys(THREE).find(k => THREE[k] === v && re.test(k)) || String(v);
  const lights = []; scene.traverse(o => { if (o.isLight) { const L = { type: o.type, intensity: o.intensity, color: o.color.getHexString(), position: o.position.toArray().map(r4), cast_shadow: !!o.castShadow };
    if (o.target) L.target = o.target.position.toArray().map(r4);
    if (o.castShadow && o.shadow) { const sc = o.shadow.camera; sc.updateMatrixWorld(true);
      L.shadow = { map_size: [o.shadow.mapSize.x, o.shadow.mapSize.y], radius: o.shadow.radius, blur_samples: o.shadow.blurSamples, bias: o.shadow.bias, normal_bias: o.shadow.normalBias,
        camera: { left: sc.left, right: sc.right, top: sc.top, bottom: sc.bottom, near: sc.near, far: sc.far } };
      // R-S01: is every visible floor point inside the shadow camera (5% margin)? sample the floor through the view
      const floor = meshes.find(m => kindOf(m) === 'floor');
      if (floor) {
        const ray = new THREE.Raycaster(); const fl = new THREE.Plane(new THREE.Vector3(0, 1, 0), -floor.getWorldPosition(new THREE.Vector3()).y);
        o.updateMatrixWorld(true); o.shadow.updateMatrices(o); const view = o.shadow.camera.matrixWorldInverse;
        let outside = 0, total = 0, worst = 0; const hw = (sc.right - sc.left) / 2, hh = (sc.top - sc.bottom) / 2;
        for (let y = 0; y <= 60; y++) for (let x = 0; x <= 48; x++) {
          ray.setFromCamera(new THREE.Vector2(x / 24 - 1, 1 - y / 30), cam); const hit = new THREE.Vector3();
          if (!ray.ray.intersectPlane(fl, hit)) continue;
          const fp = floor.getWorldPosition(new THREE.Vector3()), fsz = floor.geometry.parameters || { width: 1e9, height: 1e9 };
          if (Math.abs(hit.x - fp.x) > fsz.width / 2 || Math.abs(hit.z - fp.z) > fsz.height / 2) continue;   // past the floor mesh: no receiver there
          total++;
          const q = hit.clone().applyMatrix4(view); const fx = Math.abs(q.x - (sc.left + sc.right) / 2) / hw, fy = Math.abs(q.y - (sc.top + sc.bottom) / 2) / hh;
          worst = Math.max(worst, fx, fy); if (fx > 0.95 || fy > 0.95) outside++;
        }
        L.shadow.visible_floor = { samples: total, outside_95pct: outside, worst_fraction: r4(worst) };
      }
    }
    lights.push(L); } });
  const ctxAttr = gl.getContextAttributes();
  out.scene = Object.assign(out.scene, {
    three_revision: THREE.REVISION, gpu: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
    renderer: { tone_mapping: name(/ToneMapping$/, renderer.toneMapping), exposure: renderer.toneMappingExposure, output_color_space: renderer.outputColorSpace,
      color_management: THREE.ColorManagement.enabled, shadow_type: name(/ShadowMap$/, renderer.shadowMap.type), shadows: renderer.shadowMap.enabled,
      pixel_ratio: renderer.getPixelRatio(), drawing_buffer: [W, H], antialias: ctxAttr.antialias, preserve_drawing_buffer: ctxAttr.preserveDrawingBuffer,
      max_samples: renderer.capabilities.maxSamples, max_anisotropy: renderer.capabilities.getMaxAnisotropy(), context_lost: gl.isContextLost() },
    camera: { type: cam.type, fov: cam.fov, near: cam.near, far: cam.far, aspect: cam.aspect, position: cam.position.toArray().map(r4),
      rotation: [cam.rotation.x, cam.rotation.y, cam.rotation.z].map(r4), distance: r4(dist), log_depth: !!renderer.capabilities.logarithmicDepthBuffer },
    environment: { present: !!keep.env, intensity: scene.environmentIntensity ?? null },
    lights, floors: meshes.filter(m => kindOf(m) === 'floor').map(f => ({ position: f.getWorldPosition(new THREE.Vector3()).toArray().map(r4), opacity: f.material.opacity,
      size: f.geometry.parameters ? [f.geometry.parameters.width, f.geometry.parameters.height] : null })),
    reflections: meshes.filter(m => kindOf(m) === 'reflection').length,
    objects: objRecs, parts: parts.map(({ mesh, src, draw, ...p }) => p),
    loading: { fonts: document.fonts.status, images_complete: [...document.images].every(i => i.complete && i.naturalWidth > 0) },
  });
  // R-Z06: vertices of one glyph inside another, and the closest approach, for parts of different characters
  const pairs = [];
  const ray = new THREE.Raycaster();
  for (let a = 0; a < parts.length; a++) for (let b = a + 1; b < parts.length; b++) {
    const A = parts[a], B = parts[b]; if (!A.outline || !B.outline) continue;
    const ba = new THREE.Box3(new THREE.Vector3(...A.world_box[0]), new THREE.Vector3(...A.world_box[1]));
    const bb = new THREE.Box3(new THREE.Vector3(...B.world_box[0]), new THREE.Vector3(...B.world_box[1]));
    const gh = Math.max(ba.max.y - ba.min.y, bb.max.y - bb.min.y);
    if (!ba.clone().expandByScalar(0.05 * gh).intersectsBox(bb)) continue;
    const vA = [], vB = []; const v = new THREE.Vector3();
    for (const [P, arr] of [[A, vA], [B, vB]]) { const pos = P.mesh.geometry.attributes.position; const st = Math.max(1, Math.floor(pos.count / 1500));
      for (let i = 0; i < pos.count; i += st) arr.push(v.fromBufferAttribute(pos, i).clone().applyMatrix4(P.src.matrixWorld)); }
    let md = Infinity; for (const p of vA) for (const q of vB) { const d = p.distanceToSquared(q); if (d < md) md = d; }
    let inside = 0;
    for (const [P, Q, arr] of [[A, B, vA], [B, A, vB]]) {
      const qm = Q.mesh; qm.material = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }); qm.updateMatrixWorld(true);
      for (const p of arr.filter((_, i) => i % 5 === 0)) { ray.set(p, new THREE.Vector3(1, 0.0013, 0.0007).normalize()); if (ray.intersectObject(qm, false).length % 2 === 1) inside++; }
    }
    pairs.push({ a: A.part, b: B.part, same_object: A.object === B.object, chars: [A.char, B.char], min_dist: r4(Math.sqrt(md)), glyph_h: r4(gh), inside });
  }
  out.scene.part_pairs = pairs;
  // R-Z02: planes that faces of two different objects share (coplanar overlapping faces z-fight)
  const planes = new Map();
  for (const P of parts) {
    const pos = P.mesh.geometry.attributes.position; const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), n = new THREE.Vector3();
    const set = new Set(); const st = Math.max(1, Math.floor(pos.count / 3 / 3000));
    for (let t = 0; t < pos.count / 3; t += st) {
      a.fromBufferAttribute(pos, 3 * t).applyMatrix4(P.src.matrixWorld); b.fromBufferAttribute(pos, 3 * t + 1).applyMatrix4(P.src.matrixWorld); c.fromBufferAttribute(pos, 3 * t + 2).applyMatrix4(P.src.matrixWorld);
      n.subVectors(c, b).cross(new THREE.Vector3().subVectors(a, b)); const area = n.length(); if (area < 1e-6) continue; n.divideScalar(area);
      set.add([n.x, n.y, n.z].map(v => Math.round(v * 200) / 200).join(',') + '|' + Math.round(n.dot(a) * 2000) / 2000);
    }
    planes.set(P.part, set);
  }
  const cop = [];
  for (let i = 0; i < parts.length; i++) for (let j = i + 1; j < parts.length; j++) {
    const A = parts[i], B = parts[j]; if (A.object === B.object) continue;
    const ba = new THREE.Box3(new THREE.Vector3(...A.world_box[0]), new THREE.Vector3(...A.world_box[1])), bb = new THREE.Box3(new THREE.Vector3(...B.world_box[0]), new THREE.Vector3(...B.world_box[1]));
    if (!ba.intersectsBox(bb)) continue;
    let shared = 0; for (const k of planes.get(A.part)) if (planes.get(B.part).has(k)) shared++;
    if (shared) cop.push({ a: A.part, b: B.part, shared_planes: shared });
  }
  out.scene.coplanar_pairs = cop;
  renderer.setClearColor(keep.clear, keep.alpha);
  return out;
}
