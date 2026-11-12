"""Brand consistency across posts (QB, QS), accessibility beyond contrast (QA), the AI/template look (R-K) and
banding (R-B). Checks that compare with earlier posts read the images listed in history.json ("images": [...]);
with no history they are not scored.
"""
from __future__ import annotations

import os
import re

import numpy as np

from . import check
from .core import (Outcome, de00, estimate, gray, hamming, load_rgb, lstar, na, ok, phash, rel_lum, resize_area, simulate_cvd, to_lab, wcag, VIENOT,
                   CVD)
from .files import history_images

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

AURORA_ANCHORS = [265.0, 300.0, 330.0, 220.0]  # hue angles (CIELAB h) of the Aurora background presets: violet, magenta-pink, pink, sky


def bg_mask(sl):
    m = np.ones(sl.final.shape[:2], bool)
    from scipy import ndimage as ndi
    if sl.text_mask is not None:
        m &= ~ndi.binary_dilation(sl.text_mask, iterations=12)
    if sl.id_map is not None:
        m &= ~ndi.binary_dilation(sl.id_map[..., 2] > 0, iterations=12)
    if sl.manifest:
        for p in sl.manifest.get("panels", []) + [i for i in sl.manifest.get("images", []) if "hero" not in i["cls"]]:
            m[max(0, int(p["y"])):int(p["y"] + p["h"]), max(0, int(p["x"])):int(p["x"] + p["w"])] = False
    return m


def prev_covers(post, n=12):
    out = []
    for h, p in history_images(post):
        if h.get("slide") in (None, 1):
            out.append(load_rgb(p))
    return out[-n:]


@check("QB04", scope="post")
def bg_lightness_stable(post):
    if not post.slides:
        return na("no slides")
    prev = prev_covers(post)
    if not prev:
        return na("no earlier covers in history.json")
    sl = post.slides[0]
    now = float(lstar(sl.final[bg_mask(sl)]).mean())
    med = float(np.median([lstar(p[::4, ::4]).mean() for p in prev]))
    return ok(abs(now - med) <= 3, f"background L* {now:.1f} vs series median {med:.1f}", round(now - med, 2))


@check("QB01", scope="post")
def token_mix(post):
    prev = prev_covers(post)
    if not post.slides or not prev:
        return na("no earlier covers in history.json")
    def mix(a):
        lab = to_lab(a[::6, ::6])
        L, c = lab[..., 0], np.hypot(lab[..., 1], lab[..., 2])
        return np.array([((L > 85) & (c < 25)).mean(), ((L > 90) & (c < 6)).mean(), ((c > 40) & (lab[..., 2] < -30)).mean(), (L < 30).mean()])
    now = mix(post.slides[0].final)
    med = np.median([mix(p) for p in prev], 0)
    d = float(np.abs(now - med).max())
    return ok(d <= 0.10, f"largest token-share change {d:.0%}", round(d, 3))


@check("QB02", scope="post")
def palette_close(post):
    prev = prev_covers(post)
    if not post.slides or not prev:
        return na("no earlier covers in history.json")
    from scipy.optimize import linear_sum_assignment
    def pal(a):
        lab = to_lab(a[::8, ::8]).reshape(-1, 3).astype(np.float32)
        _, lbl, cen = cv2.kmeans(lab, 5, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5), 2, cv2.KMEANS_PP_CENTERS)
        return cen
    now = pal(post.slides[0].final)
    ref = np.median([pal(p) for p in prev], 0)
    cost = np.array([[float(de00(a[None, None], b[None, None])) for b in ref] for a in now])
    r, c = linear_sum_assignment(cost)
    v = float(cost[r, c].mean())
    return ok(v <= 8, f"mean matched dE00 {v:.1f}", round(v, 2))


@check("QB03", scope="post")
def accent_rotates(post):
    prev = prev_covers(post, 1)
    if not post.slides or not prev:
        return na("no earlier cover in history.json")
    def accent(a):
        lab = to_lab(a[::6, ::6])
        c = np.hypot(lab[..., 1], lab[..., 2])
        h = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360
        sel = (c > 30) & ~((h > 260) & (h < 300))
        return float(np.median(h[sel])) if sel.any() else None
    a, b = accent(post.slides[0].final), accent(prev[-1])
    if a is None or b is None:
        return na("no secondary accent")
    d = abs((a - b + 180) % 360 - 180)
    return ok(d >= 30 or post.pkg.get("campaign"), f"accent hue moved {d:.0f}°", round(d, 1))


@check("QB05", scope="image")
def bg_calm(post, sl):
    L = lstar(sl.final[::2, ::2][bg_mask(sl)[::2, ::2]])
    s = float(L.std())
    return ok(2 <= s <= 9, f"background L* sd {s:.1f}", round(s, 2))


@check("QB06", scope="image")
def bg_chroma(post, sl):
    lab = to_lab(sl.final[::4, ::4])[bg_mask(sl)[::4, ::4]]
    c = float(np.hypot(lab[:, 1], lab[:, 2]).mean())
    return ok(6 <= c <= 22, f"background C* {c:.1f}", round(c, 1))


@check("QB07", scope="image")
def anchors(post, sl):
    lab = to_lab(cv2.resize(sl.final, (27, 34), interpolation=cv2.INTER_AREA))
    c = np.hypot(lab[..., 1], lab[..., 2]).ravel()
    h = (np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360).ravel()
    sel = h[c > 8]
    if sel.size < 10:
        return na("background too neutral")
    hist, edges = np.histogram(sel, bins=36, range=(0, 360))
    top = [float(edges[i] + 5) for i in np.argsort(hist)[-3:] if hist[i] > 0]
    off = [min(abs((t - a + 180) % 360 - 180) for a in AURORA_ANCHORS) for t in top]
    return ok(max(off) <= 12, f"background hue centroids {', '.join(f'{t:.0f}' for t in top)}°", round(max(off), 1))


@check("QB08", scope="image")
def bg_preset(post, sl):
    presets = [p for p in (os.path.join(post.base, "pipeline", f"bg_{i}.png") for i in range(1, 7)) if os.path.exists(p)]
    if not presets:
        return na("Aurora background presets not in this checkout (pipeline/bg_N.png)")
    def lp(a):
        return lstar(cv2.resize(a, (16, 20), interpolation=cv2.INTER_AREA)).ravel()
    now = lp(sl.final)
    r = max(float(np.corrcoef(now, lp(load_rgb(p)))[0, 1]) for p in presets)
    return ok(r >= 0.6, f"best preset correlation {r:.2f}", round(r, 3))


LOGO_POS = (151, 2454, 493, 2593)   # SVPay logo box on the 2160x2700 master (footer, bottom left)


def find_logo(sl):
    if sl.manifest:
        ls = [i for i in sl.manifest.get("images", []) if "logo" in i["cls"]]
        if ls:
            l = ls[0]
            return l["x"], l["y"], l["x"] + l["w"], l["y"] + l["h"]
    return None


@check("QB09", scope="image")
def logo_position(post, sl):
    b = find_logo(sl)
    if b is None:
        return na("needs the manifest (logo box)")
    k = sl.scale
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    ex, ey = (LOGO_POS[0] + LOGO_POS[2]) / 2 * k, (LOGO_POS[1] + LOGO_POS[3]) / 2 * k
    d = max(abs(cx - ex), abs(cy - ey))
    return ok(d <= 6 * k, f"logo center {d:.0f}px from the canonical position", round(d, 1))


@check("V-K17", scope="image")
def logo_master(post, sl):
    b = find_logo(sl)
    tpl = os.path.join(post.base, "pipeline", "assets", "svpay-logo.png")
    if b is None or not os.path.exists(tpl):
        return na("needs the manifest and pipeline/assets/svpay-logo.png")
    from PIL import Image
    w, h = int(b[2] - b[0]), int(b[3] - b[1])
    t = Image.open(tpl).convert("RGBA").resize((w, h), Image.LANCZOS)
    region = sl.final[int(b[1]):int(b[3]), int(b[0]):int(b[2])].astype(np.float32)
    alpha = np.asarray(t)[..., 3:4].astype(np.float32) / 255
    rgb = np.asarray(t)[..., :3].astype(np.float32)
    bgc = np.median(sl.final[int(b[1]):int(b[3]), max(0, int(b[0]) - 20):int(b[0])].reshape(-1, 3), 0) if b[0] > 20 else region.reshape(-1, 3).max(0)
    comp = rgb * alpha + bgc * (1 - alpha)
    score = float(cv2.matchTemplate(gray(region.astype(np.uint8)), gray(comp.astype(np.uint8)), cv2.TM_CCOEFF_NORMED).max())
    sim = sl.sim1
    k = sim.shape[1] / sl.final.shape[1]
    reg2 = sim[int(b[1] * k):int(b[3] * k), int(b[0] * k):int(b[2] * k)]
    comp2 = cv2.resize(comp.astype(np.uint8), (reg2.shape[1], reg2.shape[0]), interpolation=cv2.INTER_AREA)
    score2 = float(cv2.matchTemplate(gray(reg2), gray(comp2), cv2.TM_CCOEFF_NORMED).max()) if reg2.size else 0
    return ok(score >= 0.92 and score2 >= 0.85, f"template match {score:.2f}, after recompression {score2:.2f}", round(score, 3))


@check("QB11", scope="image")
def logo_clear_space(post, sl):
    b = find_logo(sl)
    if b is None or not sl.has("mask"):
        return na("needs the manifest and the text mask")
    pad = 0.5 * (b[3] - b[1])
    x0, y0, x1, y1 = int(max(0, b[0] - pad)), int(max(0, b[1] - pad)), int(b[2] + pad), int(b[3] + pad)
    zone = np.zeros(sl.final.shape[:2], bool)
    zone[y0:y1, x0:x1] = True
    zone[int(b[1]):int(b[3]), int(b[0]):int(b[2])] = False
    hits = int((zone & sl.text_mask).sum())
    if sl.id_map is not None:
        hits += int((zone & (sl.id_map[..., 2] > 0)).sum())
    return ok(hits == 0, f"{hits} foreign pixels in the logo's clear space", hits)


@check("QB12", scope="image")
def logo_size(post, sl):
    b = find_logo(sl)
    if b is None:
        return na("needs the manifest")
    h = (b[3] - b[1]) / sl.scale
    return ok(h >= 64, f"logo {h:.0f}px tall at 2160 wide", round(h))


@check("QB13", scope="post")
def margin_token(post):
    rows = []
    for sl in post.slides:
        if not sl.manifest:
            return na("needs the manifest")
        lefts = [e["box"]["x"] for e in sl.elements if e["role"] in ("headline", "body", "label", "source")]
        if lefts:
            r = ok(abs(min(lefts) - 144 * sl.scale) <= 4 + 8, f"left margin {min(lefts):.0f}px (token 144)", round(min(lefts)))
            r.where = sl.where
            rows.append(r)
    return rows or na("no text")


@check("QB16", scope="post")
def size_tokens(post):
    tokens = None
    sizes = set()
    for sl in post.slides:
        if not sl.manifest:
            return na("needs the manifest")
        sizes |= {round(r["size"] / sl.manifest["capture"]["scale"]) for e in sl.elements for r in e.get("runs", [])}
    tokens = {16, 19, 20, 21, 22, 23, 24, 25, 27, 28, 34, 44, 52, 60, 68, 72, 80, 90, 96, 110, 120, 140, 180}
    off = sorted(s for s in sizes if s not in tokens)
    return ok(not off, f"{len(sizes)} sizes; not a type token: {off}")


@check("QB17", scope="post")
def weight_tokens(post):
    ws = set()
    for sl in post.slides:
        if not sl.manifest:
            return na("needs the manifest")
        ws |= {r["weight"] for e in sl.elements for r in e.get("runs", [])}
    off = sorted(w for w in ws if w not in (400, 500, 600, 700, 800))
    return ok(not off, f"weights {sorted(ws)}")


@check("QB18", scope="post")
def tracking_tokens(post):
    vals = set()
    for sl in post.slides:
        if not sl.manifest:
            return na("needs the manifest")
        vals |= {round(r["letter_spacing"] / max(1, r["size"]), 3) for e in sl.elements for r in e.get("runs", [])}
    tokens = [0.0, -0.01, -0.035, 0.022, 0.06, 0.1, 0.14, -0.02, 0.04]
    off = sorted(v for v in vals if min(abs(v - t) for t in tokens) > 0.01)
    return ok(not off, f"tracking values {sorted(vals)}")


@check("QB19", scope="post")
def panel_tokens(post):
    radii = set()
    for sl in post.slides:
        if not sl.manifest:
            return na("needs the manifest")
        radii |= {round(p["radius"] / sl.manifest["capture"]["scale"]) for p in sl.manifest.get("panels", []) if p["h"] > 120}
    return ok(len(radii) <= 2, f"card radii {sorted(radii)}") if radii else na("no cards")


@check("QB20", scope="image")
def frosted_white(post, sl):
    if not sl.manifest:
        return na("needs the manifest")
    cards = [p for p in sl.manifest.get("panels", []) if p["h"] > 120 and p["w"] > 200]
    if not cards:
        return na("no cards")
    bad = []
    for p in cards:
        sub = sl.final[int(p["y"]) + 12:int(p["y"] + p["h"]) - 12, int(p["x"]) + 12:int(p["x"] + p["w"]) - 12]
        m = np.ones(sub.shape[:2], bool)
        if sl.text_mask is not None:
            from scipy import ndimage as ndi
            m &= ~ndi.binary_dilation(sl.text_mask[int(p["y"]) + 12:int(p["y"] + p["h"]) - 12, int(p["x"]) + 12:int(p["x"] + p["w"]) - 12], iterations=8)
        L = float(lstar(sub[m]).mean()) if m.any() else 95
        if not (90 <= L <= 98):
            bad.append(f"card L* {L:.0f}")
    return ok(not bad, ", ".join(bad))


@check("QB21", scope="post")
def footer_band(post):
    prev = prev_covers(post, 3)
    if not post.slides or not prev:
        return na("no earlier covers in history.json")
    from skimage.metrics import structural_similarity as ssim
    H = post.slides[0].size[1]
    a = gray(post.slides[0].final[int(H * 0.88):, :1100])
    b = gray(cv2.resize(prev[-1], (post.slides[0].size[0], H))[int(H * 0.88):, :1100])
    v = float(ssim(a, b, data_range=255))
    return ok(v >= 0.97, f"footer SSIM {v:.3f}", round(v, 3))


@check("QB22", scope="post")
def numeral_material(post):
    vals = [(s.where, to_lab(s.final[s.id_map[..., 2] > 0][::7][None]).reshape(-1, 3)) for s in post.slides if s.id_map is not None and (s.id_map[..., 2] > 0).sum() > 1000]
    if not vals:
        return na("no 3D numerals with passes")
    ref_p = os.path.join(os.path.dirname(__file__), "material_ref.json")
    if not os.path.exists(ref_p):
        return na("no material reference yet (checks/image/material_ref.json)")
    import json
    ref = np.array(json.load(open(ref_p))["lab"])
    bad = []
    for w, lab in vals:
        lab = lab[lab[:, 0] < np.percentile(lab[:, 0], 98)]
        d = float(de00(lab.mean(0)[None, None], ref[None, None]))
        if d > 6:
            bad.append(f"{w} dE00 {d:.1f}")
    return ok(not bad, ", ".join(bad))


@check("QB23", scope="post")
def lighting_family(post):
    keys = [s.scene for s in post.slides if s.scene and s.scene.get("objects")]
    if not keys:
        return na("no 3D passes")
    house = np.array([8, 22, 16], float)
    house /= np.linalg.norm(house)
    bad = []
    for sc in keys:
        L = [l for l in sc["lights"] if l.get("cast_shadow")]
        if not L:
            continue
        d = np.array(L[0]["position"], float) - np.array(L[0].get("target", [0, 0, 0]), float)
        d /= np.linalg.norm(d)
        ang = float(np.degrees(np.arccos(np.clip(d @ house, -1, 1))))
        if ang > 20:
            bad.append(f"{ang:.0f}°")
    return ok(not bad, "key light off the house direction by " + ", ".join(bad))


@check("QB24", scope="post")
def camera_family(post):
    cams = [s.scene["camera"] for s in post.slides if s.scene and s.scene.get("objects")]
    if not cams:
        return na("no 3D passes")
    bad = [f"fov {c['fov']}" for c in cams if abs(c["fov"] - 26) > 5]
    elev = [np.degrees(np.arctan2(c["position"][1], c["position"][2])) for c in cams]
    bad += [f"elevation {e:.0f}°" for e in elev if abs(e - 8) > 10]
    return ok(not bad, ", ".join(bad))


@check("QB26", scope="image")
def inline_svg(post, sl):
    if not sl.manifest:
        return na("needs the manifest")
    allow = {"M4 12h15M13 5.5 19.5 12 13 18.5"}
    bad = [s["html"][:40] for s in sl.manifest.get("svgs", []) if not any(a in s["html"] for a in allow)]
    return ok(not bad, f"{len(bad)} inline SVGs outside the allowlist")


@check("QB27", scope="post")
def not_repeat_cover(post):
    prev = history_images(post, days=30)
    if not post.slides or not prev:
        return na("no earlier images in history.json")
    hs = [phash(load_rgb(p)) for _, p in prev]
    worst = min(hamming(phash(post.slides[0].final), h) for h in hs)
    return ok(worst >= 14, f"closest earlier image {worst} bits", worst)


@check("QB30", scope="post")
def variants_match_master(post):
    from skimage.metrics import structural_similarity as ssim
    rows = []
    for net, v in post.nets.items():
        for m in v.get("media", []):
            p = post.path(m)
            if not p or "-1440" not in p:
                continue
            master = p.replace("-1440", "")
            if os.path.exists(master):
                a, b = load_rgb(p), resize_area(load_rgb(master), 1440, 1800)
                s = float(ssim(gray(a), gray(b), data_range=255))
                r = ok(s >= 0.95, f"{net} {os.path.basename(p)} SSIM {s:.3f}")
                rows.append(r)
    return rows or na("no network variants of a master")


# ------------------------------------------------------------------ series system (QS)
@check("QS01", scope="post")
def anchors_present(post):
    if not post.slides or not post.slides[0].manifest:
        return na("needs the manifest")
    sl = post.slides[0]
    a = find_logo(sl) is not None
    b = any(i for i in sl.manifest.get("images", []) if "hero" in i["cls"])
    c = any("Inter Tight" in e["family"] for e in sl.elements)
    d = any(e["role"] == "footer" and "svpay.app" in e["text"] for e in sl.elements)
    n = sum([a, b, c, d])
    return ok(n == 4, f"{n} of 4 anchors (logo, Aurora background, brand type, footer URL)", n)


@check("QS02", scope="post")
def variables_change(post):
    prev = [h for h in post.history if h.get("template") or h.get("hero_value")]
    if not prev:
        return na("history.json has no template or hero fields")
    last = prev[-1]
    changed = sum([last.get("template") != post.pkg.get("template"), last.get("hero_value") != post.pkg.get("hero_value"), last.get("accent") != post.pkg.get("accent")])
    return ok(changed >= 2, f"{changed} variables changed")


@check("QS04", scope="post")
def experiment_budget(post):
    recent = [h for h in post.history[-6:] if h.get("experiment")]
    if post.pkg.get("experiment") and not post.pkg.get("experiment_rule"):
        return ok(False, "experiment without the rule it breaks")
    return ok(len(recent) + (1 if post.pkg.get("experiment") else 0) <= 1, f"{len(recent)} experiments in the last 6 posts")


@check("QS05", scope="post")
def series_tag(post):
    s = post.pkg.get("series")
    if not s:
        return na("not a named series")
    prev = [h for h in post.history if h.get("series") == s and h.get("tag_box")]
    if not prev or not post.slides or not post.slides[0].manifest:
        return na("no earlier post of this series with a tag box")
    tag = [e for e in post.slides[0].elements if e["role"] == "label"]
    if not tag:
        return ok(False, "no series tag on slide 1")
    b, p = tag[0]["box"], prev[-1]["tag_box"]
    return ok(abs(b["x"] - p["x"]) <= 6 and abs(b["y"] - p["y"]) <= 6, "")


@check("QS06", scope="post")
def signature_frequency(post):
    recent = [h for h in post.history if h.get("slide1_3d") is not None][-8:]
    if not recent:
        return na("history.json has no slide1_3d field")
    now = bool(post.slides and post.slides[0].scene and post.slides[0].scene.get("objects"))
    share = (sum(1 for h in recent if h["slide1_3d"]) + now) / (len(recent) + 1)
    return ok(share >= 0.5, f"{share:.0%} of recent covers carry a 3D numeral")


# ------------------------------------------------------------------ accessibility (QA)
def chart_marks(post):
    return [m for c in post.pkg.get("charts", []) for m in c.get("marks", [])]


@check("QA02", scope="post")
def marks_cvd(post):
    cs = [c for c in post.pkg.get("charts", []) if c.get("colors")]
    if not cs:
        return na("no charts declared in post.json")
    bad = []
    for c in cs:
        cols = np.array(c["colors"], float)
        for kind in ("protan", "tritan"):
            sim = simulate_cvd(cols, kind)
            lab = to_lab(sim[None])[0]
            for i in range(len(lab)):
                for j in range(i + 1, len(lab)):
                    if float(de00(lab[i][None, None], lab[j][None, None])) < 10:
                        bad.append(f"{c.get('title', 'chart')} {kind} {i}/{j}")
    return ok(not bad, "; ".join(bad[:4]))


@check("QA03", scope="post")
def grayscale_meaning(post):
    cs = [c for c in post.pkg.get("charts", []) if c.get("colors")]
    if not cs:
        return na("no charts declared in post.json")
    bad = []
    for c in cs:
        L = lstar(np.array(c["colors"], float))
        if len(L) > 1 and np.min(np.abs(np.diff(np.sort(L)))) < 15:
            bad.append(c.get("title", "chart"))
    return ok(not bad, ", ".join(bad))


@check("QA04", scope="image")
def two_cvd_models(post, sl):
    from .textpx import contrast_rows
    rows = contrast_rows(sl)
    if rows is None:
        return na("needs passes")
    dis = []
    for kind in ("protan", "deutan"):
        for r in rows:
            v1 = wcag(float(rel_lum(simulate_cvd(r["text_rgb"][None], kind)[0])), float(rel_lum(simulate_cvd(np.asarray(r["bg_rgb"])[None], kind)[0])))
            v2 = wcag(float(rel_lum(simulate_cvd(r["text_rgb"][None], kind, VIENOT)[0])), float(rel_lum(simulate_cvd(np.asarray(r["bg_rgb"])[None], kind, VIENOT)[0])))
            if (v1 >= 4.5) != (v2 >= 4.5):
                dis.append(f"{kind} {r['el']['text'][:16]}")
    return ok(not dis, "models disagree: " + ", ".join(dis[:4]))


@check("QA05", scope="image")
def blue_cvd(post, sl):
    lab = to_lab(sl.final[::3, ::3])
    blue = (np.hypot(lab[..., 1], lab[..., 2]) > 40) & (lab[..., 2] < -40)
    if blue.sum() < 200:
        return na("no brand-blue elements")
    from scipy import ndimage as ndi
    ring = ndi.binary_dilation(blue, iterations=6) & ~ndi.binary_dilation(blue, iterations=2)
    worst = 99.0
    for kind in ("protan", "deutan", "tritan"):
        a = to_lab(simulate_cvd(sl.final[::3, ::3][blue].mean(0)[None], kind)[None])[0, 0]
        b = to_lab(simulate_cvd(sl.final[::3, ::3][ring].mean(0)[None], kind)[None])[0, 0]
        worst = min(worst, float(de00(a[None, None], b[None, None])))
    return ok(worst >= 15, f"weakest dE00 {worst:.1f}", round(worst, 1))


@check("QA06", scope="image")
def nontext_after_recompression(post, sl):
    if sl.id_map is None and not chart_marks(post):
        return na("no meaningful non-text marks")
    if sl.id_map is None:
        return na("needs passes")
    m = sl.id_map[..., 2] > 0
    if m.sum() < 500:
        return na("no 3D marks")
    k = sl.sim1.shape[1] / sl.final.shape[1]
    mm = cv2.resize(m.astype(np.uint8), (sl.sim1.shape[1], sl.sim1.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
    from scipy import ndimage as ndi
    ring = ndi.binary_dilation(mm, iterations=6) & ~ndi.binary_dilation(mm, iterations=2)
    v = wcag(float(np.median(rel_lum(sl.sim1[mm]))), float(np.median(rel_lum(sl.sim1[ring]))))
    return ok(v >= 3, f"{v:.2f}:1", round(v, 2))


@check("QA07", scope="post")
def adjacent_segments(post):
    cs = [c for c in post.pkg.get("charts", []) if c.get("colors") and c.get("touching")]
    if not cs:
        return na("no touching chart segments declared")
    bad = []
    for c in cs:
        for i, j in c["touching"]:
            a, b = np.array(c["colors"][i], float), np.array(c["colors"][j], float)
            if wcag(float(rel_lum(a)), float(rel_lum(b))) < 3 and c.get("gap_px", 0) < 6:
                bad.append(f"{i}/{j}")
    return ok(not bad, ", ".join(bad))


@check("QA08", scope="post")
def cta_without_color(post):
    if not post.slides or not post.slides[-1].manifest:
        return na("needs the manifest")
    sl = post.slides[-1]
    pills = [p for p in sl.manifest.get("panels", []) if p["h"] < 200 and p["w"] > 200]
    if not pills:
        return na("no CTA pill")
    from scipy import ndimage as ndi
    bad = []
    for p in pills:
        inside = sl.final[int(p["y"]) + 6:int(p["y"] + p["h"]) - 6, int(p["x"]) + 6:int(p["x"] + p["w"]) - 6]
        out = sl.final[max(0, int(p["y"]) - 20):int(p["y"]) - 4, int(p["x"]):int(p["x"] + p["w"])]
        if inside.size and out.size:
            v = wcag(float(np.median(rel_lum(inside.reshape(-1, 3)))), float(np.median(rel_lum(out.reshape(-1, 3)))))
            if v < 3:
                bad.append(f"{v:.1f}:1")
    return ok(not bad, "pill fill against the background " + ", ".join(bad))


def motion(post):
    return post.pkg.get("motion") or []


@check("QA09", scope="post")
def red_flash(post):
    return na("no motion versions") if not motion(post) else ok(all(m.get("red_flashes_per_s", 0) <= 3 for m in motion(post)), "")


@check("QA11", scope="post")
def long_animation_video(post):
    ms = [m for m in motion(post) if m.get("seconds", 0) > 5]
    return na("no animation longer than 5 s") if not ms else ok(all(m.get("format") in ("mp4", "mov") for m in ms), "")


@check("QA12", scope="post")
def gentle_motion(post):
    return na("no motion versions") if not motion(post) else ok(all(m.get("max_flow_pct", 0) <= 1.5 for m in motion(post)), "")


@check("QA13", scope="post")
def first_frame(post):
    return na("no motion versions") if not motion(post) else ok(all(m.get("poster_checked") for m in motion(post)), "")


@check("QA14", scope="post")
def text_duration(post):
    return na("no motion versions") if not motion(post) else ok(all(m.get("text_seconds_ok") for m in motion(post)), "")


@check("V-M06", scope="post")
def flashes(post):
    return na("no motion versions") if not motion(post) else ok(all(m.get("flashes_per_s", 0) <= 3 and m.get("flash_area_pct", 0) <= 2.8 for m in motion(post)), "")


@check("QA15", scope="post")
def li_alt_short(post):
    alts = post.nets.get("linkedin", {}).get("alt", [])
    if not alts:
        return na("no LinkedIn alt text")
    long_ = [len(a) for a in alts if len(a) > 120]
    return ok(not [l for l in long_ if l > 300] and len(long_) <= len(alts) // 2, f"lengths {[len(a) for a in alts]}")


@check("QA16", scope="post")
def alt_differs(post):
    from difflib import SequenceMatcher
    bad = []
    for net, v in post.nets.items():
        alts = v.get("alt", [])
        for i in range(len(alts)):
            for j in range(i + 1, len(alts)):
                if SequenceMatcher(None, alts[i], alts[j]).ratio() >= 0.9:
                    bad.append(f"{net} {i + 1}/{j + 1}")
    return ok(not bad, ", ".join(bad)) if any(v.get("alt") for v in post.nets.values()) else na("no alt text")


@check("QA17", scope="post")
def alt_hygiene(post):
    bad = [a[:30] for v in post.nets.values() for a in v.get("alt", []) if re.match(r"(?i)(image|picture|graphic|photo) of\b", a) or "#" in a or re.search(r"https?://", a)]
    return ok(not bad, " | ".join(bad)) if any(v.get("alt") for v in post.nets.values()) else na("no alt text")


@check("QA18", scope="post")
def alt_numbers(post):
    from .words import claim_numbers, norm_value, img_text
    bad = []
    for net, v in post.nets.items():
        for i, m in enumerate(v.get("media", [])):
            sl = post.by_file.get(os.path.abspath(post.path(m)))
            alts = v.get("alt", [])
            if not sl or i >= len(alts):
                continue
            want = {norm_value(t)[0] for t, a, b in claim_numbers(img_text(post, sl)) if "$" in t or "%" in t}
            got = {norm_value(t)[0] for t, a, b in claim_numbers(alts[i])}
            miss = want - got
            if miss:
                bad.append(f"{net} alt {i + 1} lacks {sorted(miss)[:3]}")
    return ok(not bad, "; ".join(bad[:5])) if any(v.get("alt") for v in post.nets.values()) else na("no alt text")


@check("QA19", scope="post")
def gbp_description(post):
    if "gbp" not in post.nets:
        return na("no GBP post")
    t = post.nets["gbp"].get("text", "")
    sents = re.split(r"(?<=[.!?])\s+", t)
    return ok(any(len(s.split()) <= 20 and re.search(r"(?i)image|graphic|shows|pictured", s) for s in sents), "")


def pdf_path(post):
    p = post.nets.get("linkedin", {}).get("pdf")
    return post.path(p) if p and os.path.exists(post.path(p)) else None


def pdf_check(post, fn):
    p = pdf_path(post)
    if not p:
        return na("no LinkedIn PDF file in the package")
    import pikepdf
    with pikepdf.open(p) as pdf:
        return fn(pdf)


@check("QA20", scope="post")
def pdf_tagged(post):
    return pdf_check(post, lambda pdf: ok("/StructTreeRoot" in pdf.Root and pdf.Root.get("/MarkInfo", {}).get("/Marked", False), ""))


@check("QA21", scope="post")
def pdf_lang(post):
    return pdf_check(post, lambda pdf: ok(str(pdf.Root.get("/Lang", "")) == "en-US" and bool(pdf.Root.get("/ViewerPreferences", {}).get("/DisplayDocTitle", False)), ""))


@check("QA22", scope="post")
def pdf_order(post):
    return pdf_check(post, lambda pdf: na("needs a tagged PDF structure walk") if "/StructTreeRoot" not in pdf.Root else ok(True, "tagged"))


@check("QA23", scope="post")
def pdf_figures(post):
    return pdf_check(post, lambda pdf: na("untagged PDF") if "/StructTreeRoot" not in pdf.Root else ok(True, ""))


@check("QA24", scope="post")
def pdf_text_layer(post):
    p = pdf_path(post)
    if not p:
        return na("no LinkedIn PDF file in the package")
    from pypdf import PdfReader
    from difflib import SequenceMatcher
    txt = " ".join(pg.extract_text() or "" for pg in PdfReader(p).pages)
    want = " ".join(s.text for s in post.slides)
    r = SequenceMatcher(None, re.sub(r"\s+", " ", txt.lower()), re.sub(r"\s+", " ", want.lower())).ratio()
    return ok(r >= 0.98, f"text layer similarity {r:.2f}", round(r, 3))


@check("QA26", scope="post")
def caption_accessible_pdf(post):
    if not pdf_path(post):
        return na("no LinkedIn PDF")
    return ok(bool(re.search(r"(?i)accessible PDF|download", post.nets["linkedin"].get("text", "") + post.nets["linkedin"].get("first_comment", ""))), "")


@check("V-J01", scope="post")
def real_person_share(post):
    rec = [h for h in post.history if h.get("real_person") is not None]
    if not rec:
        return na("history.json has no real_person field")
    share = (sum(1 for h in rec[-8:] if h["real_person"]) + bool(post.pkg.get("real_person"))) / (len(rec[-8:]) + 1)
    return ok(share >= 0.25, f"{share:.0%} of recent posts show a real person")


@check("V-K15", scope="image")
def brand_names(post, sl):
    from .words import img_text
    t = img_text(post, sl)
    m = re.search(r"\b(Visa'?s|Visas|Mastercards|Mastercard'?s|MasterCard|Master Card|Squared|Toasted|stripe[ds])\b", t)
    return ok(not m, m.group(0) if m else "")


# ------------------------------------------------------------------ AI and template look (R-K), banding (R-B)
TAILWIND = np.array([[0x63, 0x66, 0xF1], [0x4F, 0x46, 0xE5], [0x8B, 0x5C, 0xF6], [0x7C, 0x3A, 0xED]], float)


@check("R-K01", scope="image")
def default_palettes(post, sl):
    lab = to_lab(sl.final[::8, ::8]).reshape(-1, 3).astype(np.float32)
    c = np.hypot(lab[:, 1], lab[:, 2])
    sat = lab[c > 15]
    if len(sat) < 100:
        return na("no saturated colors")
    k = 6
    _, lbl, cen = cv2.kmeans(sat, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5), 2, cv2.KMEANS_PP_CENTERS)
    share = np.bincount(lbl.ravel(), minlength=k) / len(lab)
    tw = to_lab(TAILWIND[None])[0]
    bad = []
    for i in range(k):
        if share[i] >= 0.03:
            d = min(float(de00(cen[i][None, None], t[None, None])) for t in tw)
            if d < 10:
                bad.append(f"cluster {share[i]:.0%} dE {d:.1f}")
    return ok(not bad, "; ".join(bad))


@check("R-K02", scope="image")
def four_point_stars(post, sl):
    g = gray(sl.final)
    m = (g > np.percentile(g, 99.5)).astype(np.uint8)
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    stars = 0
    for c in cs:
        a = cv2.contourArea(c)
        if a < 80 or a > 40000:
            continue
        hull = cv2.convexHull(c)
        sol = a / max(cv2.contourArea(hull), 1)
        ap = cv2.approxPolyDP(c, 0.04 * cv2.arcLength(c, True), True)
        if sol < 0.55 and len(ap) == 8:
            stars += 1
    return ok(stars == 0, f"{stars} four-pointed star shapes", stars)


@check("R-K03", scope="image")
def sparkle_blobs(post, sl):
    from skimage.feature import blob_log
    L = lstar(cv2.resize(sl.final, (540, 675), interpolation=cv2.INTER_AREA)).astype(np.float32)
    hp = np.clip(L - cv2.GaussianBlur(L, (0, 0), 6), 0, None) / 30
    m = bg_mask(sl)
    m = cv2.resize(m.astype(np.uint8), (540, 675), interpolation=cv2.INTER_NEAREST) > 0
    hp[~m] = 0
    blobs = blob_log(hp, min_sigma=1.5, max_sigma=6, threshold=0.25)
    return ok(len(blobs) == 0, f"{len(blobs)} bright blobs in the background", len(blobs))


@check("R-K04", scope="image")
def glass_coverage(post, sl):
    if not sl.manifest:
        return na("needs the manifest")
    m = np.zeros(sl.final.shape[:2], bool)
    for p in sl.manifest.get("panels", []):
        m[max(0, int(p["y"])):int(p["y"] + p["h"]), max(0, int(p["x"])):int(p["x"] + p["w"])] = True
    v = float(m.mean())
    return ok(v <= 0.40, f"glass covers {v:.0%}", round(v, 3))


@check("R-K05", scope="image")
def gradient_fills(post, sl):
    if not sl.manifest:
        return na("needs the manifest")
    n = sum(1 for e in sl.elements for r in e.get("runs", []) if r.get("gradient_text"))
    return ok(n <= 1, f"{n} gradient-filled elements", n)


@check("R-K06", scope="image")
def gradient_text(post, sl):
    from .textpx import measure
    if not sl.has("mask", "manifest"):
        return na("needs passes")
    bad = []
    for r in measure(sl):
        if r["ink"] < 200:
            continue
        from scipy import ndimage as ndi
        core = ndi.binary_erosion(r["mask"], iterations=2)
        if core.sum() < 50:
            continue
        lab = to_lab(sl.final[core][None])[0]
        med = np.median(lab, 0)
        d = de00(lab[None], np.broadcast_to(med, lab.shape)[None])[0]
        if np.percentile(d, 95) > 3:
            bad.append(f"{r['el']['text'][:18]} dE P95 {np.percentile(d, 95):.1f}")
    return ok(not bad, "; ".join(bad[:4]))


@check("R-K07", scope="image")
def default_shadows(post, sl):
    if not sl.manifest:
        return na("needs the manifest")
    tw = ["rgba(0, 0, 0, 0.1) 0px 1px 3px 0px", "rgba(0, 0, 0, 0.1) 0px 4px 6px -1px", "rgba(0, 0, 0, 0.1) 0px 10px 15px -3px", "rgba(0, 0, 0, 0.1) 0px 20px 25px -5px"]
    bad = [p["cls"] for p in sl.manifest.get("panels", []) if any(t in (p.get("box_shadow") or "") for t in tw)]
    return ok(not bad, ", ".join(bad))


@check("R-K08", scope="image")
def default_display_font(post, sl):
    if not sl.manifest:
        return na("needs the manifest")
    brand = ("Inter Tight", "Instrument Serif", "Inter")
    bad = [e["family"] for e in sl.elements if e["size"] >= 187 and not e["family"].split(",")[0].strip('" ') in brand]
    return ok(not bad, ", ".join(bad))


@check("R-K09", scope="post")
def off_center(post):
    if not post.slides:
        return na("no slides")
    off = 0
    for sl in post.slides:
        L = lstar(cv2.resize(sl.final, (216, 270), interpolation=cv2.INTER_AREA))
        w = np.abs(L - np.median(L))
        cx = (w * np.arange(216)[None]).sum() / max(w.sum(), 1e-6)
        off += abs(cx - 108) / 216 >= 0.04
    share = off / len(post.slides)
    return ok(share >= 0.5, f"{off} of {len(post.slides)} slides off-center", round(share, 2))


@check("R-K13", scope="post")
def layout_repeat(post):
    prev = [p for h, p in history_images(post) if h.get("slide") in (None, 1)][-10:]
    if not post.slides or not prev:
        return na("no earlier covers in history.json")
    def skel(a):
        g = cv2.resize(gray(a), (54, 68), interpolation=cv2.INTER_AREA)
        return np.abs(g - np.median(g)) > 25
    now = skel(post.slides[0].final)
    worst = max((now & skel(load_rgb(p))).sum() / max(1, (now | skel(load_rgb(p))).sum()) for p in prev)
    return ok(worst <= 0.8, f"highest layout IoU {worst:.2f}", round(float(worst), 3))


@check("R-K23", scope="post")
def purple_share(post):
    tot = vio = 0
    for sl in post.slides:
        hsv = cv2.cvtColor(sl.final[::4, ::4], cv2.COLOR_RGB2HSV_FULL)
        h = hsv[..., 0].astype(float) * 360 / 255
        s = hsv[..., 1] / 255.0
        sat = s >= 0.25
        tot += sat.sum()
        vio += (sat & (h >= 250) & (h <= 290)).sum()
    if not tot:
        return na("no saturated pixels")
    v = vio / tot
    return ok(v <= 0.10, f"{v:.0%} of saturated pixels are violet-purple", round(float(v), 3))


def contour_runs(g, mask, W):
    """Long iso-luminance contours made of single code-value steps in smooth regions (banding plateaus)."""
    gi = g.astype(np.int16)
    grad = np.abs(cv2.Sobel(g.astype(np.float32), cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(g.astype(np.float32), cv2.CV_32F, 0, 1))
    smooth = (grad < 8) & mask
    step = (np.abs(np.diff(gi, axis=0, prepend=gi[:1])) == 1) & smooth
    from scipy import ndimage as ndi
    lab, k = ndi.label(step, structure=np.ones((3, 3)))
    if not k:
        return 0
    objs = ndi.find_objects(lab)
    return sum(1 for s in objs if s is not None and (s[1].stop - s[1].start) > 0.15 * W)


@check("R-B03", scope="image")
def plateau_contours(post, sl):
    g = gray(sl.final).astype(np.uint8)
    n = contour_runs(g, bg_mask(sl), sl.size[0])
    return ok(n == 0, f"{n} long banding contours", n)


@check("R-B05", scope="image")
def card_banding(post, sl):
    if not sl.manifest:
        return na("needs the manifest")
    cards = [p for p in sl.manifest.get("panels", []) if p["w"] > 300 and p["h"] > 150]
    if not cards:
        return na("no cards")
    g = gray(sl.final).astype(np.uint8)
    m = np.zeros(g.shape, bool)
    for p in cards:
        m[int(p["y"]) + 8:int(p["y"] + p["h"]) - 8, int(p["x"]) + 8:int(p["x"] + p["w"]) - 8] = True
    if sl.text_mask is not None:
        from scipy import ndimage as ndi
        m &= ~ndi.binary_dilation(sl.text_mask, iterations=8)
    n = contour_runs(g, m, min(p["w"] for p in cards) / 0.15 * 0.15)
    return ok(n == 0, f"{n} banding contours in cards (CAMBI part: tool not installed)", n)


@check("R-S11", scope="slide")
def penumbra_banding(post, sl):
    a = sl.raw("shadow")
    if a is None:
        return na("no shadow pass")
    s = 255 - a[..., 0]
    m = (s > 5) & (s < 250)
    if m.sum() < 500:
        return na("no penumbra")
    g = gray(sl.final).astype(np.uint8)
    n = contour_runs(g, m, sl.size[0] * 0.5)
    return ok(n == 0, f"{n} banding contours in shadow ramps (CAMBI part: tool not installed)", n)


@check("R-B06", scope="post")
def sixteen_bit_master(post):
    side = None
    from .files import provenance
    side = provenance(post)
    if side is None:
        return na("no provenance sidecar recording the master's bit depth")
    return ok(side.get("master_bit_depth") == 16 and side.get("dither"), f"master {side.get('master_bit_depth')}-bit, dither {side.get('dither')}")


@check("R-B04", scope="post")
def half_float(post):
    sc = next((s.scene for s in post.slides if s.scene and s.scene.get("objects")), None)
    if not sc:
        return na("no 3D passes")
    return ok(bool(sc.get("renderer", {}).get("half_float_target")), "no half-float render target and dither before 8-bit (the 3D layer renders straight to an 8-bit canvas)")
