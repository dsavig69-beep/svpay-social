"""3D render checks (R-, T-D, V-I06, V-I23): geometry from scene.json, silhouettes from the object-ID and cover passes,
shadows from the shadow-only pass, normals from the normal pass.

The 3D passes come from pipeline/run3d.js --passes and are linked to a slide through the manifest's hero. A slide with
no 3D hero gets na on these checks. Without the passes, R-S02 and R-A08 fall back to estimates on the final image,
which can fail a gate but never clear it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re

import numpy as np

from . import check
from .core import NEEDS, Outcome, apca, apca_y, de00, estimate, euler, gray, lstar, na, ok, rel_lum, to_lab

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

NUMERAL_RULE_FROM = "2026-11-11"   # ruling 3: 3D only on numerals, for posts dated Nov 11, 2026 or later


def has3d(sl):
    """None when the slide has no 3D hero, a NEEDS outcome when it has one but no 3D passes, else True."""
    if sl.scene is not None:
        return True if sl.scene.get("objects") else None
    if sl.manifest is not None:
        h = sl.manifest.get("hero") or ""
        if "hero" in os.path.basename(h):
            return Outcome(NEEDS, "needs passes: 3D passes for " + os.path.basename(h))
        return None
    return Outcome(NEEDS, "needs passes: manifest and 3D passes")


def gate3d(fn):
    """Decorator: skip slides without 3D; report missing passes."""
    def wrap(post, sl):
        h = has3d(sl)
        if h is None:
            return na("no 3D hero on this slide")
        if isinstance(h, Outcome):
            return h
        return fn(post, sl)
    wrap.__name__ = fn.__name__
    return wrap


def scene_val(sl, *path, default=None):
    v = sl.scene
    for p in path:
        if v is None:
            return default
        v = v.get(p) if isinstance(v, dict) else None
    return default if v is None else v


def key_light(sl):
    ls = [l for l in scene_val(sl, "lights", default=[]) if l.get("cast_shadow")]
    return ls[0] if ls else None


def glyphs(sl):
    """Glyph instances: parts grouped by recognized character within an object."""
    out = []
    for o in sl.objects():
        ps = [p for p in sl.parts() if p["object"] == o["index"]]
        groups = []
        for p in sorted(ps, key=lambda p: np.mean([v[0] for v in p.get("outline") or [[0, 0]]])):
            cx = np.mean([v[0] for v in p.get("outline") or [[0, 0]]])
            if groups and p.get("char") and groups[-1]["char"] == p["char"] and abs(groups[-1]["cx"] - cx) < 45:
                groups[-1]["parts"].append(p)
            else:
                groups.append({"char": p.get("char"), "parts": [p], "cx": cx, "object": o})
        out += groups
    return out


def numeral_glyphs(sl):
    return [g for g in glyphs(sl) if g["object"].get("role") in ("numeral",) or (g["char"] and g["char"] in "0123456789$%,.")]


def union_cover(sl, parts):
    m = None
    for p in parts:
        c = sl.cover(p["part"])
        if c is None:
            continue
        m = c if m is None else (m | c)
    return m


def visible(sl, parts, group=None):
    a = sl.id_map
    if a is None:
        return None
    m = np.isin(a[..., 0], [p["part"] for p in parts])
    if group is not None:
        m &= a[..., 1] == group
    return m


def expected_euler(parts):
    return sum(1 - len(p.get("holes") or []) for p in parts)


# ------------------------------------------------------------------ camera math (three.js conventions)
def cam_matrices(sl):
    c = sl.scene["camera"]
    rx, ry, rz = c["rotation"]
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    R = Rx @ Ry @ Rz
    return np.array(c["position"], float), R, c


def project(sl, pts):
    pos, R, c = cam_matrices(sl)
    W, H = scene_val(sl, "renderer", "drawing_buffer", default=[2160, 2700])
    v = (np.asarray(pts, float) - pos) @ R          # world -> camera (R columns are camera axes)
    f = 1 / math.tan(math.radians(c["fov"]) / 2)
    x = f / c["aspect"] * v[:, 0] / -v[:, 2]
    y = f * v[:, 1] / -v[:, 2]
    return np.stack([(x * 0.5 + 0.5) * W, (-y * 0.5 + 0.5) * H, -v[:, 2]], 1)


def light_dir(sl):
    L = key_light(sl)
    if not L:
        return None
    d = np.array(L["position"], float) - np.array(L.get("target", [0, 0, 0]), float)
    return d / np.linalg.norm(d)


def floor_y(sl):
    fl = scene_val(sl, "floors", default=[])
    return fl[0]["position"][1] if fl else None


def floor_opacity(sl):
    fl = scene_val(sl, "floors", default=[])
    return fl[0].get("opacity") or 1.0 if fl else 1.0


def shadow_strength(sl):
    a = sl.raw("shadow")
    if a is None:
        return None
    return 1.0 - a[..., 0].astype(np.float32) / 255.0


# ------------------------------------------------------------------ scene settings (asserts on scene.json)
@check("R-P01", scope="image")
def passes_exist(post, sl):
    need = ["manifest", "bg", "mask", "unclipped"]
    miss = [n for n in need if not sl.has(n)]
    h = has3d(sl)
    if h is True:
        miss += [n for n in ("id", "idj", "cover", "normal", "shadow", "scene") if not sl.has(n)]
        a = sl.id_map
        if a is not None and a.shape[:2] != sl.final.shape[:2]:
            miss.append("id size")
    elif isinstance(h, Outcome):
        miss.append("3D passes")
    return ok(not miss, "missing: " + ", ".join(miss) if miss else "")


@check("R-P06", scope="image")
def assets_loaded(post, sl):
    m = sl.manifest
    if not m:
        return Outcome(NEEDS, "needs passes: the render manifest records font and image loading")
    bad = []
    if m["fonts"]["status"] != "loaded":
        bad.append("fonts " + m["fonts"]["status"])
    if not m.get("images_complete", True):
        bad.append("an image had not loaded")
    fails = [f for f in m.get("failed_requests", []) if f]
    if fails:
        bad.append("failed requests: " + ", ".join(sorted(set(fails))[:4]))
    if sl.scene:
        lo = sl.scene.get("loading", {})
        if lo.get("fonts") not in (None, "loaded") or lo.get("images_complete") is False:
            bad.append("3D page assets not loaded")
        if any("failed" in c for c in sl.scene.get("console", [])):
            bad.append("3D request failed")
    return ok(not bad, "; ".join(bad))


@check("QR06", scope="image")
def webgl_layer(post, sl):
    h = has3d(sl)
    if h is None:
        return na("no 3D hero")
    if isinstance(h, Outcome):
        return h
    r = sl.scene["renderer"]
    vis = sl.id_map
    cov = 0 if vis is None else int((vis[..., 2] > 0).sum())
    return ok(not r.get("context_lost") and cov > 0, f"context lost {r.get('context_lost')}, 3D coverage {cov}px")


@check("V-I23", scope="image")
@gate3d
def native_resolution(post, sl):
    r = sl.scene["renderer"]
    good = r["drawing_buffer"] == [sl.size[0], sl.size[1]] and r["pixel_ratio"] == 1
    return ok(good, f"buffer {r['drawing_buffer']} at ratio {r['pixel_ratio']}")


@check("R-A07", scope="image")
@gate3d
def aa_kept(post, sl):
    r = sl.scene["renderer"]
    return ok(r.get("antialias") and r.get("max_samples", 0) >= 4, f"antialias {r.get('antialias')}, samples {r.get('max_samples')}")


@check("R-A12", scope="image")
@gate3d
def texture_filtering(post, sl):
    mx = sl.scene["renderer"]["max_anisotropy"]
    maps = [m for o in sl.objects() for m in o["material"] if m.get("map_anisotropy") is not None]
    if not maps:
        return na("no textured materials")
    bad = [m["type"] for m in maps if m["map_anisotropy"] < mx]
    return ok(not bad, f"anisotropy below {mx} on {len(bad)} maps")


@check("R-S01", scope="image")
@gate3d
def shadow_camera_covers(post, sl):
    L = key_light(sl)
    if not L or "shadow" not in L:
        return na("no shadow-casting light")
    vf = L["shadow"].get("visible_floor")
    if not vf:
        return na("no floor")
    return ok(vf["outside_95pct"] == 0, f"{vf['outside_95pct']} of {vf['samples']} visible floor samples within 5% of or past the shadow camera edge", vf["outside_95pct"])


@check("R-T02", scope="image")
@gate3d
def shadow_bundle(post, sl):
    L = key_light(sl)
    if not L:
        return na("no shadow-casting light")
    s = L["shadow"]
    ms = s["map_size"][0]
    good = ms >= 2048 and (ms & (ms - 1)) == 0 and 2 <= s["radius"] <= 6 and abs(s["bias"]) <= 5e-4
    return ok(good, f"map {ms}, radius {s['radius']}, bias {s['bias']}, normal bias {s['normal_bias']}")


@check("R-S07", scope="image")
@gate3d
def texel_density(post, sl):
    L = key_light(sl)
    if not L:
        return na("no shadow-casting light")
    s = L["shadow"]
    texel = (s["camera"]["right"] - s["camera"]["left"]) / s["map_size"][0]
    c = sl.scene["camera"]
    H = sl.size[1]
    px_per_unit = H / (2 * c["distance"] * math.tan(math.radians(c["fov"]) / 2))
    v = texel * px_per_unit
    lim = 1.0 if s["radius"] >= 2 else 0.5
    return ok(v <= lim, f"{v:.2f}px per shadow texel at the glyphs", round(v, 3))


@check("R-S08", scope="image")
@gate3d
def shadow_type_valid(post, sl):
    t = sl.scene["renderer"]["shadow_type"]
    return ok(t != "BasicShadowMap", t)


@check("R-S09", scope="image")
@gate3d
def no_vsm(post, sl):
    t = sl.scene["renderer"]["shadow_type"]
    return ok(t != "VSMShadowMap", t)


@check("R-X03", scope="image")
@gate3d
def tone_mapper(post, sl):
    t = sl.scene["renderer"]["tone_mapping"]
    return ok(t in ("NeutralToneMapping", "AgXToneMapping"), t)


@check("R-X07", scope="image")
@gate3d
def color_space(post, sl):
    r = sl.scene["renderer"]
    return ok(r["output_color_space"] == "srgb" and r["color_management"], f"{r['output_color_space']}, management {r['color_management']}")


@check("R-X08", scope="image")
@gate3d
def bloom(post, sl):
    return na("no post-processing (bloom) in the scene")


@check("R-N03", scope="image")
@gate3d
def mikktspace(post, sl):
    nm = [m for o in sl.objects() for m in o["material"] if m.get("normal_map")]
    if not nm:
        return na("no normal maps")
    return ok(all(m.get("has_tangents") for m in nm), "")


@check("R-N06", scope="image")
@gate3d
def env_map(post, sl):
    env = scene_val(sl, "environment", default={})
    return ok(env.get("present"), "environment " + ("present" if env.get("present") else "missing"))


@check("R-N07", scope="image")
@gate3d
def front_side(post, sl):
    bad = [o["text"] or o["geometry"] for o in sl.objects() if o.get("role") in ("numeral", "glyph") and any(m["side"] != 0 for m in o["material"])]
    return ok(not bad, ", ".join(bad))


@check("R-G03", scope="image")
@gate3d
def transparency_flags(post, sl):
    bad = [o["text"] or o["geometry"] for o in sl.objects() for m in o["material"] if m.get("transmission", 0) > 0 and m.get("transparent")]
    return ok(not bad, "transmissive and transparent: " + ", ".join(bad))


@check("R-Z01", scope="image")
@gate3d
def depth_precision(post, sl):
    c = sl.scene["camera"]
    good = c["far"] / c["near"] <= 1000 and c["near"] >= 0.01 * c["distance"] or c.get("log_depth")
    return ok(good, f"far/near {c['far'] / c['near']:.0f}, near {c['near']} at distance {c['distance']:.1f}")


@check("R-Z07", scope="image")
@gate3d
def no_sinking(post, sl):
    fy = floor_y(sl)
    if fy is None:
        return na("no floor")
    bad = []
    for p in sl.parts():
        h = p["world_box"][1][1] - p["world_box"][0][1]
        if p["min_world_y"] < fy - 0.002 * h:
            bad.append(f"{p.get('char') or p['part']} {p['min_world_y'] - fy:.3f}")
    return ok(not bad, ", ".join(bad))


@check("R-Z08", scope="image")
@gate3d
def near_clip(post, sl):
    c = sl.scene["camera"]
    pts = [[x, y, z] for p in sl.parts() for x in (p["world_box"][0][0], p["world_box"][1][0]) for y in (p["world_box"][0][1], p["world_box"][1][1])
           for z in (p["world_box"][0][2], p["world_box"][1][2])]
    if not pts:
        return na("no parts")
    d = project(sl, pts)[:, 2].min()
    return ok(d >= 1.05 * c["near"], f"closest vertex {d:.2f}", round(float(d), 3))


@check("R-Z09", scope="image")
@gate3d
def card_thickness(post, sl):
    return na("glass cards are HTML panels, not 3D meshes")


@check("R-Z05", scope="image")
@gate3d
def glyph_card_clearance(post, sl):
    cards = [o for o in sl.objects() if o.get("role") == "card"]
    return na("no 3D cards (glass cards are HTML panels; R-G02 covers glyphs behind them)") if not cards else ok(False, "3D cards need a plane test")


@check("R-T03", scope="image")
@gate3d
def floor_edge(post, sl):
    fl = scene_val(sl, "floors", default=[])
    if not fl:
        return na("no floor")
    f = fl[0]
    w, h = (f.get("size") or [200, 200])
    p = f["position"]
    corners = [[p[0] + sx * w / 2, p[1], p[2] + sz * h / 2] for sx in (-1, 1) for sz in (-1, 1)]
    pr = project(sl, corners)
    W, H = sl.size
    inside = [(x, y) for x, y, z in pr if z > 0 and -0.05 * W <= x <= 1.05 * W and -0.05 * H <= y <= 1.05 * H]
    return ok(not inside, f"{len(inside)} floor corners in view")


@check("R-T05", scope="post")
def lighting_preset(post):
    keys = []
    for sl in post.slides:
        if sl.scene and sl.scene.get("objects"):
            L = key_light(sl)
            if L:
                d = np.array(L["position"], float) - np.array(L.get("target", [0, 0, 0]), float)
                keys.append((d / np.linalg.norm(d), L["intensity"], sl.where))
    if len(keys) < 2:
        return na("fewer than 2 slides with 3D passes")
    bad = []
    for d, i, w in keys[1:]:
        ang = math.degrees(math.acos(max(-1, min(1, float(d @ keys[0][0])))))
        if ang > 5 or abs(i - keys[0][1]) / keys[0][1] > 0.10:
            bad.append(f"{w} {ang:.0f}° {i}")
    return ok(not bad, "; ".join(bad))


@check("R-T06", scope="image")
@gate3d
def env_lighting(post, sl):
    lights = scene_val(sl, "lights", default=[])
    tot = sum(l["intensity"] for l in lights) or 1
    amb = sum(l["intensity"] for l in lights if l["type"] in ("AmbientLight", "HemisphereLight"))
    good = scene_val(sl, "environment", "present") and amb <= 0.2 * tot
    return ok(good, f"environment {scene_val(sl, 'environment', 'present')}, ambient {amb / tot:.0%}")


@check("R-T04", scope="post")
def font_provenance(post):
    sc = next((s.scene for s in post.slides if s.scene and s.scene.get("objects")), None)
    if not sc:
        return na("no 3D passes")
    url = sc.get("font_url")
    if not url:
        return ok(False, "scene.json has no 3D font")
    p = os.path.join(post.base, "pipeline", url)
    if not os.path.exists(p):
        p = os.path.join(post.base, url)
    if not os.path.exists(p):
        return na("font file not in this checkout")
    lock = json.load(open(os.path.join(os.path.dirname(__file__), "fonts.lock.json"), encoding="utf-8"))
    sha = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return ok(lock.get(os.path.basename(p)) == sha, f"{os.path.basename(p)} {sha[:12]}")


# ------------------------------------------------------------------ glyph geometry (R-D)
def poly(pts):
    from shapely.geometry import Polygon
    return Polygon(pts)


def clean_ring(r):
    pts = [tuple(v) for v in r if v is not None and None not in v]
    out = []
    for q in pts:
        if not out or (abs(q[0] - out[-1][0]) > 1e-6 or abs(q[1] - out[-1][1]) > 1e-6):
            out.append(q)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


@check("R-D01", scope="image")
@gate3d
def bevel_feasible(post, sl):
    """The outline offset by the bevel's reach, outward and inward, keeps its topology (research method: shapely
    buffer with mitre joins): no parts merge or vanish, counters stay open (50% of their area), strokes stay at least
    1.5 x the reach wide."""
    from shapely.geometry import Polygon
    from skimage.morphology import medial_axis
    bad = []
    for p in sl.parts():
        if not p.get("outline") or not p.get("bevel_reach"):
            continue
        d = float(p["bevel_reach"])
        shell = clean_ring(p["outline"])
        holes = [clean_ring(h) for h in p.get("holes") or [] if len(clean_ring(h)) >= 3]
        if len(shell) < 3:
            continue
        g = Polygon(shell, holes).buffer(0)
        n_poly = len(getattr(g, "geoms", [g]))
        n_holes = sum(len(q.interiors) for q in getattr(g, "geoms", [g]))
        for sign in (1, -1):
            o = g.buffer(sign * d, join_style="mitre", mitre_limit=10)
            geoms = getattr(o, "geoms", [o])
            if o.is_empty or len(geoms) != n_poly or sum(len(q.interiors) for q in geoms) != n_holes:
                bad.append(f"'{p.get('char') or '?'}' changes shape when offset {'out' if sign > 0 else 'in'} by the bevel")
                break
        for h in holes:
            hp = Polygon(h)
            shrunk = hp.buffer(-d, join_style="mitre", mitre_limit=10)
            if hp.area > 0 and shrunk.area < 0.5 * hp.area:
                bad.append(f"'{p.get('char') or '?'}' counter closes to {shrunk.area / hp.area:.0%}")
        # stroke half-width from the medial axis of a 20 px-per-unit raster of the glyph
        minx, miny, maxx, maxy = g.bounds
        k = 20.0 / max(1.0, (maxy - miny) / 60.0)
        W, H = int((maxx - minx) * k) + 4, int((maxy - miny) * k) + 4
        if W * H < 4e6:
            from PIL import Image, ImageDraw
            im = Image.new("1", (W, H), 0)
            dr = ImageDraw.Draw(im)
            for q in getattr(g, "geoms", [g]):
                dr.polygon([((x - minx) * k + 2, (maxy - y) * k + 2) for x, y in q.exterior.coords], fill=1)
                for ring in q.interiors:
                    dr.polygon([((x - minx) * k + 2, (maxy - y) * k + 2) for x, y in ring.coords], fill=0)
            a = np.asarray(im, bool)
            skel, dist = medial_axis(a, return_distance=True)
            if skel.any():
                half = float(np.percentile(dist[skel], 2)) / k
                if half < 1.5 * d:
                    bad.append(f"'{p.get('char') or '?'}' thinnest stroke half-width {half:.2f} under 1.5 x the bevel reach {d:.2f}")
    return ok(not bad, "; ".join(sorted(set(bad))[:5]), len(bad))


@check("R-D03", scope="post")
def font_overlaps(post):
    sc = next((s.scene for s in post.slides if s.scene and s.scene.get("objects")), None)
    if not sc:
        return na("no 3D passes")
    chars = sorted({p.get("char") for s in post.slides if s.scene for p in s.parts() if p.get("char")})
    url = sc.get("font_url")
    p = os.path.join(post.base, "pipeline", url) if url else None
    if not p or not os.path.exists(p):
        return na("3D font file not in this checkout")
    import pathops
    from fontTools.ttLib import TTFont
    from fontTools.pens.recordingPen import RecordingPen
    f = TTFont(p)
    gs, cmap = f.getGlyphSet(), f.getBestCmap()
    bad = []
    for ch in chars:
        g = cmap.get(ord(ch))
        if not g:
            continue
        path = pathops.Path()
        pen = path.getPen(glyphSet=gs)
        gs[g].draw(pen)
        before = (len(list(path.contours)), path.area)
        simp = pathops.simplify(path)
        after = (len(list(simp.contours)), simp.area)
        if before[0] != after[0] or abs(before[1] - after[1]) > 0.001 * max(1, abs(before[1])):
            bad.append(ch)
    return ok(not bad, "overlapping contours in: " + "".join(bad))


@check("R-D04", scope="image")
@gate3d
def holes_winding(post, sl):
    font = font_outlines(post, sl)
    if font is None:
        return na("3D font file not in this checkout")
    bad = []
    for g in glyphs(sl):
        if not g["char"] or g["char"] not in font:
            continue
        exp_polys, exp_holes = font[g["char"]]
        got_polys, got_holes = len(g["parts"]), sum(len(p.get("holes") or []) for p in g["parts"])
        if (got_polys, got_holes) != (exp_polys, exp_holes):
            bad.append(f"'{g['char']}' {got_polys}/{got_holes} vs {exp_polys}/{exp_holes}")
    return ok(not bad, "; ".join(bad))


_FONT = {}


def font_outlines(post, sl):
    url = scene_val(sl, "font_url")
    p = os.path.join(post.base, "pipeline", url) if url else None
    if not p or not os.path.exists(p):
        return None
    if p in _FONT:
        return _FONT[p]
    from fontTools.ttLib import TTFont
    from fontTools.pens.basePen import BasePen
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    f = TTFont(p)
    gs, cmap = f.getGlyphSet(), f.getBestCmap()

    class Pen(BasePen):
        def __init__(s):
            super().__init__(gs)
            s.cs, s.cur = [], []

        def _moveTo(s, pt): s.cur = [pt]
        def _lineTo(s, pt): s.cur.append(pt)
        def _curveToOne(s, a, b, c):
            p0 = s.cur[-1]
            for i in range(1, 17):
                t = i / 16
                s.cur.append(tuple((1 - t) ** 3 * q0 + 3 * (1 - t) ** 2 * t * q1 + 3 * (1 - t) * t * t * q2 + t ** 3 * q3 for q0, q1, q2, q3 in zip(p0, a, b, c)))
        def _qCurveToOne(s, a, b):
            p0 = s.cur[-1]
            for i in range(1, 17):
                t = i / 16
                s.cur.append(tuple((1 - t) ** 2 * q0 + 2 * (1 - t) * t * q1 + t * t * q2 for q0, q1, q2 in zip(p0, a, b)))
        def _closePath(s): s.cs.append(s.cur); s.cur = []
        _endPath = _closePath

    out = {}
    for ch in "0123456789$%,.?":
        g = cmap.get(ord(ch))
        if not g:
            continue
        pen = Pen()
        gs[g].draw(pen)
        u = unary_union([Polygon(c).buffer(0) for c in pen.cs if len(c) > 2])
        # even-odd: rebuild from contours by containment depth
        polys = [Polygon(c) for c in pen.cs if len(c) > 2]
        outers = [p for p in polys if sum(1 for q in polys if q is not p and q.contains(p)) % 2 == 0]
        holes = [p for p in polys if p not in outers]
        out[ch] = (len(outers), len(holes))
    _FONT[p] = out
    return out


@check("R-D05", scope="image")
@gate3d
def tessellation(post, sl):
    errs = []
    for p in sl.parts():
        ol = p.get("outline")
        if not ol or len(ol) < 8:
            continue
        a = np.array(ol)
        # chord deviation: for each segment, the deviation of the polyline from a smooth (spline) refit through its points
        mid = (a + np.roll(a, -1, 0)) / 2
        nxt = (np.roll(a, -1, 0) + np.roll(a, -2, 0)) / 2
        prev = (np.roll(a, 1, 0) + a) / 2
        dev = np.linalg.norm(a - (prev + nxt) / 2, axis=1) * 0.25
        h = p["px_box"][3] - p["px_box"][1]
        units = max(1e-6, (a[:, 1].max() - a[:, 1].min()))
        errs.append(float(np.percentile(dev, 99)) * h / units)
    if not errs:
        return na("no extruded shapes")
    return ok(max(errs) <= 0.25, f"max chord deviation {max(errs):.2f}px", round(max(errs), 3))


@check("R-D06", scope="image")
@gate3d
def bevel_segments(post, sl):
    bad = []
    for o in sl.objects():
        ex = o.get("extrude")
        if not ex or o.get("role") not in ("numeral", "glyph"):
            continue
        hpx = max([p["px_box"][3] - p["px_box"][1] for p in sl.parts() if p["object"] == o["index"]] or [0])
        need_seg = 6 if hpx >= 600 else 4
        creased = (o.get("user") or {}).get("normals") == "creased"
        if ex["bevelSegments"] < need_seg or not creased:
            bad.append(f"{o.get('text')}: {ex['bevelSegments']} segments, normals {'creased' if creased else 'not creased'}")
    return ok(not bad, "; ".join(bad)) if any(o.get("extrude") for o in sl.objects()) else na("no extruded glyphs")


@check("T-D06", scope="image")
@gate3d
def bevel_proportion(post, sl):
    bad, n = [], 0
    for o in sl.objects():
        ex = o.get("extrude")
        if not ex or o.get("role") not in ("numeral", "glyph"):
            continue
        ps = [p for p in sl.parts() if p["object"] == o["index"] and p.get("outline") and p.get("char", "") and p["char"].isdigit()]
        if not ps:
            continue
        n += 1
        cap = float(np.median([np.ptp(np.array(p["outline"])[:, 1]) for p in ps]))
        bs, bt = ex["bevelSize"], ex["bevelThickness"]
        if not (0.04 * cap <= bs <= 0.12 * cap and 0.5 * bs <= bt <= 1.5 * bs):
            bad.append(f"{o.get('text')}: bevel {bs / cap:.1%} of cap height, thickness {bt / max(bs, 1e-9):.1f}x")
    return na("no extruded digits") if not n else ok(not bad, "; ".join(bad))


@check("R-D10", scope="image")
@gate3d
def one_mesh_per_glyph(post, sl):
    multi = [o["text"] for o in sl.objects() if o.get("role") in ("numeral", "glyph") and len(o.get("text") or "") > 1]
    return ok(not multi, "one mesh holds several glyphs: " + ", ".join(multi))


def number_groups(sl):
    """Numbers as a viewer reads them: objects that share a parent and a baseline, in x order."""
    objs = [o for o in sl.objects() if o.get("role") in ("numeral", "glyph")]
    groups = {}
    for o in objs:
        groups.setdefault(o.get("parent"), []).append(o)
    out = []
    for k, os_ in groups.items():
        os_.sort(key=lambda o: o["world_box"][0][0])
        out.append(("".join(o.get("text") or "" for o in os_), os_))
    return out


@check("R-D11", scope="image")
@gate3d
def numerals_only(post, sl):
    if post.date and post.date < NUMERAL_RULE_FROM:
        return na(f"rule applies to posts dated {NUMERAL_RULE_FROM} or later")
    bad = []
    ok_ids = set()
    for txt, os_ in number_groups(sl):
        if re.search(r"\d", txt) and re.fullmatch(r"[$]?[\d.,]*\d[\d.,]*%?", txt):
            ok_ids |= {o["index"] for o in os_}
    for o in sl.objects():
        if o["index"] in ok_ids:
            continue
        what = o.get("text") or {"ExtrudeGeometry": "an extruded shape", "TorusGeometry": "a ring", "RoundedBoxGeometry": "a box or bar"}.get(o["geometry"], o["geometry"])
        bad.append(what)
    return ok(not bad, "3D object that is not a numeral: " + ", ".join(sorted(set(bad))))


@check("R-D12", scope="image")
@gate3d
def baseline_scale(post, sl):
    bad = []
    for txt, os_ in number_groups(sl):
        digits = [p for o in os_ for p in sl.parts() if p["object"] == o["index"] and (p.get("char") or "").isdigit()]
        if len(digits) < 2:
            continue
        hs = [p["world_box"][1][1] - p["world_box"][0][1] for p in digits]
        base = [p["world_box"][0][1] for p in digits]
        h = float(np.median(hs))
        if max(base) - min(base) > 0.005 * h + 0.02 * h * ("6" in txt or "9" in txt or "0" in txt):
            bad.append(f"{txt}: baselines differ by {(max(base) - min(base)) / h:.1%}")
        scales = [o.get("matrix", [1])[0] for o in os_ if o.get("matrix")]
        if scales and max(scales) / max(min(scales), 1e-9) > 1.01:
            bad.append(f"{txt}: scales differ")
    return ok(not bad, "; ".join(bad)) if number_groups(sl) else na("no numerals")


@check("R-D13", scope="post")
def same_geometry(post):
    exs = {}
    for sl in post.slides:
        for o in sl.objects():
            if o.get("role") == "numeral" and o.get("extrude"):
                e = o["extrude"]
                exs.setdefault(json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in e.items()}, sort_keys=True), []).append(sl.where)
    if not exs:
        return na("no 3D numerals with passes")
    return ok(len(exs) == 1, f"{len(exs)} different extrusion settings")


@check("R-D16", scope="image")
@gate3d
def counters_open(post, sl):
    from scipy import ndimage as ndi
    bad, n = [], 0
    for g in numeral_glyphs(sl):
        holes = sum(len(p.get("holes") or []) for p in g["parts"])
        if not holes:
            continue
        cap = visible(sl, g["parts"], 1)
        if cap is None or cap.sum() < 100:
            continue
        n += 1
        filled = ndi.binary_fill_holes(cap)
        got = (filled & ~cap).sum() / max(1, filled.sum())
        exp_out = sum(poly(p["outline"]).area for p in g["parts"])
        exp_holes = sum(poly(h).area for p in g["parts"] for h in p.get("holes") or [])
        exp = exp_holes / max(exp_out, 1e-9)
        if got < 0.85 * exp:
            bad.append(f"'{g['char']}' counter {got:.3f} vs {exp:.3f}")
    return na("no glyphs with counters") if not n else ok(not bad, "; ".join(bad))


# ------------------------------------------------------------------ silhouettes and topology (R-A08, T-D04, R-Z)
@check("R-A08", scope="image")
def glyph_topology(post, sl):
    h = has3d(sl)
    if h is None:
        return na("no 3D hero on this slide")
    if isinstance(h, Outcome):
        return h
    bad, n = [], 0
    for g in glyphs(sl):
        if not g["char"]:
            continue
        cap = visible(sl, g["parts"], 1)
        if cap is None or cap.sum() < 200:
            continue
        n += 1
        e, comps, holes = euler(cap)
        exp = expected_euler(g["parts"])
        if e != exp:
            bad.append(f"'{g['char']}' front face has {comps} pieces and {holes} holes (expected Euler {exp})")
    return na("no glyphs") if not n else ok(not bad, "; ".join(bad[:5]), len(bad))


@check("T-D04", scope="image")
@gate3d
def no_slivers(post, sl):
    from scipy import ndimage as ndi
    bad, n = [], 0
    for g in glyphs(sl):
        if not g["char"]:
            continue
        cap = visible(sl, g["parts"], 1)
        side = visible(sl, g["parts"], 2)
        if cap is None or cap.sum() < 200:
            continue
        n += 1
        area = (cap | side).sum()
        lab, k = ndi.label(cap, structure=np.ones((3, 3)))
        sizes = ndi.sum(cap, lab, range(1, k + 1)) if k else []
        big = [s for s in sizes if s > 0.002 * area]
        if len(big) != len(g["parts"]):
            bad.append(f"'{g['char']}' front face in {len(big)} pieces")
        lab2, k2 = ndi.label(side, structure=np.ones((3, 3)))
        if k2:
            dist = ndi.distance_transform_edt(side)
            for i in range(1, k2 + 1):
                comp = lab2 == i
                s = comp.sum()
                thick = 2 * dist[comp].max()
                if s < 0.002 * area and s > 6 or (thick < 3 and s > 40):
                    bad.append(f"'{g['char']}' wall sliver {s}px, {thick:.0f}px thick")
                    break
    return na("no glyphs") if not n else ok(not bad, "; ".join(bad[:5]), len(bad))


def boundary(a):
    from scipy import ndimage as ndi
    lab = a[..., 0].astype(np.int32) * 4 + a[..., 1]
    b = np.zeros(lab.shape, bool)
    b[:, 1:] |= lab[:, 1:] != lab[:, :-1]
    b[1:, :] |= lab[1:, :] != lab[:-1, :]
    return b, lab


@check("R-Z03", scope="image")
@gate3d
def jitter(post, sl):
    a, j = sl.raw("id"), sl.raw("idj")
    if j is None:
        return Outcome(NEEDS, "needs passes: idj")
    from scipy import ndimage as ndi
    b, la = boundary(a)
    lj = j[..., 0].astype(np.int32) * 4 + j[..., 1]
    band = ndi.binary_dilation(b, iterations=2)
    changed = (la != lj) & ~band
    lab, k = ndi.label(changed)
    sizes = ndi.sum(changed, lab, range(1, k + 1)) if k else []
    big = [s for s in sizes if s >= 4]
    return ok(not big, f"{int(sum(big))} px in {len(big)} regions changed object with a tiny camera move", int(sum(big)))


@check("R-Z04", scope="image")
@gate3d
def speckle(post, sl):
    a = sl.raw("id")
    from scipy import ndimage as ndi
    b, lab = boundary(a)
    obj = lab
    diff = np.zeros(obj.shape, np.int32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == dx == 0:
                continue
            diff += (obj != np.roll(np.roll(obj, dy, 0), dx, 1)).astype(np.int32)
    cand = (diff >= 6) & (obj > 0)
    band = ndi.binary_dilation(ndi.binary_dilation((obj > 0) != np.roll(obj > 0, 1, 1)) | ((obj > 0) != np.roll(obj > 0, 1, 0)), iterations=3)
    cand &= ~band
    n = int(cand.sum())
    return ok(n == 0, f"{n} speckle pixels inside surfaces", n)


@check("R-Z02", scope="image")
@gate3d
def coplanar(post, sl):
    pairs = sl.scene.get("coplanar_pairs")
    if pairs is None:
        return Outcome(NEEDS, "needs passes: scene.json without coplanar_pairs (re-render with the current run3d.js)")
    big = [p for p in pairs if p["shared_planes"] >= 1]
    parts = {p["part"]: p for p in sl.parts()}
    return ok(not big, "; ".join(f"{parts[p['a']].get('char') or p['a']}/{parts[p['b']].get('char') or p['b']} share {p['shared_planes']} planes" for p in big[:4]))


@check("R-Z06", scope="image")
@gate3d
def glyph_clearance3d(post, sl):
    pairs = sl.scene.get("part_pairs", [])
    parts = {p["part"]: p for p in sl.parts()}
    def same_glyph(p):
        A, B = parts[p["a"]], parts[p["b"]]
        if A["object"] != B["object"] or A.get("char") != B.get("char"):
            return False
        ca = np.mean([v[0] for v in A["outline"]]); cb = np.mean([v[0] for v in B["outline"]])
        return abs(ca - cb) < 45
    bad = [f"'{p['chars'][0]}'/'{p['chars'][1]}' {'interpenetrate' if p['inside'] else f'{p['min_dist'] / p['glyph_h']:.1%} apart'}"
           for p in pairs if not same_glyph(p) and (p["inside"] > 0 or p["min_dist"] < 0.02 * p["glyph_h"])]
    return ok(not bad, "; ".join(bad[:5]), len(bad))


@check("R-G01", scope="image")
@gate3d
def transmissive_overlap(post, sl):
    trans = [o for o in sl.objects() if any(m.get("transmission", 0) > 0 for m in o["material"])]
    if len(trans) < 2:
        return na("fewer than 2 transmissive objects")
    bad = []
    covers = {o["index"]: union_cover(sl, [p for p in sl.parts() if p["object"] == o["index"]]) for o in trans}
    for i, a in enumerate(trans):
        for b in trans[i + 1:]:
            ca, cb = covers[a["index"]], covers[b["index"]]
            if ca is None or cb is None:
                continue
            ov = (ca & cb).sum()
            if ov > 0.01 * min(ca.sum(), cb.sum()):
                bad.append(f"{a.get('text') or a['geometry']}/{b.get('text') or b['geometry']} overlap {ov}px")
    return ok(not bad, "; ".join(bad[:4]))


@check("R-G02", scope="image")
@gate3d
def glyph_behind_glass(post, sl):
    panels = (sl.manifest or {}).get("panels", [])
    if not panels:
        return na("no glass panels")
    bad, n = [], 0
    fin = gray(sl.final).astype(np.float64)
    for g in glyphs(sl) + [{"char": o.get("text") or o["geometry"], "parts": [p for p in sl.parts() if p["object"] == o["index"]]} for o in sl.objects() if not o.get("text")]:
        cov = union_cover(sl, g["parts"])
        if cov is None or cov.sum() < 100:
            continue
        for p in panels:
            x0, y0, x1, y1 = int(p["x"]), int(p["y"]), int(p["x"] + p["w"]), int(p["y"] + p["h"])
            sub = cov[max(0, y0):y1, max(0, x0):x1]
            ov = sub.sum()
            if ov <= 0.005 * cov.sum():
                continue
            n += 1
            blur = cv2.GaussianBlur(sub.astype(np.float64), (0, 0), 6)
            lum = fin[max(0, y0):y1, max(0, x0):x1]
            r = np.corrcoef(blur.flatten(), lum.flatten())[0, 1] if blur.std() > 0 and lum.std() > 0 else 0
            if abs(r) < 0.3:
                bad.append(f"'{g['char']}' {ov}px behind a card, visible r {abs(r):.2f}")
    return ok(not bad, "; ".join(bad[:4])) if n else ok(True, "no glyph behind a card")


# ------------------------------------------------------------------ shadows (R-S, R-C08)
def straight_faint_lines(s, exclude, min_len, lo, hi):
    """Straight lines in a [0,1] shadow image whose step is between lo and hi, outside the excluded mask."""
    res = s - cv2.GaussianBlur(s, (0, 0), 4)
    m = ((np.abs(res) > lo * 0.5) & (np.abs(res) < hi) & ~exclude).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 1)))
    lines = cv2.HoughLinesP(m, 1, np.pi / 720, threshold=int(min_len * 0.5), minLineLength=int(min_len), maxLineGap=14)
    out = []
    if lines is not None:
        for x1, y1, x2, y2 in lines.reshape(-1, 4):
            L = math.hypot(x2 - x1, y2 - y1)
            # the line must be a step: different on its two sides, with little texture along it
            out.append((int(L), int(x1), int(y1), int(x2), int(y2)))
    out.sort(reverse=True)
    return out


def exclusion(sl, pad=24):
    from scipy import ndimage as ndi
    W, H = sl.size
    ex = np.zeros((H, W), bool)
    for p in sl.parts():
        c = sl.cover(p["part"])
        if c is not None:
            ex |= c
    if sl.text_mask is not None:
        ex |= sl.text_mask
    for pnl in (sl.manifest or {}).get("panels", []):
        ex[max(0, int(pnl["y"])):int(pnl["y"] + pnl["h"]), max(0, int(pnl["x"])):int(pnl["x"] + pnl["w"])] = True
    return ndi.binary_dilation(ex, iterations=pad)


@check("R-S02", scope="image")
def shadow_edge_line(post, sl):
    h = has3d(sl)
    if h is None:
        return na("no 3D hero on this slide")
    W, H = sl.size
    if isinstance(h, Outcome) or not sl.has("shadow"):
        # estimate on the final image: a long, thin, faint straight dark line outside text (it can fail, never clear)
        g = gray(sl.final).astype(np.float32)
        res = g - cv2.GaussianBlur(g, (0, 0), 3)
        m = ((res < -2.0) & (res > -14)).astype(np.uint8) * 255
        if sl.text_mask is not None:
            m[sl.text_mask] = 0
        lines = cv2.HoughLinesP(m, 1, np.pi / 720, threshold=150, minLineLength=int(0.25 * W), maxLineGap=10)
        found = []
        if lines is not None:
            for x1, y1, x2, y2 in lines.reshape(-1, 4):
                ang = abs(math.degrees(math.atan2(y2 - y1, x2 - x1)))
                if (ang < 5 or ang > 175) and 0.25 * H < y1 < 0.85 * H:
                    found.append((int(math.hypot(x2 - x1, y2 - y1)), int(x1), int(y1)))
        return estimate(not found, f"{len(found)} faint straight lines" + (f", longest {found[0][0]}px at y {found[0][2]}" if found else ""))
    s = shadow_strength(sl)
    op = floor_opacity(sl)
    ex = exclusion(sl, 30)
    lines = straight_faint_lines(s * op * 255 / 255.0, ex, 0.10 * W, 2.0 / 255, 0.2)
    real = []
    for L, x1, y1, x2, y2 in lines:
        n = max(2, int(L))
        xs, ys = np.linspace(x1, x2, n).astype(int), np.linspace(y1, y2, n).astype(int)
        on = s[np.clip(ys, 0, H - 1), np.clip(xs, 0, W - 1)] * op
        off = s[np.clip(ys - 4, 0, H - 1), np.clip(xs, 0, W - 1)] * op
        off2 = s[np.clip(ys + 4, 0, H - 1), np.clip(xs, 0, W - 1)] * op
        step = float(np.median(np.abs(on - (off + off2) / 2)))
        if step * 255 >= 2.0:
            real.append((L, x1, y1, step * 255))
    return ok(not real, f"{len(real)} straight shadow lines" + (f", longest {real[0][0]}px at y {real[0][2]} ({real[0][3]:.1f} levels)" if real else ""), len(real))


@check("R-A05", scope="image")
@gate3d
def shadow_stairs(post, sl):
    s = shadow_strength(sl)
    if s is None or s.max() < 0.05:
        return na("no shadow")
    from skimage.measure import find_contours
    lvl = 0.5 * float(s.max())
    cs = [c for c in find_contours(s, lvl) if len(c) > 200]
    if not cs:
        return na("no shadow edge")
    rms = []
    from scipy.ndimage import gaussian_filter1d
    for c in cs:
        sm = np.stack([gaussian_filter1d(c[:, 0], 6, mode="wrap"), gaussian_filter1d(c[:, 1], 6, mode="wrap")], 1)
        rms.append(float(np.sqrt(((c - sm) ** 2).sum(1).mean())))
    v = max(rms)
    return ok(v <= 0.30, f"RMS {v:.2f}px", round(v, 3))


@check("R-S06", scope="image")
@gate3d
def contact_or_float(post, sl):
    fy = floor_y(sl)
    if fy is None:
        return na("no floor")
    bad = []
    for txt, os_ in number_groups(sl) or [(o.get("text") or o["geometry"], [o]) for o in sl.objects()]:
        ps = [p for o in os_ for p in sl.parts() if p["object"] == o["index"]]
        if not ps:
            continue
        hgt = max(p["world_box"][1][1] for p in ps) - min(p["world_box"][0][1] for p in ps)
        gap = min(p["min_world_y"] for p in ps) - fy
        r = gap / max(hgt, 1e-9)
        if 0.002 < r < 0.05:
            bad.append(f"{txt} hovers {r:.1%} of its height above the floor")
    return ok(not bad, "; ".join(bad))


@check("R-S12", scope="image")
@gate3d
def shadow_density(post, sl):
    s = shadow_strength(sl)
    if s is None:
        return Outcome(NEEDS, "needs passes: shadow")
    reg = (s > 0.15) & ~exclusion(sl, 2)
    if reg.sum() < 200:
        return na("no floor shadow")
    L = lstar(sl.final[reg])
    return ok(L.min() >= 55, f"darkest floor shadow L* {L.min():.0f}", round(float(L.min()), 1))


@check("R-S13", scope="image")
@gate3d
def one_shadow(post, sl):
    s = shadow_strength(sl)
    if s is None:
        return Outcome(NEEDS, "needs passes: shadow")
    from scipy import ndimage as ndi
    lab, k = ndi.label(s > 0.08)
    sizes = ndi.sum(s > 0.08, lab, range(1, k + 1)) if k else []
    lobes = len([x for x in sizes if x > 2000])
    casters = len({o.get("parent") for o in sl.objects()})
    nl = len([l for l in scene_val(sl, "lights", default=[]) if l.get("cast_shadow")])
    return ok(lobes <= max(1, casters) * max(1, nl) * max(1, len(sl.objects())), f"{lobes} shadow lobes")


@check("R-S14", scope="image")
@gate3d
def shadow_direction(post, sl):
    s = shadow_strength(sl)
    d = light_dir(sl)
    if s is None or d is None or s.max() < 0.05:
        return na("no shadow or light")
    cov = union_cover(sl, sl.parts())
    if cov is None:
        return na("no casters")
    reg = (s > 0.1 * s.max()) & ~cov
    if reg.sum() < 100:
        return na("no visible shadow")
    ys, xs = np.nonzero(reg)
    sc = np.array([xs.mean(), ys.mean()])
    cy, cx = np.nonzero(cov)
    base = np.array([cx.mean(), cy.max()])
    obs = sc - base
    pts = [p for p in sl.parts()]
    ctr = np.mean([[(p["world_box"][0][i] + p["world_box"][1][i]) / 2 for i in range(3)] for p in pts], 0)
    fy = floor_y(sl) or 0
    hit = ctr - d * ((ctr[1] - fy) / max(d[1], 1e-6))
    pr = project(sl, [ctr, hit])
    pred = pr[1, :2] - project(sl, [[ctr[0], fy, ctr[2]]])[0, :2]
    if np.linalg.norm(pred) < 5 or np.linalg.norm(obs) < 5:
        return na("shadow falls under the object")
    ang = math.degrees(math.acos(max(-1, min(1, float(obs @ pred / np.linalg.norm(obs) / np.linalg.norm(pred))))))
    return ok(ang <= 15, f"shadow direction off by {ang:.0f}°", round(ang, 1))


@check("R-S17", scope="image")
@gate3d
def shadow_current(post, sl):
    s = shadow_strength(sl)
    d = light_dir(sl)
    if s is None or d is None or s.max() < 0.05:
        return na("no shadow")
    reg = s > 0.25 * s.max()
    ys, xs = np.nonzero(reg)
    obs = np.array([xs.mean(), ys.mean()])
    fy = floor_y(sl) or 0
    pts = []
    for p in sl.parts():
        b0, b1 = np.array(p["world_box"][0]), np.array(p["world_box"][1])
        for t in np.linspace(0, 1, 5):
            q = b0 + (b1 - b0) * np.array([t, 0.5, 0.5])
            pts.append(q - d * ((q[1] - fy) / max(d[1], 1e-6)))
    pred = project(sl, pts)[:, :2].mean(0)
    off = float(np.linalg.norm(obs - pred)) / sl.size[0]
    return ok(off <= 0.05, f"shadow centroid {off:.1%} of the width from where the light puts it", round(off, 4))


@check("R-C08", scope="image")
@gate3d
def shadow_over_text(post, sl):
    s = shadow_strength(sl)
    if s is None or not sl.manifest:
        return Outcome(NEEDS, "needs passes: shadow, manifest")
    op = floor_opacity(sl)
    bad = []
    for e in sl.elements:
        b = e["box"]
        sub = s[int(b["y"]):int(b["y"] + b["h"]), int(b["x"]):int(b["x"] + b["w"])] * op
        if sub.size and sub.max() > 0.02:
            bad.append(f"{e['text'][:20]} {sub.max():.2f}")
    return ok(not bad, "; ".join(bad[:4]))


@check("R-C07", scope="image")
@gate3d
def glyph_text_clearance(post, sl):
    if not sl.has("mask", "manifest"):
        return Outcome(NEEDS, "needs passes: mask")
    from scipy import ndimage as ndi
    cov = union_cover(sl, sl.parts())
    s = shadow_strength(sl)
    if cov is None:
        return na("no 3D objects")
    zone = cov | ((s * floor_opacity(sl) > 0.02) if s is not None else False)
    zone = ndi.binary_dilation(zone, iterations=int(48 * sl.scale))
    tm = sl.text_mask.copy()
    for p in sl.manifest.get("panels", []):
        tm[int(p["y"]):int(p["y"] + p["h"]), int(p["x"]):int(p["x"] + p["w"])] = False
    hit = (zone & tm).sum()
    return ok(hit == 0, f"{hit} text px within 48px of a 3D object or its shadow", int(hit))


@check("V-C18", scope="image")
@gate3d
def text_over_3d(post, sl):
    if not sl.has("mask", "manifest"):
        return Outcome(NEEDS, "needs passes: mask")
    cov = union_cover(sl, sl.parts())
    if cov is None:
        return na("no 3D objects")
    tm = sl.text_mask.copy()
    for p in sl.manifest.get("panels", []):
        tm[int(p["y"]):int(p["y"] + p["h"]), int(p["x"]):int(p["x"] + p["w"])] = False
    hit = int((cov & tm).sum())
    return ok(hit == 0, f"{hit} text px over a 3D silhouette", hit)


@check("R-C05", scope="image")
@gate3d
def tangents(post, sl):
    cov = union_cover(sl, sl.parts())
    if cov is None or not sl.manifest:
        return Outcome(NEEDS, "needs passes: cover, manifest")
    from scipy import ndimage as ndi
    W, H = sl.size
    dist = ndi.distance_transform_edt(~cov)
    bad = []
    rects = [("card", p) for p in sl.manifest.get("panels", [])] + [("text", e["box"]) for e in sl.elements]
    for kind, r in rects:
        x0, y0, x1, y1 = int(max(0, r["x"])), int(max(0, r["y"])), int(min(W, r["x"] + r["w"])), int(min(H, r["y"] + r["h"]))
        if x1 <= x0 or y1 <= y0:
            continue
        edge = np.zeros_like(cov)
        edge[y0, x0:x1] = edge[y1 - 1, x0:x1] = True
        edge[y0:y1, x0] = edge[y0:y1, x1 - 1] = True
        d = dist[edge]
        d = d[d > 0]
        if d.size and 1 <= d.min() <= 23:
            bad.append(f"{kind} {d.min():.0f}px from a 3D edge")
    ys, xs = np.nonzero(cov)
    for name, d in (("left", xs.min()), ("right", W - 1 - xs.max()), ("top", ys.min()), ("bottom", H - 1 - ys.max())):
        if 1 <= d <= 23:
            bad.append(f"3D object {d}px from the {name} edge")
    return ok(not bad, "; ".join(bad[:4]))


@check("R-C06", scope="image")
@gate3d
def overlap_decisive(post, sl):
    panels = (sl.manifest or {}).get("panels", [])
    bad, n = [], 0
    for g in glyphs(sl):
        cov = union_cover(sl, g["parts"])
        if cov is None or not panels:
            continue
        for p in panels:
            sub = cov[int(p["y"]):int(p["y"] + p["h"]), int(p["x"]):int(p["x"] + p["w"])].sum()
            if sub:
                n += 1
                if sub < 0.15 * cov.sum():
                    bad.append(f"'{g['char']}' overlaps a card by {sub / cov.sum():.0%}")
    return ok(not bad, "; ".join(bad)) if n else na("no glyph overlaps a card")


# ------------------------------------------------------------------ image quality on the 3D layer
def obj_mask(sl):
    a = sl.id_map
    return None if a is None else a[..., 2] > 0


@check("V-I06", scope="image")
@gate3d
def aa_coverage(post, sl):
    m = obj_mask(sl)
    from scipy import ndimage as ndi
    edge = m & ~ndi.binary_erosion(m)
    g = gray(sl.final).astype(np.float32)
    inner = cv2.blur(np.where(ndi.binary_erosion(m, iterations=3), g, 0), (9, 9)) / np.maximum(cv2.blur(ndi.binary_erosion(m, iterations=3).astype(np.float32), (9, 9)), 1e-3)
    outer = cv2.blur(np.where(~ndi.binary_dilation(m, iterations=3), g, 0), (9, 9)) / np.maximum(cv2.blur((~ndi.binary_dilation(m, iterations=3)).astype(np.float32), (9, 9)), 1e-3)
    ys, xs = np.nonzero(edge)
    if len(ys) < 50:
        return na("no 3D silhouette")
    lo, hi = np.minimum(inner[ys, xs], outer[ys, xs]), np.maximum(inner[ys, xs], outer[ys, xs])
    v = g[ys, xs]
    strong = (hi - lo) > 20
    part = (v > lo + 0.04 * (hi - lo)) & (v < hi - 0.04 * (hi - lo)) & strong
    ratio = part.sum() / max(1, strong.sum())
    return ok(ratio >= 0.8, f"partial-coverage ratio {ratio:.2f}", round(float(ratio), 3))


@check("R-A02", scope="image")
@gate3d
def edge_profile(post, sl):
    m = obj_mask(sl)
    g = gray(sl.final).astype(np.float32)
    ys, xs = np.nonzero(m[:, 1:] != m[:, :-1])
    rises = []
    for y, x in list(zip(ys, xs))[::15][:800]:
        prof = g[y, max(0, x - 6):x + 7]
        if len(prof) < 13 or prof.max() - prof.min() < 30:
            continue
        p = (prof - prof.min()) / (prof.max() - prof.min())
        if p[0] < p[-1]:
            p = p[::-1]
        i10, i90 = np.argmax(p < 0.9), np.argmax(p < 0.1)
        rises.append(max(0, i90 - i10))
    if not rises:
        return na("no strong silhouette edges")
    med, p95 = float(np.median(rises)), float(np.percentile(rises, 95))
    return ok(0.9 <= med <= 2.2 and p95 <= 3.0, f"median {med:.1f}px, P95 {p95:.1f}px", med)


@check("R-A04", scope="image")
@gate3d
def sparkle(post, sl):
    m = obj_mask(sl)
    L = lstar(sl.final)
    hl = (L > 95) & m
    from scipy import ndimage as ndi
    lab, k = ndi.label(hl)
    if not k:
        return ok(True, "no highlights")
    sizes = ndi.sum(hl, lab, range(1, k + 1))
    big = np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s > 4])
    near = ndi.binary_dilation(big, iterations=3)
    specks = [i + 1 for i, s in enumerate(sizes) if s <= 4 and not near[lab == i + 1].any()]
    return ok(len(specks) <= 10, f"{len(specks)} isolated specks", len(specks))


@check("R-A09", scope="image")
@gate3d
def texture_alias(post, sl):
    m = obj_mask(sl)
    g = gray(sl.final).astype(np.float32)
    ys, xs = np.nonzero(m)
    if len(ys) < 4096:
        return na("3D layer too small")
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    from scipy import ndimage as ndi
    inner = ndi.binary_erosion(m, iterations=6)
    fine = g - cv2.GaussianBlur(g, (0, 0), 1.0)
    coarse = g - cv2.GaussianBlur(g, (0, 0), 8.0)
    hi = float((fine[inner] ** 2).sum() / max((coarse[inner] ** 2).sum(), 1e-9))
    return ok(hi <= 0.15, f"share of surface detail finer than 0.4 cycles/px {hi:.1%}", round(float(hi), 4))


@check("R-A10", scope="image")
@gate3d
def thin_features(post, sl):
    from scipy import ndimage as ndi
    from skimage.morphology import skeletonize
    bad = []
    for g in glyphs(sl):
        cap = visible(sl, g["parts"], 1)
        if cap is None or cap.sum() < 500:
            continue
        d = ndi.distance_transform_edt(cap)
        sk = skeletonize(cap)
        if sk.any():
            w = 2 * np.percentile(d[sk], 2)
            if w < 6 * sl.scale:
                bad.append(f"'{g['char']}' {w:.1f}px")
    return ok(not bad, "; ".join(bad))


@check("R-N01", scope="image")
@gate3d
def seams(post, sl):
    a = sl.id_map
    nrm = sl.img("normal")
    if nrm is None:
        return Outcome(NEEDS, "needs passes: normal")
    from scipy import ndimage as ndi
    g = gray(sl.final).astype(np.uint8)
    edges = cv2.Canny(g, 40, 110) > 0
    n = nrm.astype(np.float32) / 127.5 - 1
    n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-6)
    dot = np.ones(g.shape, np.float32)
    for dy, dx in ((0, 1), (1, 0)):
        sh = np.roll(np.roll(n, dy, 0), dx, 1)
        dot = np.minimum(dot, (n * sh).sum(2))
    crease = ndi.binary_dilation(dot < math.cos(math.radians(8)), iterations=3)
    b, lab = boundary(a)
    explained = crease | ndi.binary_dilation(b, iterations=3)
    L = lstar(sl.final)
    hl = ndi.binary_dilation(np.abs(L - cv2.GaussianBlur(L, (0, 0), 3)) > 6, iterations=1)
    bad = []
    for gl in glyphs(sl):
        m = visible(sl, gl["parts"])
        if m is None or m.sum() < 500:
            continue
        inner = ndi.binary_erosion(m, iterations=4)
        un = edges & inner & ~explained
        lab2, k = ndi.label(un, structure=np.ones((3, 3)))
        per = (m & ~ndi.binary_erosion(m)).sum()
        lens = ndi.sum(un, lab2, range(1, k + 1)) if k else []
        long_ = [l for l in lens if l > 20]
        straight = []
        for i, l in enumerate(lens, 1):
            if l > 20:
                yy, xx = np.nonzero(lab2 == i)
                if np.ptp(xx) + np.ptp(yy) > 0.8 * l:
                    straight.append(l)
        if sum(lens) > 0.02 * per and straight:
            bad.append(f"'{gl['char']}' {int(sum(lens))}px of unexplained edges, longest straight {int(max(straight))}px")
    return ok(not bad, "; ".join(bad[:4]))


@check("R-N02", scope="image")
@gate3d
def normal_continuity(post, sl):
    nrm = sl.img("normal")
    m = obj_mask(sl)
    if nrm is None:
        return Outcome(NEEDS, "needs passes: normal")
    n = nrm.astype(np.float32) / 127.5 - 1
    n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-6)
    ang = np.degrees(np.arccos(np.clip((n * np.roll(n, 1, 1)).sum(2), -1, 1)))
    from scipy import ndimage as ndi
    inner = ndi.binary_erosion(m, iterations=2) & ndi.binary_erosion(np.roll(m, 1, 1), iterations=2)
    jump = (ang > 3) & (ang < 20) & inner
    share = jump.sum() / max(1, m.sum())
    return ok(share <= 0.002, f"{share:.2%} of glyph pixels jump 3-20°", round(float(share), 5))


@check("R-D07", scope="image")
@gate3d
def facet_histogram(post, sl):
    nrm = sl.img("normal")
    a = sl.id_map
    if nrm is None:
        return Outcome(NEEDS, "needs passes: normal")
    n = nrm.astype(np.float32) / 127.5 - 1
    n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-6)
    worst = 0
    for gl in glyphs(sl):
        cap = visible(sl, gl["parts"], 1)
        side = visible(sl, gl["parts"], 2)
        if cap is None or cap.sum() < 200 or side.sum() < 200:
            continue
        f = np.median(n[cap], axis=0)
        f /= np.linalg.norm(f)
        alpha = np.degrees(np.arccos(np.clip(n[side] @ f, -1, 1)))
        sel = alpha[(alpha > 5) & (alpha < 85)]
        if sel.size < 200:
            continue
        h, _ = np.histogram(sel, bins=80, range=(5, 85))
        worst = max(worst, float(h.max() / max(h.mean(), 1e-6)))
    return ok(worst <= 3, f"histogram max/mean {worst:.1f}", round(worst, 2)) if worst else na("no bevels in view")


@check("R-N08", scope="image")
@gate3d
def crisp_edge(post, sl):
    nrm = sl.img("normal")
    if nrm is None:
        return Outcome(NEEDS, "needs passes: normal")
    a = sl.id_map
    n = nrm.astype(np.float32) / 127.5 - 1
    n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-6)
    widths = []
    from scipy import ndimage as ndi
    for gl in glyphs(sl):
        cap = visible(sl, gl["parts"], 1)
        side = visible(sl, gl["parts"], 2)
        if cap is None or cap.sum() < 200 or side.sum() < 200:
            continue
        f = np.median(n[cap], axis=0)
        f /= np.linalg.norm(f)
        alpha = np.degrees(np.arccos(np.clip(n @ f, -1, 1)))
        mid = side & (alpha > 10) & (alpha < 80)
        border = (cap & ndi.binary_dilation(side)).sum()
        if border:
            widths.append(mid.sum() / border)
    return ok(max(widths) <= 6, f"front-to-side ramp {max(widths):.1f}px", round(max(widths), 2)) if widths else na("no front-side edges")


@check("R-N05", scope="image")
@gate3d
def smooth_faces(post, sl):
    L = lstar(sl.final)
    hp = L - cv2.GaussianBlur(L, (0, 0), 8)
    vals = []
    from scipy import ndimage as ndi
    for gl in glyphs(sl):
        cap = visible(sl, gl["parts"], 1)
        if cap is None or cap.sum() < 1000:
            continue
        inner = ndi.binary_erosion(cap, iterations=10)
        if inner.sum() > 200:
            vals.append(float(hp[inner].std()))
    return ok(max(vals) <= 1.0, f"face residual sd {max(vals):.2f}", round(max(vals), 3)) if vals else na("no large faces")


@check("R-X01", scope="image")
@gate3d
def silhouette_separation(post, sl):
    from scipy import ndimage as ndi
    from skimage.measure import find_contours
    L = lstar(sl.final)
    lab = to_lab(sl.final[::2, ::2])
    m = obj_mask(sl)
    tm = sl.text_mask if sl.text_mask is not None else np.zeros_like(m)
    din = ndi.distance_transform_edt(m)
    dout = ndi.distance_transform_edt(~m)
    inb = (din >= 2) & (din <= 5)
    outb = (dout >= 2) & (dout <= 5) & ~tm
    bad = []
    for gl in glyphs(sl):
        g = union_cover(sl, gl["parts"])
        vis = visible(sl, gl["parts"])
        if vis is None or vis.sum() < 500:
            continue
        cs = find_contours(vis.astype(float), 0.5)
        if not cs:
            continue
        c = max(cs, key=len)
        segs, good = 0, 0
        for i in range(0, len(c) - 16, 16):
            y, x = c[i + 8].astype(int)
            y0, y1, x0, x1 = max(0, y - 8), y + 9, max(0, x - 8), x + 9
            a_in, a_out = inb[y0:y1, x0:x1] & vis[y0:y1, x0:x1], outb[y0:y1, x0:x1]
            if a_in.sum() < 3 or a_out.sum() < 3:
                continue
            segs += 1
            dl = abs(L[y0:y1, x0:x1][a_in].mean() - L[y0:y1, x0:x1][a_out].mean())
            if dl >= 8:
                good += 1
            else:
                lin = to_lab(sl.final[y0:y1, x0:x1][a_in].mean(0)[None, None])[0, 0]
                lout = to_lab(sl.final[y0:y1, x0:x1][a_out].mean(0)[None, None])[0, 0]
                if float(de00(lin, lout)) >= 10:
                    good += 1
        if segs and good / segs < 0.85:
            bad.append(f"'{gl['char']}' {good / segs:.0%} of its outline separates")
    return ok(not bad, "; ".join(bad[:4]))


@check("R-X02", scope="image")
@gate3d
def highlight_rolloff(post, sl):
    m = obj_mask(sl)
    clip = (sl.final.max(2) >= 254) & m
    from scipy import ndimage as ndi
    lab, k = ndi.label(clip)
    if not k:
        return ok(True, "no clipped highlights")
    big = max(ndi.sum(clip, lab, range(1, k + 1)))
    share = big / clip.size
    return ok(share <= 0.0005, f"largest clipped area {share:.3%} of the frame", round(float(share), 6))


@check("R-X04", scope="image")
@gate3d
def highlight_hue(post, sl):
    m0 = obj_mask(sl)
    ys, xs = np.nonzero(m0)
    if not len(ys):
        return na("no 3D object")
    y0, y1, x0, x1 = max(0, ys.min() - 12), ys.max() + 12, max(0, xs.min() - 12), xs.max() + 12
    m = m0[y0:y1, x0:x1]
    lab = to_lab(sl.final[y0:y1, x0:x1])
    hl = m & (lab[..., 0] > 92) & (np.hypot(lab[..., 1], lab[..., 2]) > 4)
    if hl.sum() < 50:
        return na("no colored highlights")
    from scipy import ndimage as ndi
    ring = ndi.binary_dilation(hl, iterations=10) & m & ~hl & (lab[..., 0] < 85)
    if ring.sum() < 50:
        return na("no base color around the highlights")
    hh = math.degrees(math.atan2(np.median(lab[hl][:, 2]), np.median(lab[hl][:, 1])))
    hb = math.degrees(math.atan2(np.median(lab[ring][:, 2]), np.median(lab[ring][:, 1])))
    d = abs((hh - hb + 180) % 360 - 180)
    return ok(d <= 10, f"highlight hue off by {d:.0f}°", round(d, 1))


@check("R-X05", scope="image")
@gate3d
def tonal_range(post, sl):
    m = obj_mask(sl)
    L = lstar(sl.final[m])
    v = float(np.percentile(L, 99) - np.percentile(L, 1))
    return ok(v >= 30, f"L* P99-P1 {v:.0f}", round(v, 1))


@check("R-X09", scope="post")
def exposure_parity(post):
    vals = []
    for sl in post.slides:
        if sl.scene and sl.scene.get("objects") and sl.id_map is not None:
            m = sl.id_map[..., 2] > 0
            if m.sum() > 1000:
                vals.append((float(lstar(sl.final[m]).mean()), sl.where))
    if len(vals) < 2:
        return na("fewer than 2 slides with 3D passes")
    med = float(np.median([v for v, _ in vals]))
    bad = [f"{w} {v:.0f}" for v, w in vals if abs(v - med) > 4]
    return ok(not bad, f"median {med:.0f}; " + ", ".join(bad))


@check("R-F01", scope="image")
@gate3d
def fireflies(post, sl):
    L = lstar(sl.final)
    med = cv2.medianBlur(L.astype(np.float32), 3)
    m = obj_mask(sl)
    from scipy import ndimage as ndi
    zone = ndi.binary_dilation(m, iterations=40)
    hot = (L - med > 30) & zone & (L > 90)
    lab, k = ndi.label(hot)
    sizes = ndi.sum(hot, lab, range(1, k + 1)) if k else []
    n = len([s for s in sizes if s <= 2])
    return ok(n <= 3, f"{n} isolated hot pixels", n)


@check("R-F02", scope="image")
def grain_character(post, sl):
    lab = to_lab(sl.final[::2, ::2])
    flat = cv2.GaussianBlur(lab[..., 0], (0, 0), 3)
    res = lab - np.stack([cv2.GaussianBlur(lab[..., i], (0, 0), 3) for i in range(3)], 2)
    grad = np.hypot(cv2.Sobel(flat, cv2.CV_64F, 1, 0), cv2.Sobel(flat, cv2.CV_64F, 0, 1))
    sel = grad < np.percentile(grad, 30)
    luma, chroma = res[..., 0][sel].std(), np.hypot(res[..., 1][sel], res[..., 2][sel]).std()
    if luma < 0.2:
        return na("no measurable grain")
    return ok(chroma <= 0.5 * luma, f"chroma sd {chroma:.2f} vs luma sd {luma:.2f}")


@check("R-F03", scope="image")
@gate3d
def same_grain(post, sl):
    L = lstar(sl.final).astype(np.float32)
    hp = L - cv2.GaussianBlur(L, (0, 0), 1.5)
    m = obj_mask(sl)
    from scipy import ndimage as ndi
    caps = np.zeros_like(m)
    for gl in glyphs(sl):
        c = visible(sl, gl["parts"], 1)
        if c is not None:
            caps |= ndi.binary_erosion(c, iterations=8)
    bgm = ~ndi.binary_dilation(m, iterations=20)
    if sl.text_mask is not None:
        bgm &= ~ndi.binary_dilation(sl.text_mask, iterations=10)
    if caps.sum() < 500:
        return na("no large faces")
    a, b = float(hp[bgm].std()), float(hp[caps].std())
    r = b / max(a, 1e-6)
    return ok(0.75 <= r <= 1.25, f"face grain {b:.2f} vs background {a:.2f}", round(r, 2))


@check("R-G05", scope="image")
def glass_edge(post, sl):
    panels = (sl.manifest or {}).get("panels", [])
    if not sl.manifest:
        return Outcome(NEEDS, "needs passes: manifest")
    if not panels:
        return na("no glass panels")
    L = lstar(sl.final)
    bad = []
    for p in panels:
        x0, y0, x1, y1 = int(p["x"]), int(p["y"]), int(p["x"] + p["w"]), int(p["y"] + p["h"])
        segs, good = 0, 0
        for side in ("t", "b", "l", "r"):
            for t in np.linspace(0.1, 0.9, 12):
                if side in "tb":
                    x = int(x0 + t * (x1 - x0)); y = y0 if side == "t" else y1 - 1
                    i = L[np.clip(y + (2 if side == "t" else -3), 0, L.shape[0] - 1):np.clip(y + (4 if side == "t" else -1), 0, L.shape[0]), x]
                    o = L[np.clip(y - (4 if side == "t" else -2), 0, L.shape[0] - 1):np.clip(y - (2 if side == "t" else -4), 0, L.shape[0]), x]
                else:
                    y = int(y0 + t * (y1 - y0)); x = x0 if side == "l" else x1 - 1
                    i = L[y, np.clip(x + (2 if side == "l" else -3), 0, L.shape[1] - 1):np.clip(x + (4 if side == "l" else -1), 0, L.shape[1])]
                    o = L[y, np.clip(x - (4 if side == "l" else -2), 0, L.shape[1] - 1):np.clip(x - (2 if side == "l" else -4), 0, L.shape[1])]
                if i.size and o.size:
                    segs += 1
                    good += abs(float(i.mean()) - float(o.mean())) >= 3
        if segs and good / segs < 0.9:
            bad.append(f"card at ({x0},{y0}) edge visible on {good / segs:.0%}")
    return ok(not bad, "; ".join(bad[:3]))


@check("R-G06", scope="image")
def glass_backdrop(post, sl):
    panels = (sl.manifest or {}).get("panels", [])
    if not sl.manifest:
        return Outcome(NEEDS, "needs passes: manifest")
    big = [p for p in panels if p["h"] > 150]
    if not big:
        return na("no glass cards")
    bg = sl.img("bg")
    L = lstar(bg)
    bad = [f"card at ({int(p['x'])},{int(p['y'])}) backdrop sd {L[int(p['y']):int(p['y'] + p['h']), int(p['x']):int(p['x'] + p['w'])].std():.1f}"
           for p in big if L[int(p["y"]):int(p["y"] + p["h"]), int(p["x"]):int(p["x"] + p["w"])].std() < 1.0]
    return ok(not bad, "; ".join(bad))


@check("R-G07", scope="image")
def blur_band(post, sl):
    if not sl.manifest:
        return Outcome(NEEDS, "needs passes: manifest")
    cards = [p for p in sl.manifest.get("panels", []) if re.search(r"blur\(([\d.]+)px", p.get("backdrop") or "") and p["w"] > 300]
    if not cards:
        return na("no blurred cards")
    bad = []
    for p in cards:
        s = float(re.search(r"blur\(([\d.]+)px", p["backdrop"]).group(1)) * sl.scale * 2
        r = s / p["w"]
        if not (0.015 <= r <= 0.04):
            bad.append(f"{r:.1%} of card width")
    return ok(not bad, ", ".join(bad))


@check("R-G08", scope="image")
def blur_blockiness(post, sl):
    if not sl.manifest:
        return Outcome(NEEDS, "needs passes: manifest")
    cards = [p for p in sl.manifest.get("panels", []) if p["w"] > 200 and p["h"] > 120]
    if not cards:
        return na("no cards")
    bg = gray(sl.img("bg")).astype(np.float32)
    bad = []
    for p in cards:
        sub = bg[int(p["y"]) + 8:int(p["y"] + p["h"]) - 8, int(p["x"]) + 8:int(p["x"] + p["w"]) - 8]
        if sub.shape[0] < 20 or sub.shape[1] < 20:
            continue
        gx = np.abs(np.diff(sub, axis=1))
        r = gx[:, ::2].mean() / max(gx[:, 1::2].mean(), 1e-6)
        if not (0.9 <= r <= 1.1):
            bad.append(f"{r:.2f}")
    return ok(not bad, "even/odd gradient ratio " + ", ".join(bad))


@check("R-G10", scope="image")
@gate3d
def dispersion(post, sl):
    m = obj_mask(sl)
    from scipy import ndimage as ndi
    edge = ndi.binary_dilation(m, iterations=2) & ~ndi.binary_erosion(m, iterations=2)
    f = sl.final.astype(np.int16)
    rb = np.abs(f[..., 0] - f[..., 2])[edge]
    share = float((rb > 90).mean()) if rb.size else 0
    return ok(share <= 0.05, f"{share:.1%} of edge pixels split red from blue", round(share, 4))


@check("R-K10", scope="image")
def clip_stub_k10(post, sl):
    return na("tool not installed: CLIP (zero-shot classifier)")


@check("R-Q01", scope="image")
@gate3d
def material_count(post, sl):
    kinds = {(m["type"], m.get("transmission", 0) > 0) for o in sl.objects() for m in o["material"]}
    n = len(kinds) + (1 if (sl.manifest or {}).get("panels") else 0) + (1 if scene_val(sl, "floors") else 0)
    return ok(n <= 3, f"{n} materials", n)


def view_light(sl):
    d = light_dir(sl)
    if d is None:
        return None
    _, R, _ = cam_matrices(sl)
    v = d @ R
    return v / np.linalg.norm(v)


@check("R-Q02", scope="image")
@gate3d
def light_ratio(post, sl):
    nrm = sl.img("normal")
    lv = view_light(sl)
    if nrm is None or lv is None:
        return Outcome(NEEDS, "needs passes: normal")
    n = nrm.astype(np.float32) / 127.5 - 1
    m = obj_mask(sl)
    ndl = (n @ lv.astype(np.float32))
    y = rel_lum(sl.final)
    lit, away = y[m & (ndl > 0.5)], y[m & (ndl < 0.0)]
    if lit.size < 100 or away.size < 100:
        return na("not enough lit and unlit glyph surface")
    r = float(lit.mean() / max(away.mean(), 1e-6))
    return ok(1.8 <= r <= 5, f"lit/away {r:.2f}", round(r, 2))


@check("R-Q04", scope="image")
@gate3d
def bevels_catch_light(post, sl):
    bad, n = [], 0
    L = lstar(sl.final)
    for gl in glyphs(sl):
        cap = visible(sl, gl["parts"], 1)
        side = visible(sl, gl["parts"], 2)
        if cap is None or (cap | side).sum() < 500:
            continue
        n += 1
        m = cap | side
        thr = np.percentile(L[m], 95)
        top = m & (L >= thr)
        share = (top & side).sum() / max(1, top.sum())
        if share < 0.5:
            bad.append(f"'{gl['char']}' {share:.0%}")
    return na("no glyphs") if not n else ok(not bad, "bevel share of the brightest pixels: " + ", ".join(bad[:4]))


@check("R-Q05", scope="image")
@gate3d
def soft_highlights(post, sl):
    from scipy import ndimage as ndi
    L = lstar(sl.final)
    bad, n = [], 0
    for gl in glyphs(sl):
        m = visible(sl, gl["parts"])
        if m is None or m.sum() < 500:
            continue
        n += 1
        hl = m & (L >= np.percentile(L[m], 97))
        lab, k = ndi.label(hl)
        big = max(ndi.sum(hl, lab, range(1, k + 1))) if k else 0
        if big < 0.005 * m.sum():
            bad.append(f"'{gl['char']}' {big / m.sum():.2%}")
    return na("no glyphs") if not n else ok(not bad, ", ".join(bad[:4]))


@check("R-C03", scope="image")
@gate3d
def shadow_length(post, sl):
    s = shadow_strength(sl)
    if s is None:
        return Outcome(NEEDS, "needs passes: shadow")
    ratios = []
    for gl in numeral_glyphs(sl):
        cov = union_cover(sl, gl["parts"])
        if cov is None or cov.sum() < 500:
            continue
        ys, xs = np.nonzero(cov)
        x0, x1, ybot, h = xs.min(), xs.max(), ys.max(), ys.max() - ys.min()
        col = (s[:, x0:x1 + 1] > 0.1).any(1)
        below = np.nonzero(col[ybot:])[0]
        above = np.nonzero(col[:ybot])[0]
        ext = max(below.max() if below.size else 0, (ybot - above.min()) if above.size else 0)
        ratios.append(ext / max(h, 1))
    if len(ratios) < 2:
        return na("fewer than 2 glyphs on the floor")
    med = float(np.median(ratios))
    dev = max(abs(r - med) / max(med, 1e-6) for r in ratios)
    return ok(dev <= 0.10, f"max deviation {dev:.0%}", round(dev, 3))


@check("R-B07", scope="image")
@gate3d
def hue_steps(post, sl):
    bad = 0
    tot = 0
    for gl in glyphs(sl):
        cap = visible(sl, gl["parts"], 1)
        if cap is None or cap.sum() < 1000:
            continue
        ys, xs = np.nonzero(cap)
        y = int(np.median(ys))
        row = np.nonzero(cap[y])[0]
        if len(row) < 12:
            continue
        lrow = to_lab(sl.final[y:y + 1, row])[0]
        a, b = lrow[:, 1], lrow[:, 2]
        c = np.hypot(a, b)
        h = np.degrees(np.arctan2(b, a))
        d = np.abs((h[4:] - h[:-4] + 180) % 360 - 180)
        sel = (c[4:] >= 10) & (c[:-4] >= 10)
        tot += sel.sum()
        bad += int((d[sel] > 4).sum())
    return na("no glyph faces") if not tot else ok(bad / tot <= 0.01, f"{bad} of {tot} hue samples step more than 4°", bad)


@check("T-D05", scope="image")
@gate3d
def shading_creases(post, sl):
    from scipy import ndimage as ndi
    L = lstar(sl.final).astype(np.float32)
    med = ndi.median_filter(L, size=7)
    bad = []
    for gl in glyphs(sl):
        side = visible(sl, gl["parts"], 2)
        if side is None or side.sum() < 200:
            continue
        out = side & (np.abs(L - med) > 25)
        lab, k = ndi.label(out)
        sizes = ndi.sum(out, lab, range(1, k + 1)) if k else []
        big = [s for s in sizes if s > 20]
        if big:
            bad.append(f"'{gl['char']}' {len(big)} crease clusters")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-D07", scope="image")
@gate3d
def face_reads(post, sl):
    from scipy import ndimage as ndi
    bad, n = [], 0
    for gl in glyphs(sl):
        cap = visible(sl, gl["parts"], 1)
        side = visible(sl, gl["parts"], 2)
        m = visible(sl, gl["parts"])
        if cap is None or cap.sum() < 300:
            continue
        n += 1
        ring = ndi.binary_dilation(m, iterations=25) & ~ndi.binary_dilation(m, iterations=5)
        if sl.text_mask is not None:
            ring &= ~sl.text_mask
        a = sl.id_map
        ring &= a[..., 2] == 0
        if ring.sum() < 50:
            continue
        lc = abs(apca(float(np.median(apca_y(sl.final[cap]))), float(np.median(apca_y(sl.final[ring])))))
        dl = abs(float(np.median(lstar(sl.final[cap]))) - float(np.median(lstar(sl.final[side])))) if side.sum() > 50 else 99
        if lc < 60 or dl < 8:
            bad.append(f"'{gl['char']}' Lc {lc:.0f}, dL* to walls {dl:.0f}")
    return na("no glyph faces") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-D09", scope="image")
@gate3d
def pose(post, sl):
    bad = []
    pos, R, c = cam_matrices(sl)
    for o in sl.objects():
        if o.get("role") not in ("numeral", "glyph") or not o.get("matrix"):
            continue
        M = np.array(o["matrix"], float).reshape(4, 4).T
        fwd = M[:3, 2] / np.linalg.norm(M[:3, 2])
        up = M[:3, 1] / np.linalg.norm(M[:3, 1])
        ctr = np.mean(np.array(o["world_box"], float), 0)
        to_cam = pos - ctr
        to_cam /= np.linalg.norm(to_cam)
        yaw = math.degrees(math.atan2(np.cross(to_cam, fwd)[1], to_cam @ fwd))
        pitch = math.degrees(math.asin(max(-1, min(1, float(fwd[1] - to_cam[1])))))
        up_v = up @ R
        roll = math.degrees(math.atan2(up_v[0], up_v[1]))
        if abs(yaw) > 30 or abs(pitch) > 20 or abs(roll) > 1:
            bad.append(f"{o.get('text')}: yaw {yaw:.0f}°, pitch {pitch:.0f}°, roll {roll:.1f}°")
    return ok(not bad, "; ".join(sorted(set(bad))[:4])) if any(o.get("role") in ("numeral", "glyph") for o in sl.objects()) else na("no glyphs")


@check("T-D10", scope="image")
@gate3d
def highlight_erase(post, sl):
    from scipy import ndimage as ndi
    L = lstar(sl.final)
    bad, n = [], 0
    for gl in glyphs(sl):
        cap = visible(sl, gl["parts"], 1)
        if cap is None or cap.sum() < 300:
            continue
        n += 1
        hl = cap & (L >= 95)
        share = hl.sum() / cap.sum()
        holes = ndi.binary_fill_holes(cap) & ~cap
        bridge = (ndi.binary_dilation(hl, iterations=2) & holes).any() if holes.any() else False
        if share > 0.15 or bridge:
            bad.append(f"'{gl['char']}' highlight {share:.0%}" + (", touches a counter" if bridge else ""))
    return na("no glyph faces") if not n else ok(not bad, "; ".join(bad[:4]))


@check("V-D13", scope="image")
@gate3d
def specular_clip(post, sl):
    m = obj_mask(sl)
    white = (sl.final.min(2) >= 254) & m
    share = white.sum() / max(1, m.sum())
    return ok(share <= 0.01, f"{share:.2%} of 3D pixels pure white", round(float(share), 4))


@check("R-K15", scope="image")
def iridescence_on_meaning(post, sl):
    lab = to_lab(sl.final[::2, ::2])
    c = np.hypot(lab[..., 1], lab[..., 2])
    h = np.arctan2(lab[..., 2], lab[..., 1])
    hs = np.abs(cv2.Sobel(np.cos(h), cv2.CV_64F, 1, 0)) + np.abs(cv2.Sobel(np.sin(h), cv2.CV_64F, 1, 0))
    irid = (c > 45) & (hs > 0.15)
    if irid.sum() < 200:
        return na("no iridescent areas")
    if sl.id_map is None:
        return Outcome(NEEDS, "needs passes: id") if has3d(sl) is not None else na("no 3D hero")
    m = sl.id_map[::2, ::2, 2] > 0
    share = (irid & m).sum() / irid.sum()
    return ok(share >= 0.9, f"{share:.0%} of iridescent pixels on numerals", round(float(share), 3))


# ------------------------------------------------------------------ calibration: the V-I07 gates apply from Nov 11, 2026
# These gates catch the 3D artifacts the founder rejected on Nov 11 (V-I07: seams, bevels that cut through a glyph,
# glyphs that interpenetrate, z-fighting, glass over a glyph, the shadow-camera line). The approved Nov 4 deck was made
# with the scene code that had the same shadow camera and bevels (fixed on Nov 11 in 8369ffb and 213cd9f), so these
# gates fail it too; as weighted checks they would no longer stop the rejected renders either. They apply to posts dated
# Nov 11, 2026 or later, like R-D11 (calibration, ruling 4); on earlier posts they are not scored.
V_I07_FROM = NUMERAL_RULE_FROM
V_I07_GATES = ("R-D01", "R-N01", "R-Z06", "R-Z03", "R-Z04", "R-S01", "R-S02", "R-G01", "R-G02")


def _from_nov11(fn):
    def wrap(post, sl):
        if (post.date or "9999") < V_I07_FROM:
            return na(f"applies to posts dated {V_I07_FROM} or later (V-I07 lessons; the Nov 4 deck predates them)")
        return fn(post, sl)
    wrap.__name__ = fn.__name__
    return wrap


def _scope_v_i07():
    from . import REGISTRY
    for cid in V_I07_GATES:
        REGISTRY[cid] = [(_from_nov11(fn), scope) for fn, scope in REGISTRY.get(cid, [])]


_scope_v_i07()
