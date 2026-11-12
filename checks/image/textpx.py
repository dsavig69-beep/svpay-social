"""Text measured on the pixels: size, contrast, spacing, lines, numbers, OCR integrity and fonts (T-, V-C, V-D, F-IG).

Every measurement uses the render passes: the text mask (glyph pixels), the text-free background under each glyph and
the manifest's boxes, roles and fonts. Without the passes a gate falls back to an estimate that can fail it but never
clear it (ruling 6); a non-gate without its passes is not scored.
"""
from __future__ import annotations

import math
import re

import numpy as np

from . import check
from .core import tesseract_ok as ocr_ok
from .core import (NEEDS, Outcome, apca, apca_y, cer, de00, estimate, gray, lstar, na, ocr, ok, rel_lum, resize_area, simulate_cvd, to_lab,
                   wcag, word_match_rate, font_file, jpeg_roundtrip)

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

XCH = set("acemnorsuvwxz")
CAPCH = set("EFHILT0123456789")
BODY = ("body", "cta")
SPOT = ("source", "footnote", "footer", "label", "counter", "legal")


def need(sl, *passes):
    return Outcome(NEEDS, "needs passes: " + ", ".join(p for p in passes if not sl.has(p)))


# ------------------------------------------------------------------ per-element measurement (cached on the slide)
def measure(sl):
    if "measure" in sl._c:
        return sl._c["measure"]
    from scipy import ndimage as ndi
    from skimage.morphology import skeletonize
    out = []
    mask = sl.text_mask
    fin = sl.final
    bg = sl.img("bg")
    for e in sl.elements:
        rec = {"el": e, "role": e["role"], "chars": [], "lines": []}
        em = sl.element_mask(e)
        rec["mask"] = em
        n_ink = int(em.sum())
        rec["ink"] = n_ink
        if n_ink < 10:
            out.append(rec)
            continue
        heights_x, heights_c, comps = [], [], []
        for li, ln in enumerate(e["lines"]):
            y0, y1 = int(max(0, ln["y"] - 3)), int(min(mask.shape[0], ln["y"] + ln["h"] + 3))
            x0, x1 = int(max(0, ln["x"] - 3)), int(min(mask.shape[1], ln["x"] + ln["w"] + 3))
            sub = mask[y0:y1, x0:x1]
            lab, n = ndi.label(sub, structure=np.ones((3, 3)))
            objs = ndi.find_objects(lab)
            line = {"y0": y0, "y1": y1, "x0": x0, "x1": x1, "comps": [], "text": ln["text"]}
            for k, sll in enumerate(objs, 1):
                if sll is None:
                    continue
                ys, xs = sll
                area = int((lab[sll] == k).sum())
                cx = x0 + (xs.start + xs.stop) / 2
                c = {"k": k, "x0": x0 + xs.start, "x1": x0 + xs.stop, "y0": y0 + ys.start, "y1": y0 + ys.stop, "area": area, "ch": None, "lab": lab, "off": (y0, x0)}
                best = None
                for ch in ln.get("chars", []):
                    chx, chy, chw, chh = ch[1], ch[2], ch[3], ch[4]
                    d = abs(cx - (chx + chw / 2))
                    if chx - 2 <= cx <= chx + chw + 2 and (best is None or d < best[0]):
                        best = (d, ch[0])
                c["ch"] = best[1] if best else None
                line["comps"].append(c)
            # baseline = median bottom of non-descender letters
            bottoms = [c["y1"] for c in line["comps"] if c["ch"] and c["ch"] not in "gjpqy,;()" and c["area"] > 20]
            line["baseline"] = float(np.median(bottoms)) if bottoms else None
            floor_h = 0.25 * ln["h"]
            for c in line["comps"]:
                h = c["y1"] - c["y0"]
                c["edge"] = c["y0"] <= y0 or c["y1"] >= y1      # ink from a neighbouring element reaching into the box
                if h < floor_h or c["edge"]:
                    continue                      # dots, commas, stray pieces and neighbours are not letters
                if c["ch"] in XCH and c["area"] > 15:
                    heights_x.append(h)
                if c["ch"] and c["ch"] in CAPCH:
                    heights_c.append(h)
            rec["lines"].append(line)
            comps += line["comps"]
        rec["comps"] = comps
        rec["h"] = float(np.percentile(heights_x, 40)) if heights_x else None
        rec["H"] = float(np.median(heights_c)) if heights_c else None
        rec["caps_only"] = rec["h"] is None
        if rec["h"] is None and rec["H"] is not None:
            rec["h"] = rec["H"]                 # all caps or digits: the letters are cap height, so that is the size measure
        # stem width: 2 x median distance on the skeleton
        dist = ndi.distance_transform_edt(em)
        sk = skeletonize(em)
        rec["stem"] = float(2 * np.median(dist[sk])) if sk.any() else None
        # colors
        er = max(1, int(round(0.25 * rec["stem"]))) if rec.get("stem") else 1
        core = ndi.binary_erosion(em, iterations=er) if n_ink > 200 else em
        if core.sum() < 20:
            core = ndi.binary_erosion(em, iterations=1)
        if core.sum() < 5:
            core = em
        px = fin[core]
        rec["text_rgb"] = np.median(px, axis=0)
        ys = rel_lum(px)
        rec["text_y_med"] = float(np.median(ys))
        rec["text_y_light"] = float(np.percentile(ys, 90))
        rec["text_y_dark"] = float(np.percentile(ys, 10))
        if bg is not None:
            under = ndi.binary_dilation(em, iterations=1)
            ring_outer = ndi.binary_dilation(em, iterations=max(3, int(0.5 * (rec["h"] or 20))))
            ring = ring_outer & ~ndi.binary_dilation(em, iterations=2)
            ub = bg[under]
            uy = rel_lum(ub)
            rec["bg_y"] = {p: float(np.percentile(uy, p)) for p in (0, 0.5, 1, 5, 50, 95, 99, 99.5, 100)}
            rec["bg_rgb"] = np.median(ub, axis=0)
            ry = rel_lum(bg[ring]) if ring.any() else uy
            rec["ring_y"] = {p: float(np.percentile(ry, p)) for p in (1, 5, 50, 95)}
            dark = rec["text_y_med"] < rec["bg_y"][50]
            rec["dark_text"] = dark
            ty = rec["text_y_light"] if dark else rec["text_y_dark"]
            by = rec["bg_y"][0.5] if dark else rec["bg_y"][99.5]
            rec["wcag_worst"] = wcag(ty, by)
            rec["wcag_med"] = wcag(rec["text_y_med"], rec["bg_y"][50])
            ta = float(np.percentile(apca_y(px), 90 if dark else 10))
            ba = float(np.percentile(apca_y(ub), 1 if dark else 99))
            rec["lc"] = apca(ta, ba)
            rec["lc_med"] = apca(float(np.median(apca_y(px))), float(np.median(apca_y(ub))))
        out.append(rec)
    sl._c["measure"] = out
    return out


def els_by(sl, roles):
    return [r for r in measure(sl) if r["role"] in roles and r["ink"] >= 10]


def px_to_css(sl, v):
    return v / 2.0


# ------------------------------------------------------------------ sizes (V-C01-03, T-S)
@check("T-S05", scope="image")
def size_floor(post, sl):
    if not sl.has("mask", "manifest"):
        words = sl.ocr_words("final")
        if words is None:
            return need(sl, "mask", "manifest")
        hs = [w["h"] for w in words if w["conf"] > 50 and re.search(r"[a-z]", w["text"])]
        m = min(hs) * 0.55 if hs else 99
        return estimate(m >= 24, f"smallest OCR word height x 0.55 = {m:.0f}px")
    hs = [(r["h"], r["el"]["text"][:30]) for r in measure(sl) if r.get("h")]
    if not hs:
        return na("no measurable text")
    h, t = min(hs)
    return ok(h >= 24, f"smallest x-height {h:.0f}px ({t})", round(h, 1))


def size_rule(sl, roles, min_font, min_x, label):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask", "manifest")
    rs = els_by(sl, roles)
    if not rs:
        return na(f"no {label} text")
    bad = []
    for r in rs:
        fs = r["el"]["size"]
        if fs < min_font * sl.scale or (r.get("h") is not None and r["h"] < min_x * sl.scale):
            bad.append(f"{r['el']['text'][:24]} ({fs:.0f}px, x-height {r.get('h') or 0:.0f})")
    return ok(not bad, "; ".join(bad[:4]), len(bad))


@check("V-C01", scope="image")
def smallest_text(post, sl):
    return size_rule(sl, BODY + SPOT + ("headline",), 61, 34, "any")


@check("V-C02", scope="slide")
def body_text_size(post, sl):
    return size_rule(sl, BODY, 94, 46, "body")


@check("V-C03", scope="slide")
def headline_size(post, sl):
    return size_rule(sl, ("headline",), 187, 98, "headline")


@check("T-S02", scope="slide")
def body_xheight_bonus(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    hs = [r["h"] for r in els_by(sl, BODY) if r.get("h")]
    return na("no body text") if not hs else ok(min(hs) >= 52, f"{min(hs):.0f}px", round(min(hs), 1))


@check("T-S06", scope="image")
def running_text_max(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    bad = [r["el"]["text"][:30] for r in measure(sl) if r.get("h") and len(r["el"]["text"].split()) > 3 and r["h"] > 455]
    return ok(not bad, "; ".join(bad))


@check("T-S07", scope="image")
def xheight_ratio(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    rs = [(r["h"] / r["H"], r["el"]["text"][:24]) for r in measure(sl) if r.get("h") and r.get("H") and r.get("H") > 0 and any(c["ch"] in XCH for c in r.get("comps", []))]
    if not rs:
        return na("no element with both x-height and cap letters")
    v, t = min(rs)
    return ok(v >= 0.70, f"{v:.2f} ({t})", round(v, 3))


@check("T-S08", scope="image")
def stem_floor(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    rs = [(r["stem"], r["el"]["text"][:24]) for r in measure(sl) if r.get("stem")]
    if not rs:
        return na("no text")
    v, t = min(rs)
    return ok(v >= 5 * sl.scale, f"{v:.1f}px ({t})", round(v, 2))


@check("T-S09", scope="image")
def no_light_weights(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    rs = [(r["stem"] / r["h"], r["el"]["text"][:24]) for r in measure(sl) if r.get("stem") and r.get("h") and r["h"] < 98]
    if not rs:
        return na("no small text")
    v, t = min(rs)
    return ok(v >= 0.13, f"s/h {v:.3f} ({t})", round(v, 3))


@check("T-S10", scope="image")
def counter_size(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    from scipy import ndimage as ndi
    small = []
    for r in measure(sl):
        if r["ink"] < 10:
            continue
        holes = ndi.binary_fill_holes(r["mask"]) & ~r["mask"]
        lab, n = ndi.label(holes)
        if n:
            areas = ndi.sum(holes, lab, range(1, n + 1))
            d = 2 * math.sqrt(min(areas) / math.pi)
            small.append((d, r["el"]["text"][:24]))
    if not small:
        return na("no counters")
    d, t = min(small)
    return ok(d >= 6 * sl.scale, f"smallest counter {d:.1f}px ({t})", round(d, 2))


@check("T-S12", scope="post")
def hook_number_size(post):
    if not post.slides:
        return na("no slides")
    sl = post.slides[0]
    if sl.has("scene") and sl.objects():
        hs = [p["px_box"][3] - p["px_box"][1] for p in sl.parts() if p.get("char") and p["char"].isdigit()]
        if hs:
            return ok(min(hs) >= 120, f"3D digit height {min(hs)}px", min(hs))
    if not sl.has("mask", "manifest"):
        return na("needs passes")
    nums = [r["H"] for r in measure(sl) if r.get("H") and re.search(r"\$?\d", r["el"]["text"]) and r["role"] == "headline"]
    return na("no hero number") if not nums else ok(max(nums) >= 120, f"digit height {max(nums):.0f}px", max(nums))


@check("T-S15", scope="post")
def hook_small_text(post):
    if not post.slides:
        return na("no slides")
    sl = post.slides[0]
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    hs = [(r["h"], r["el"]["text"][:30]) for r in measure(sl) if r.get("h") and r["role"] not in SPOT]
    if not hs:
        return na("no text")
    h, t = min(hs)
    return ok(h >= 46, f"{h:.0f}px ({t})", round(h, 1))


@check("T-S16", scope="post")
def role_sizes_consistent(post):
    by = {}
    for sl in post.slides:
        if not sl.has("mask", "manifest"):
            return na("needs passes")
        for r in measure(sl):
            if r.get("h") and r["role"] in ("body", "source", "label"):
                by.setdefault(r["role"], []).append(r["h"])
    bad = {k: round(max(v) / min(v), 3) for k, v in by.items() if len(v) > 1 and max(v) / min(v) > 1.03}
    return ok(not bad, ", ".join(f"{k} {v}" for k, v in bad.items()))


def font_metrics(post, sl, family, weight):
    m = sl.manifest
    files = [f for f in m.get("fonts", {}).get("files", []) if f["family"] == family.split(",")[0].strip().strip('"') and str(f["weight"]) == str(weight)]
    if not files:
        return None
    import os
    base = os.path.join(post.base, "pipeline")
    p = font_file(files[0]["src"], base)
    if not p:
        return None
    from fontTools.ttLib import TTFont
    f = TTFont(p)
    return {"path": p, "upm": f["head"].unitsPerEm, "xh": getattr(f["OS/2"], "sxHeight", 0), "cap": getattr(f["OS/2"], "sCapHeight", 0),
            "fixed": f["post"].isFixedPitch, "panose": f["OS/2"].panose.bFamilyType if hasattr(f["OS/2"], "panose") else None, "font": f}


@check("T-S17", scope="image")
def size_matches_font(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    bad, n = [], 0
    for r in measure(sl):
        if not r.get("h") or not any(c["ch"] in XCH for c in r.get("comps", [])):
            continue
        fm = font_metrics(post, sl, r["el"]["family"], r["el"]["weight"])
        if not fm or not fm["xh"]:
            continue
        pred = r["el"]["size"] * fm["xh"] / fm["upm"]
        n += 1
        if abs(r["h"] - pred) / pred > 0.04 + 1.5 / pred:
            bad.append(f"{r['el']['text'][:20]} {r['h']:.0f} vs {pred:.0f}")
    return na("no element with a known font") if not n else ok(not bad, "; ".join(bad[:4]))


@check("F-IG01", scope="post")
def hook_headline_px(post):
    if not post.slides or not post.slides[0].manifest:
        return na("needs the manifest")
    hs = [e["size"] for e in post.slides[0].elements if e["role"] == "headline"]
    return na("no headline") if not hs else ok(max(hs) >= 240, f"{max(hs):.0f}px", max(hs))


# ------------------------------------------------------------------ contrast (V-D, T-C)
def contrast_rows(sl, roles=None):
    if not sl.has("mask", "manifest", "bg"):
        return None
    return [r for r in measure(sl) if "wcag_worst" in r and (roles is None or r["role"] in roles)]


@check("V-D01", scope="image")
def worst_case_contrast(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "mask", "manifest", "bg")
    if not rows:
        return na("no text")
    bad = [(r["wcag_worst"], r["el"]["text"][:30]) for r in rows if r["wcag_worst"] < 4.5]
    worst = min(rows, key=lambda r: r["wcag_worst"])
    return ok(not bad, "; ".join(f"{t} {v:.2f}:1" for v, t in sorted(bad)[:4]) or f"lowest {worst['wcag_worst']:.2f}:1", round(worst["wcag_worst"], 3))


@check("V-D03", scope="image")
def large_text_contrast(post, sl):
    rows = contrast_rows(sl, ("headline",))
    if rows is None:
        return need(sl, "bg")
    if not rows:
        return na("no headline")
    v = min(r["wcag_med"] for r in rows)
    return ok(v >= 4.5, f"{v:.2f}:1", round(v, 2))


@check("V-D05", scope="slide")
def body_7(post, sl):
    rows = contrast_rows(sl, BODY)
    if rows is None:
        return need(sl, "bg")
    if not rows:
        return na("no body text")
    good = all(r["wcag_med"] >= 7 or r["lc"] >= 90 for r in rows)
    return ok(good, f"lowest {min(r['wcag_med'] for r in rows):.2f}:1")


def lc_rule(sl, roles, thr):
    rows = contrast_rows(sl, roles)
    if rows is None:
        return need(sl, "bg")
    if not rows:
        return na("no such text")
    bad = []
    for r in rows:
        t = thr(r)
        if t is not None and abs(r["lc"]) < t:
            bad.append(f"{r['el']['text'][:24]} Lc {r['lc']:.0f} < {t}")
    return ok(not bad, "; ".join(bad[:4]), min(round(r["lc"], 1) for r in rows))


def body_thr(r):
    h = r.get("h") or 0
    return 75 if h >= 45 else 90


@check("T-C01", scope="image")
def apca_body(post, sl):
    return lc_rule(sl, BODY, body_thr)


@check("T-C03", scope="image")
def apca_headline(post, sl):
    return lc_rule(sl, ("headline", "label", "cta"), lambda r: 60 if (r.get("h") or 0) >= 60 or ((r.get("h") or 0) >= 40 and r["el"]["weight"] >= 700) else body_thr(r))


@check("T-C04", scope="image")
def apca_spot(post, sl):
    return lc_rule(sl, ("source", "footnote", "legal"), lambda r: 75)


APCA_LUT = [(90, 14), (75, 18), (60, 24), (45, 36), (30, 48), (15, 72)]   # Lc -> min CSS px at weight 400 (APCA bronze, simplified)


@check("T-C05", scope="image")
def apca_lookup(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    bad = []
    for r in rows:
        if not r.get("h"):
            continue
        need_px = next((px for lc, px in APCA_LUT if r["lc"] >= lc), 999)
        if r["el"]["weight"] >= 700:
            need_px *= 0.8
        if r["h"] < need_px * 2.52 * sl.scale * 0.5 * 2:
            bad.append(f"{r['el']['text'][:20]} Lc {r['lc']:.0f} x-height {r['h']:.0f}")
    return ok(not bad, "; ".join(bad[:4])) if rows else na("no text")


@check("T-C06", scope="image")
def apca_large_max(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    big = [r for r in rows if (r.get("h") or 0) >= 91]
    return na("no large text") if not big else ok(all(r["lc"] <= 90 for r in big), f"max Lc {max(r['lc'] for r in big):.0f}")


@check("T-C08", scope="image")
def contrast_after_delivery(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    sim = sl.sim1
    k = sim.shape[1] / sl.final.shape[1]
    bgs = resize_area(sl.img("bg"), sim.shape[1], sim.shape[0])
    bad = []
    for r in rows:
        m = resize_area(r["mask"].astype(np.uint8) * 255, sim.shape[1], sim.shape[0]) > 160
        if m.sum() < 5:
            continue
        ty = rel_lum(sim[m])
        by = rel_lum(bgs[m])
        dark = r.get("dark_text", True)
        v = wcag(float(np.percentile(ty, 90 if dark else 10)), float(np.percentile(by, 0.5 if dark else 99.5)))
        if v < 4.5 or v < r["wcag_worst"] * 0.9 - 0.3:
            bad.append(f"{r['el']['text'][:20]} {r['wcag_worst']:.1f} -> {v:.1f}")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-C09", scope="image")
def even_contrast(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    bg = sl.img("bg")
    bad = []
    from scipy import ndimage as ndi
    for r in rows:
        lcs = []
        for c in r.get("comps", []):
            if c["area"] < 30:
                continue
            y0, x0 = c["off"]
            sub = c["lab"] == c["k"]
            ys, xs = np.nonzero(sub)
            ys, xs = ys + y0, xs + x0
            t = float(np.percentile(apca_y(sl.final[ys, xs]), 90))
            b = float(np.median(apca_y(bg[np.clip(ys, 0, bg.shape[0] - 1), np.clip(xs, 0, bg.shape[1] - 1)])))
            lcs.append(apca(t, b))
        if len(lcs) > 2 and max(lcs) - min(lcs) > 15:
            bad.append(f"{r['el']['text'][:20]} spread {max(lcs) - min(lcs):.0f}")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-C10", scope="image")
def background_detail(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    g = rel_lum(sl.img("bg").astype(np.float64))
    bad = []
    for r in rows:
        h = r.get("h") or 20
        lo = cv2.GaussianBlur(g, (0, 0), max(1.0, h / 5 / 2.0))
        hi = cv2.GaussianBlur(g, (0, 0), max(1.0, h / 1 / 2.0))
        band = lo - hi
        box = r["el"]["box"]
        y0, y1, x0, x1 = int(box["y"]), int(box["y"] + box["h"]), int(box["x"]), int(box["x"] + box["w"])
        sub, mean = band[y0:y1, x0:x1], g[y0:y1, x0:x1].mean()
        v = float(np.sqrt((sub ** 2).mean()) / max(mean, 1e-3))
        if v > 0.02:
            bad.append(f"{r['el']['text'][:20]} {v:.3f}")
    return ok(not bad, "; ".join(bad[:4]))


@check("V-C17", scope="image")
def busy_background(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    g = lstar(sl.img("bg"))
    bad = []
    for r in rows:
        b = r["el"]["box"]
        sub = g[int(b["y"]):int(b["y"] + b["h"]), int(b["x"]):int(b["x"] + b["w"])]
        if sub.size and sub.std() > 12:
            bad.append(f"{r['el']['text'][:20]} {sub.std():.1f}")
    return ok(not bad, "; ".join(bad[:4]))


def edges_near(sl, r, dist):
    bg = gray(sl.img("bg"))
    e = cv2.Canny(bg.astype(np.uint8), 20, 60) > 0
    from scipy import ndimage as ndi
    zone = ndi.binary_dilation(r["mask"], iterations=int(dist)) & ~ndi.binary_dilation(r["mask"], iterations=2)
    return int((e & zone).sum())


@check("T-C11", scope="image")
def hard_edges(post, sl):
    rows = contrast_rows(sl, BODY + ("headline", "source", "footnote"))
    if rows is None:
        return need(sl, "bg")
    from scipy import ndimage as ndi
    bad = []
    panels = (sl.manifest or {}).get("panels", [])
    for r in rows:
        h = r.get("h") or 20
        b = r["el"]["box"]
        n = edges_near(sl, r, 0.5 * h)
        own = [p for p in panels if p["x"] <= b["x"] and p["y"] <= b["y"] and p["x"] + p["w"] >= b["x"] + b["w"] and p["y"] + p["h"] >= b["y"] + b["h"]]
        if n > 0.02 * r["ink"] and not own:
            bad.append(f"{r['el']['text'][:20]} {n}px")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-C12", scope="image")
def colored_text_luminance(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    bad = []
    for r in rows:
        lab = to_lab(r["text_rgb"][None, None, :])[0, 0]
        if math.hypot(lab[1], lab[2]) > 20 and r.get("dark_text") and lab[0] > 50 and (r.get("h") or 0) < 60:
            bad.append(f"{r['el']['text'][:20]} L* {lab[0]:.0f}")
    return ok(not bad, "; ".join(bad[:4]))


BRAND_BLUE = np.array([0x23, 0x50, 0xFF])


@check("T-C13", scope="image")
def blue_text_large(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    bl = to_lab(BRAND_BLUE[None, None, :])[0, 0]
    blue = [r for r in rows if de00(to_lab(r["text_rgb"][None, None, :])[0, 0], bl) <= 10]
    if not blue:
        return na("no brand-blue text")
    bad = [r["el"]["text"][:20] for r in blue if (r.get("h") or 0) < 60]
    return ok(not bad, ", ".join(bad))


@check("T-C15", scope="image")
def opaque_text(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    bad = [f"{r['text'][:20]} opacity {r['opacity']:.2f} {r['blend']}" for e in sl.elements for r in e.get("runs", []) if r["opacity"] < 0.999 or r["blend"] != "normal"]
    return ok(not bad, "; ".join(bad[:4]))


@check("T-C16", scope="image")
def color_matches_spec(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    bad = []
    for r in rows:
        run = r["el"]["runs"][0] if r["el"].get("runs") else None
        if not run or run.get("gradient_text") or len(r["el"]["runs"]) > 1:
            continue
        m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", run["color"])
        if not m:
            continue
        spec = np.array([int(m.group(i)) for i in (1, 2, 3)])
        d = float(de00(to_lab(r["text_rgb"][None, None, :])[0, 0], to_lab(spec[None, None, :])[0, 0]))
        if d > 3:
            bad.append(f"{r['el']['text'][:20]} dE {d:.1f}")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-C17", scope="image")
def halo_rule(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    bad = [f"{e['text'][:20]}" for e in sl.elements for r in e.get("runs", []) if r.get("text_shadow") not in (None, "none") and e["role"] in BODY + SPOT]
    return ok(not bad, ", ".join(bad[:4]))


@check("T-C18", scope="image")
def polarity(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    bad = [r["el"]["text"][:20] for r in rows if not r.get("dark_text") and not any(p for p in (sl.manifest or {}).get("panels", []))]
    light = [r["el"]["text"][:20] for r in rows if not r.get("dark_text")]
    return ok(not light, ", ".join(light[:4]))


@check("T-C19", scope="image")
def ghost_text(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    bad = [f"{r['el']['text'][:20]} Lc {r['lc_med']:.0f}" for r in rows if abs(r["lc_med"]) < 45]
    return ok(not bad, "; ".join(bad[:4]))


@check("T-C20", scope="image")
def unlisted_text(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "manifest")
    words = sl.ocr_words("final")
    if words is None:
        return na("needs OCR")
    boxes = [e["box"] for e in sl.elements]
    out = [w for w in words if w["conf"] > 60 and len(w["text"]) > 2 and not any(b["x"] - 20 <= w["x"] + w["w"] / 2 <= b["x"] + b["w"] + 20 and b["y"] - 20 <= w["y"] + w["h"] / 2 <= b["y"] + b["h"] + 20 for b in boxes)]
    if not out or not sl.has("bg"):
        return ok(True, "") if not out else na("needs passes")
    bg = sl.img("bg")
    bad = []
    for w in out:
        sub = sl.final[w["y"]:w["y"] + w["h"], w["x"]:w["x"] + w["w"]]
        g = rel_lum(sub)
        if g.size and wcag(float(g.min()), float(g.max())) < 4.5:
            bad.append(w["text"])
    return ok(not bad, ", ".join(bad[:5]))


@check("T-C22", scope="image")
def glare(post, sl):
    rows = contrast_rows(sl, BODY + SPOT)
    if rows is None:
        return need(sl, "bg")
    if not rows:
        return na("no body or spot text")
    v = min(((r["text_y_light"] + 0.05 + 0.05) / (r["bg_y"][0.5] + 0.05 + 0.05)) ** -1 if r["bg_y"][0.5] < r["text_y_light"] else (r["bg_y"][0.5] + 0.1) / (r["text_y_light"] + 0.1) for r in rows)
    return ok(v >= 4.5, f"{v:.2f}:1 with 5% glare", round(v, 2))


@check("QA01", scope="image")
def cvd_contrast(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    bad = []
    for kind in ("protan", "deutan", "tritan"):
        for r in rows:
            t = simulate_cvd(r["text_rgb"][None, :], kind)[0]
            b = simulate_cvd(np.asarray(r["bg_rgb"])[None, :], kind)[0]
            v = wcag(float(rel_lum(t)), float(rel_lum(b)))
            if v < 4.5:
                bad.append(f"{kind} {r['el']['text'][:16]} {v:.1f}")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-C24", scope="image")
def low_acuity(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "manifest")
    rows = [r for r in measure(sl) if r.get("h")]
    body_h = min([r["h"] for r in rows if r["role"] in BODY] or [46])
    blur = cv2.GaussianBlur(sl.final, (0, 0), 0.06 * body_h)
    bad = []
    for r in rows:
        if r["role"] not in ("headline",) and not re.search(r"\d", r["el"]["text"]):
            continue
        b = r["el"]["box"]
        crop = blur[max(0, int(b["y"] - 8)):int(b["y"] + b["h"] + 8), max(0, int(b["x"] - 8)):int(b["x"] + b["w"] + 8)]
        got = " ".join(w["text"] for w in (ocr(crop, 6) or []))
        if word_match_rate(r["el"]["text"], got) < 1.0:
            bad.append(r["el"]["text"][:24])
    return ok(not bad, " | ".join(bad[:4]))


@check("T-C25", scope="post")
def role_contrast_consistent(post):
    by = {}
    for sl in post.slides:
        rows = contrast_rows(sl)
        if rows is None:
            return na("needs passes")
        for r in rows:
            by.setdefault(r["role"], []).append(r["lc"])
    bad = {k: round(max(v) - min(v)) for k, v in by.items() if len(v) > 1 and max(v) - min(v) > 10 and k in ("body", "headline", "source")}
    return ok(not bad, ", ".join(f"{k} range {v}" for k, v in bad.items()))


@check("F-IG08", scope="post")
def hook_highest_contrast(post):
    if not post.slides:
        return na("no slides")
    rows = contrast_rows(post.slides[0])
    if rows is None:
        return na("needs passes")
    hs = [r for r in rows if r["role"] == "headline"]
    if not hs:
        return na("no headline")
    hv = min(r["wcag_med"] for r in hs)
    other = max([r["wcag_med"] for r in rows if r["role"] != "headline"] or [0])
    return ok(hv >= 7 and hv >= other - 0.01, f"headline {hv:.1f}:1, highest other {other:.1f}:1", round(hv, 2))


# ------------------------------------------------------------------ glass panels under text (T-G)
def panel_of(sl, box):
    for p in (sl.manifest or {}).get("panels", []):
        if p["x"] - 2 <= box["x"] and p["y"] - 2 <= box["y"] and p["x"] + p["w"] + 2 >= box["x"] + box["w"] and p["y"] + p["h"] + 2 >= box["y"] + box["h"]:
            return p
    return None


@check("T-G01", scope="image")
def backdrop_blur(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "manifest")
    bad, n = [], 0
    for r in measure(sl):
        p = panel_of(sl, r["el"]["box"])
        if not p or not r.get("h"):
            continue
        m = re.search(r"blur\(([\d.]+)px\)", p.get("backdrop") or "")
        if not m:
            continue
        n += 1
        sigma = float(m.group(1)) * sl.scale
        if sigma < 0.4 * r["h"]:
            bad.append(f"{r['el']['text'][:20]} blur {sigma:.0f}px < {0.4 * r['h']:.0f}")
    return na("no text on a blurred panel") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-G02", scope="image")
def grain_under_text(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    g = rel_lum(sl.img("bg").astype(np.float64)) * 100
    hp = g - cv2.GaussianBlur(g, (0, 0), 2)
    from scipy import ndimage as ndi
    worst = 0
    for r in rows:
        ring = ndi.binary_dilation(r["mask"], iterations=6) & ~r["mask"]
        if ring.any():
            worst = max(worst, float(hp[ring].std()))
    return ok(worst <= 1.5, f"high-pass sd {worst:.2f}", round(worst, 3))


@check("T-G03", scope="image")
def straddle(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "manifest")
    panels = sl.manifest.get("panels", [])
    bad = []
    for r in measure(sl):
        if r["ink"] < 10:
            continue
        ys, xs = np.nonzero(r["mask"])
        for p in panels:
            inside = ((xs >= p["x"]) & (xs < p["x"] + p["w"]) & (ys >= p["y"]) & (ys < p["y"] + p["h"])).mean()
            if 0.0 < inside < 0.995:
                bad.append(f"{r['el']['text'][:20]} {inside:.0%} inside")
    return ok(not bad, "; ".join(bad[:4]))


# ------------------------------------------------------------------ spacing (T-K)
def adjacent_pairs(rec):
    for line in rec["lines"]:
        comps = sorted([c for c in line["comps"] if c["ch"] and c["ch"].strip() and c["area"] > 8], key=lambda c: c["x0"])
        for a, b in zip(comps, comps[1:]):
            if a["ch"] in "ij!?;:%\"" and b["ch"] == a["ch"]:
                continue
            yield line, a, b


def comp_pixels(c):
    y0, x0 = c["off"]
    ys, xs = np.nonzero(c["lab"] == c["k"])
    return ys + y0, xs + x0


def comp_distance(a, b):
    ya, xa = comp_pixels(a)
    yb, xb = comp_pixels(b)
    if not len(xa) or not len(xb):
        return 99.0
    # nearest distance between the right edge of a and the left edge of b, row by row, plus a 2D fallback
    pa = np.stack([ya, xa], 1)
    pb = np.stack([yb, xb], 1)
    pa = pa[pa[:, 1] >= pa[:, 1].max() - 12]
    pb = pb[pb[:, 1] <= pb[:, 1].min() + 12]
    d = np.sqrt(((pa[:, None, :] - pb[None, :, :]) ** 2).sum(-1)).min() - 1
    return float(max(0.0, d))


@check("T-K01", scope="image")
def glyph_clearance(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "mask", "manifest")
    bad = []
    for r in measure(sl):
        if not r.get("h"):
            continue
        thr = max(2.0, 0.04 * r["h"])
        words_ok = set()
        for line, a, b in adjacent_pairs(r):
            if a["k"] == b["k"]:
                # one component spans two characters: a touch, unless the font itself joins them (ligature)
                if not (a["ch"] + b["ch"] in ("fi", "fl", "ff", "ft", "tt")):
                    bad.append(f"'{a['ch']}{b['ch']}' touch in {r['el']['text'][:20]}")
                continue
            d = comp_distance(a, b)
            if d < thr and not (a["ch"] == " " or b["ch"] == " "):
                bad.append(f"'{a['ch']}{b['ch']}' {d:.1f}px in {r['el']['text'][:20]}")
    return ok(not bad, "; ".join(bad[:5]), len(bad))


def reference_components(post, sl, rec):
    """Component count of the element's text rendered alone in its own font (Pillow), per word."""
    from PIL import Image, ImageDraw, ImageFont
    fm = font_metrics(post, sl, rec["el"]["family"], rec["el"]["weight"])
    if not fm:
        return None
    from scipy import ndimage as ndi
    font = ImageFont.truetype(fm["path"], int(round(rec["el"]["size"])))
    out = 0
    for ln in rec["el"]["lines"]:
        t = ln["text"]
        w = int(font.getlength(t)) + 40
        im = Image.new("L", (w, int(rec["el"]["size"] * 1.6)), 0)
        ImageDraw.Draw(im).text((10, 10), t, fill=255, font=font)
        a = np.asarray(im) > 127
        out += ndi.label(a, structure=np.ones((3, 3)))[1]
    return out


@check("T-K02", scope="image")
def merged_glyphs(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "mask")
    bad, n = [], 0
    for r in measure(sl):
        if not r.get("h") or r["el"]["runs"] and r["el"]["runs"][0].get("text_transform") == "uppercase":
            continue
        ref = reference_components(post, sl, r)
        if ref is None:
            continue
        got = sum(len([c for c in l["comps"] if c["area"] > 3]) for l in r["lines"])
        n += 1
        if abs(got - ref) > max(1, 0.02 * ref):
            bad.append(f"{r['el']['text'][:20]} {got} vs {ref}")
    return na("no element with a known font") if not n else ok(not bad, "; ".join(bad[:4]))


def tracking_em(rec):
    run = rec["el"]["runs"][0] if rec["el"].get("runs") else {}
    return (run.get("letter_spacing") or 0) / max(1.0, rec["el"]["size"])


def tracking_rule(sl, sel, lo, hi):
    if not sl.has("manifest", "mask"):
        return need(sl, "manifest")
    rs = [r for r in measure(sl) if sel(r)]
    if not rs:
        return na("no such text")
    bad = [f"{r['el']['text'][:20]} {tracking_em(r):+.3f}em" for r in rs if not (lo - 1e-6 <= tracking_em(r) <= hi + 1e-6)]
    return ok(not bad, "; ".join(bad[:4]))


def is_caps(r):
    return (r["el"]["runs"] and r["el"]["runs"][0].get("text_transform") == "uppercase") or (r["el"]["text"].isupper() and len(r["el"]["text"]) > 2)


@check("T-K03", scope="image")
def body_tracking(post, sl):
    return tracking_rule(sl, lambda r: r["role"] in BODY and not is_caps(r), -0.01, 0.03)


@check("T-K04", scope="image")
def headline_tracking(post, sl):
    return tracking_rule(sl, lambda r: r["role"] == "headline" and not is_caps(r), -0.04, 0.01)


@check("T-K05", scope="image")
def caps_tracking(post, sl):
    return tracking_rule(sl, lambda r: is_caps(r) and len(r["el"]["text"]) > 2, 0.05, 0.12)


@check("T-K06", scope="image")
def small_tracking(post, sl):
    return tracking_rule(sl, lambda r: (r.get("h") or 99) < 46 and not is_caps(r), 0.0, 1.0)


@check("T-K08", scope="image")
def word_spacing(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "mask")
    bad = []
    for r in measure(sl):
        if r["role"] not in BODY or not r.get("h"):
            continue
        for line in r["lines"]:
            comps = sorted([c for c in line["comps"] if c["area"] > 8], key=lambda c: c["x0"])
            gaps = [b["x0"] - a["x1"] for a, b in zip(comps, comps[1:])]
            if len(gaps) < 4:
                continue
            letter = float(np.median(gaps))
            words = [g for g in gaps if g > letter * 2.5]
            if words:
                wg = float(np.median(words)) / r["el"]["size"]
                if not (0.2 <= wg <= 0.5):
                    bad.append(f"{r['el']['text'][:20]} {wg:.2f}em")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-K09", scope="image")
def line_clearance(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "mask")
    bad = []
    for r in measure(sl):
        if len(r["lines"]) < 2 or not r.get("h"):
            continue
        for a, b in zip(r["lines"], r["lines"][1:]):
            ya = max([c["y1"] for c in a["comps"]] or [0])
            yb = min([c["y0"] for c in b["comps"]] or [10 ** 6])
            if yb - ya < 0.15 * r["h"]:
                bad.append(f"{r['el']['text'][:20]} {yb - ya}px")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-K10", scope="image")
def crowding(post, sl):
    if not sl.has("manifest", "mask", "bg"):
        return need(sl, "bg")
    from scipy import ndimage as ndi
    bad = []
    rows = measure(sl)
    for r in rows:
        if r["role"] not in BODY + ("source", "footnote") or not r.get("h"):
            continue
        zone = ndi.binary_dilation(r["mask"], iterations=int(46 * sl.scale)) & ~ndi.binary_dilation(r["mask"], iterations=3)
        others = np.zeros_like(zone)
        for o in rows:
            if o is not r:
                others |= o["mask"]
        if (zone & others).sum() > 20:
            bad.append(r["el"]["text"][:20])
    return ok(not bad, ", ".join(bad[:4]))


@check("T-K11", scope="image")
def pill_centering(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "mask")
    bad, n = [], 0
    for r in measure(sl):
        p = panel_of(sl, r["el"]["box"])
        if not p or p["h"] > 160 * sl.scale or not r.get("comps"):
            continue
        n += 1
        top = min(c["y0"] for c in r["comps"] if c["ch"] and (c["ch"].isupper() or c["ch"].isdigit()) or True)
        base = r["lines"][0].get("baseline") or max(c["y1"] for c in r["comps"])
        g1, g2 = top - p["y"], p["y"] + p["h"] - base
        if abs(g1 - g2) > 3 * sl.scale * 2:
            bad.append(f"{r['el']['text'][:20]} {g1:.0f}/{g2:.0f}")
    return na("no pills") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-K12", scope="image")
def card_padding_optical(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "mask")
    bad, n = [], 0
    for p in sl.manifest.get("panels", []):
        inside = [r for r in measure(sl) if r.get("comps") and panel_of(sl, r["el"]["box"]) is p]
        if not inside or p["h"] < 200:
            continue
        n += 1
        top = min(c["y0"] for r in inside for c in r["comps"]) - p["y"]
        left = min(c["x0"] for r in inside for c in r["comps"]) - p["x"]
        if left and abs(top - left) / left > 0.10:
            bad.append(f"top {top:.0f} vs left {left:.0f}")
    return na("no cards with text") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-K14", scope="image")
def parallel_baselines(post, sl):
    if not sl.has("manifest", "mask"):
        return need(sl, "mask")
    cards = [p for p in sl.manifest.get("panels", []) if p["h"] > 150]
    if len(cards) < 2:
        return na("no side-by-side cards")
    rows = sorted(cards, key=lambda p: p["y"])
    side = [(a, b) for a in cards for b in cards if a is not b and abs(a["y"] - b["y"]) < 10 and a["x"] < b["x"]]
    if not side:
        return na("no side-by-side cards")
    bad = []
    for a, b in side:
        fa = [r for r in measure(sl) if panel_of(sl, r["el"]["box"]) is a and r["lines"]]
        fb = [r for r in measure(sl) if panel_of(sl, r["el"]["box"]) is b and r["lines"]]
        if fa and fb and fa[0]["lines"][0].get("baseline") and fb[0]["lines"][0].get("baseline") and abs(fa[0]["lines"][0]["baseline"] - fb[0]["lines"][0]["baseline"]) > 2:
            bad.append("baselines differ")
    return ok(not bad, ", ".join(bad))


@check("T-K16", scope="post")
def tracking_consistent(post):
    by = {}
    for sl in post.slides:
        if not sl.manifest:
            return na("needs the manifest")
        for e in sl.elements:
            if e.get("runs"):
                by.setdefault(e["role"], set()).add(round(e["runs"][0]["letter_spacing"] / max(1, e["size"]), 3))
    bad = {k: sorted(v) for k, v in by.items() if len(v) > 1 and max(v) - min(v) > 0.005 and k in ("body", "headline", "source", "label")}
    return ok(not bad, "; ".join(f"{k}: {v}" for k, v in bad.items()))


@check("T-K17", scope="slide")
def vertical_rhythm(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    hs = [e for e in sl.elements if e["role"] == "headline"]
    bs = [e for e in sl.elements if e["role"] == "body"]
    if not hs or not bs:
        return na("no headline and body")
    h = max(hs, key=lambda e: e["box"]["y"])
    b = min([e for e in bs if e["box"]["y"] > h["box"]["y"]] or [None], key=lambda e: e["box"]["y"] if e else 0)
    if not b:
        return na("no body below the headline")
    pitch = b["lines"][1]["y"] - b["lines"][0]["y"] if len(b["lines"]) > 1 else b["size"] * 1.4
    gap = b["box"]["y"] - (h["box"]["y"] + h["box"]["h"])
    return ok(0.8 * pitch <= gap <= 3.0 * pitch, f"gap {gap:.0f}px, pitch {pitch:.0f}px")


# ------------------------------------------------------------------ lines (T-L)
def line_rule(sl, roles, fn):
    if not sl.manifest:
        return need(sl, "manifest")
    es = [e for e in sl.elements if e["role"] in roles]
    if not es:
        return na("no such text")
    bad = [x for e in es for x in fn(e)]
    return ok(not bad, "; ".join(bad[:4]))


@check("T-L01", scope="image")
def min_line_length(post, sl):
    return line_rule(sl, BODY, lambda e: [f"{l['text'][:20]} ({len(l['text'])})" for l in e["lines"][:-1] if len(l["text"]) < 16])


@check("T-L02", scope="image")
def lines_per_body(post, sl):
    return line_rule(sl, BODY, lambda e: [f"{e['text'][:20]} {len(e['lines'])} lines"] if len(e["lines"]) > 4 else [])


@check("T-L03", scope="image")
def headline_lines(post, sl):
    return line_rule(sl, ("headline",), lambda e: [f"{e['text'][:20]} {len(e['lines'])} lines"] if len(e["lines"]) > 3 else [])


@check("T-L04", scope="slide")
def lines_per_slide(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    n = sum(len(e["lines"]) for e in sl.elements if e["role"] in BODY + ("headline",))
    return ok(n <= 9, f"{n} lines", n)


@check("T-L05", scope="image")
def body_runts(post, sl):
    def f(e):
        if len(e["lines"]) < 2:
            return []
        last = e["lines"][-1]
        longest = max(l["w"] for l in e["lines"])
        return [f"{e['text'][:20]}: '{last['text']}'"] if len(last["text"].split()) < 2 or last["w"] < 0.25 * longest else []
    return line_rule(sl, BODY, f)


@check("T-L06", scope="image")
def rag(post, sl):
    def f(e):
        if len(e["lines"]) < 3 or e.get("align") not in ("left", "start"):
            return []
        longest = max(l["w"] for l in e["lines"])
        return [f"{e['text'][:20]}"] if any(l["w"] < 0.65 * longest for l in e["lines"][:-1]) else []
    return line_rule(sl, BODY, f)


WEAK = {"a", "an", "the", "of", "to", "in", "on", "at", "by", "for", "and", "or", "with"}


@check("T-L07", scope="image")
def weak_endings(post, sl):
    return line_rule(sl, ("headline",), lambda e: [f"'{l['text'][-20:]}'" for l in e["lines"][:-1] if l["text"].split() and l["text"].split()[-1].lower().strip(",.") in WEAK])


@check("T-L08", scope="image")
def no_hyphen_breaks(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    bad = [l["text"][-20:] for e in sl.elements for l in e["lines"][:-1] if re.search(r"\w-$", l["text"]) and not re.search(r"\w-\w", e["text"])]
    hy = [e["text"][:20] for e in sl.elements for r in e.get("runs", []) if r.get("hyphens") == "auto"]
    return ok(not bad and not hy, "; ".join(bad + hy))


@check("T-L09", scope="image")
def no_justify(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    bad = [e["text"][:20] for e in sl.elements if e.get("align") == "justify"]
    return ok(not bad, ", ".join(bad))


@check("T-L11", scope="image")
def even_leading(post, sl):
    def f(e):
        if len(e["lines"]) < 3:
            return []
        ys = [l["y"] for l in e["lines"]]
        d = np.diff(ys)
        return [f"{e['text'][:20]} {d.max() - d.min():.0f}px"] if d.max() - d.min() > 2 else []
    return line_rule(sl, BODY + ("headline",), f)


@check("T-L12", scope="image")
def balanced_centered(post, sl):
    def f(e):
        if e.get("align") != "center" or len(e["lines"]) not in (2, 3):
            return []
        ws = [l["w"] for l in e["lines"]]
        return [e["text"][:20]] if min(ws) < 0.6 * max(ws) else []
    return line_rule(sl, ("headline",), f)


@check("T-L15", scope="image")
def hanging_indent(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    items = [e for e in sl.elements if len(e["lines"]) > 1 and e["role"] in BODY]
    bad = [e["text"][:20] for e in items if abs(e["lines"][1]["x"] - e["lines"][0]["x"]) > 3 * sl.scale * 2]
    return ok(not bad, ", ".join(bad[:4])) if items else na("no wrapped list items")


@check("T-L17", scope="image")
def emphasis(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    bad = []
    for e in sl.elements:
        runs = e.get("runs", [])
        if len(runs) < 2:
            continue
        base = runs[0]
        emph = [r for r in runs[1:] if r["weight"] != base["weight"] or r["color"] != base["color"] or r.get("gradient_text")]
        if len(emph) > 1 or any(len(r["text"].split()) > 4 for r in emph):
            bad.append(e["text"][:20])
    return ok(not bad, ", ".join(bad))


@check("T-L18", scope="image")
def underlines(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    bad = [e["text"][:20] for e in sl.elements for r in e.get("runs", []) if "underline" in (r.get("decoration") or "")]
    return ok(not bad, ", ".join(bad))


@check("T-O07", scope="image")
def line_breaks(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    over = [e["text"][:20] for e in sl.elements if (e["role"] == "headline" and len(e["lines"]) > 3) or (e["role"] in BODY and len(e["lines"]) > 4)]
    return ok(not over, ", ".join(over))


@check("T-O12", scope="slide")
def reading_order(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    es = [e for e in sl.elements if e["role"] in ("headline", "body", "source", "footnote")]
    pos = [e["id"] for e in sorted(es, key=lambda e: (round(e["box"]["y"] / 40), e["box"]["x"]))]
    dom = [e["id"] for e in es]
    return ok(pos == dom, "" if pos == dom else "visual order differs from the narrative order")


# ------------------------------------------------------------------ numbers on pixels (T-N)
NUMTOK = re.compile(r"[−\-]?\$?\d[\d,]*(?:\.\d+)?(?:%|¢|/mo|/yr|[KMB])?")


@check("T-N06", scope="image")
def numbers_one_line(post, sl):
    if not sl.manifest:
        t = sl.text
        return estimate(True, "no line boxes without the manifest") if t else na("no text")
    bad = []
    for e in sl.elements:
        intended = e["text"]          # the element's rendered text in reading order (runs come in DOM-walk order)
        lines = [re.sub(r"\s+", " ", l["text"]) for l in e["lines"]]
        for m in NUMTOK.finditer(intended):
            tok = m.group(0).rstrip(".,")
            if len(tok) < 2:
                continue
            if not any(tok in l for l in lines):
                bad.append(tok)
    return ok(not bad, "split across lines: " + ", ".join(bad))


def digit_comps(rec):
    out = []
    for line in rec["lines"]:
        cs = [c for c in line["comps"] if c["ch"] and (c["ch"].isdigit() or c["ch"] in "$%.,")]
        out.append((line, cs))
    return out


@check("T-N01", scope="image")
def lining_figures(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    bad = []
    for r in measure(sl):
        for line, cs in digit_comps(r):
            hs = [c["y1"] - c["y0"] for c in cs if c["ch"].isdigit()]
            if len(hs) > 1 and max(hs) / max(1, min(hs)) > 1.03 + 2 / max(hs):
                bad.append(f"{line['text'][:20]} {min(hs)}-{max(hs)}px")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-N02", scope="image")
def dollar_size(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    bad, n = [], 0
    for r in measure(sl):
        for line, cs in digit_comps(r):
            for a, b in zip(cs, cs[1:]):
                if a["ch"] == "$" and b["ch"].isdigit():
                    n += 1
                    ratio = (a["y1"] - a["y0"]) / max(1, b["y1"] - b["y0"])
                    if not (0.55 <= ratio <= 1.25):
                        bad.append(f"$ {ratio:.2f}x digit")
    return na("no $ amounts") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-N03", scope="image")
def dollar_gap(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    bad, n = [], 0
    for r in measure(sl):
        for line, cs in digit_comps(r):
            for a, b in zip(cs, cs[1:]):
                if a["ch"] == "$" and b["ch"].isdigit():
                    n += 1
                    g = (b["x0"] - a["x1"]) / max(1, b["y1"] - b["y0"])
                    if not (0.0 <= g <= 0.12):
                        bad.append(f"gap {g:.2f}x digit height")
    return na("no $ amounts") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-N04", scope="image")
def percent_size(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    bad, n = [], 0
    for r in measure(sl):
        for line, cs in digit_comps(r):
            pc = [c for c in line["comps"] if c["ch"] == "%"]
            ds = [c for c in cs if c["ch"].isdigit()]
            if pc and ds:
                n += 1
                h = max(c["y1"] for c in pc) - min(c["y0"] for c in pc)
                ratio = h / float(np.median([d["y1"] - d["y0"] for d in ds]))
                if not (0.9 <= ratio <= 1.1):
                    bad.append(f"% {ratio:.2f}x digits")
    return na("no percent signs") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-N07", scope="image")
def thousands_visible(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    bad, n = [], 0
    for r in measure(sl):
        for line, cs in digit_comps(r):
            for i, c in enumerate(cs):
                if c["ch"] == ",":
                    n += 1
                    nb = [x for x in (cs[i - 1] if i else None, cs[i + 1] if i + 1 < len(cs) else None) if x]
                    if any(x["k"] == c["k"] for x in nb):
                        bad.append(f"comma merged in {line['text'][:16]}")
    return na("no thousands separators") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-N08", scope="image")
def decimal_visible(post, sl):
    if not sl.has("mask", "manifest"):
        o = sl.ocr_words("final")
        if o is None:
            return need(sl, "mask")
        txt = sl.text or ""
        dec = re.findall(r"\d\.\d", txt)
        if not dec:
            return na("no decimal points")
        got = " ".join(w["text"] for w in o)
        # a decimal counts as lost only when OCR read the digits around it without the point; a number OCR did not
        # read at all is inconclusive (large gradient numerals often defeat Tesseract), so the gate waits for the passes
        lost = [d for d in dec if d not in got and d.replace(".", "") in got.replace(",", "")]
        if lost:
            return estimate(False, "decimal point lost in OCR read: " + ", ".join(lost))
        if any(d not in got for d in dec):
            return need(sl, "mask")
        return estimate(True, "decimals read by OCR")
    bad, n = [], 0
    for r in measure(sl):
        for line, cs in digit_comps(r):
            for i, c in enumerate(cs):
                if c["ch"] == "." and 0 < i < len(cs) - 1 and cs[i - 1]["ch"].isdigit() and cs[i + 1]["ch"].isdigit():
                    n += 1
                    dh = cs[i - 1]["y1"] - cs[i - 1]["y0"]
                    if c["k"] in (cs[i - 1]["k"], cs[i + 1]["k"]) or c["area"] < (0.10 * dh) ** 2:
                        bad.append(f"decimal point lost in {line['text'][:16]}")
    for o in sl.objects():
        if o.get("role") == "numeral" and "." in (o.get("text") or ""):
            parts = [p for p in sl.parts() if p["object"] == o["index"] and p.get("char") == "."]
            n += len(parts)
            for p in parts:
                vis = sl.visible_part(p["part"])
                if vis is None or vis.sum() < 50:
                    bad.append(f"3D decimal point hidden in {o['text']}")
    return na("no decimal points") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-N09", scope="image")
def digit_spacing(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    bad, n = [], 0
    for r in measure(sl):
        if r["role"] != "headline" and (r.get("H") or 0) < 120:
            continue
        for line, cs in digit_comps(r):
            ds = [c for c in cs if c["ch"].isdigit()]
            if len(ds) < 3:
                continue
            n += 1
            gaps = [b["x0"] - a["x1"] for a, b in zip(ds, ds[1:]) if b["x0"] - a["x1"] < (b["y1"] - b["y0"])]
            h = float(np.median([d["y1"] - d["y0"] for d in ds]))
            if gaps and (max(gaps) > 1.5 * max(1, np.median(gaps)) + 2 or np.median(gaps) > 0.12 * h):
                bad.append(f"{line['text'][:16]} gaps {min(gaps)}-{max(gaps)}")
    return na("no hero numbers in 2D") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-N10", scope="image")
def stacked_aligned(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    nums = [e for e in sl.elements if re.fullmatch(r"[−\-]?\$?\d[\d,.]*[KMB%]?", e["text"].strip())]
    stacks = [(a, b) for a in nums for b in nums if a is not b and abs(a["box"]["x"] + a["box"]["w"] - b["box"]["x"] - b["box"]["w"]) < 60 and a["box"]["y"] < b["box"]["y"]]
    if not stacks:
        return na("no stacked amounts")
    bad = [f"{a['text']}/{b['text']}" for a, b in stacks if abs(a["box"]["x"] + a["box"]["w"] - b["box"]["x"] - b["box"]["w"]) > 3 * sl.scale * 2]
    return ok(not bad, ", ".join(bad))


@check("T-N18", scope="post")
def digits_distinct(post):
    fam = None
    for sl in post.slides:
        if sl.manifest:
            nums = [e for e in sl.elements if re.search(r"\d", e["text"])]
            if nums:
                e = min(nums, key=lambda e: e["size"])
                fam = (post, sl, e)
                break
    if not fam:
        return na("needs the manifest")
    post_, sl, e = fam
    fm = font_metrics(post_, sl, e["family"], e["weight"])
    if not fm:
        return na("font file not found")
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(fm["path"], max(12, int(e["size"])))
    def glyph(ch):
        im = Image.new("L", (int(e["size"] * 1.2), int(e["size"] * 1.4)), 0)
        ImageDraw.Draw(im).text((4, 4), ch, fill=255, font=font)
        return np.asarray(im) > 127
    worst = 0
    for a, b in ("38", "56", "68", "17", "08"):
        A, B = glyph(a), glyph(b)
        iou = (A & B).sum() / max(1, (A | B).sum())
        worst = max(worst, iou)
    return ok(worst <= 0.85, f"max IoU {worst:.2f}", round(float(worst), 3))


@check("T-N19", scope="post")
def no_mono_numbers(post):
    for sl in post.slides:
        if not sl.manifest:
            return na("needs the manifest")
    seen = set()
    bad = []
    for sl in post.slides:
        for e in sl.elements:
            if re.search(r"\d", e["text"]) and (e["family"], e["weight"]) not in seen:
                seen.add((e["family"], e["weight"]))
                fm = font_metrics(post, sl, e["family"], e["weight"])
                if fm and fm["fixed"]:
                    bad.append(e["family"])
    return ok(not bad, ", ".join(bad))


# ------------------------------------------------------------------ delivery: JPEG and recompression (T-J)
def text_edge_profile(img, mask):
    g = gray(img).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1)
    mag = np.hypot(gx, gy)
    from scipy import ndimage as ndi
    edge = ndi.binary_dilation(mask, iterations=1) & ~ndi.binary_erosion(mask, iterations=1)
    return mag, edge


@check("T-J04", scope="image")
def edge_sharpness(post, sl):
    if not sl.has("mask"):
        return need(sl, "mask")
    sim = sl.sim1
    m = resize_area(sl.text_mask.astype(np.uint8) * 255, sim.shape[1], sim.shape[0]) > 127
    g = gray(sim)
    # 10-90% rise: sample horizontal profiles across vertical glyph edges
    rises = []
    ys, xs = np.nonzero(m[:, 1:] & ~m[:, :-1])
    for y, x in list(zip(ys, xs))[::25][:600]:
        prof = g[y, max(0, x - 6):x + 7]
        if len(prof) < 13:
            continue
        lo, hi = prof.min(), prof.max()
        if hi - lo < 40:
            continue
        p = (prof - lo) / (hi - lo)
        dec = p[0] > p[-1]
        p = p[::-1] if not dec else p
        i10 = np.argmax(p < 0.9)
        i90 = np.argmax(p < 0.1)
        rises.append(max(0, i90 - i10))
    if not rises:
        return na("no glyph edges")
    med = float(np.median(rises))
    return ok(med <= 2.0, f"median 10-90% rise {med:.1f}px at 1080", med)


@check("T-J05", scope="image")
def ringing(post, sl):
    if not sl.has("mask"):
        return need(sl, "mask")
    ref = resize_area(sl.final, 1080, round(sl.final.shape[0] * 1080 / sl.final.shape[1]))
    sim = sl.sim1
    m = resize_area(sl.text_mask.astype(np.uint8) * 255, 1080, ref.shape[0]) > 60
    from scipy import ndimage as ndi
    ring = ndi.binary_dilation(m, iterations=4) & ~ndi.binary_dilation(m, iterations=1)
    d = np.abs(lstar(sim[ring]) - lstar(ref[ring]))
    p95 = float(np.percentile(d, 95)) if d.size else 0
    return ok(p95 <= 6, f"P95 dL* {p95:.1f}", round(p95, 2))


@check("T-J06", scope="image")
def chroma_bleed(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    colored = [r for r in rows if math.hypot(*to_lab(r["text_rgb"][None, None, :])[0, 0][1:]) > 20]
    if not colored:
        return na("no colored text")
    ref = resize_area(sl.final, 1080, round(sl.final.shape[0] * 1080 / sl.final.shape[1]))
    sim = sl.sim1
    from scipy import ndimage as ndi
    worst = 0
    for r in colored:
        m = resize_area(r["mask"].astype(np.uint8) * 255, 1080, ref.shape[0]) > 127
        ring = ndi.binary_dilation(m, iterations=2) & ~m
        if ring.sum() < 10:
            continue
        d = float(np.mean(de00(to_lab(sim[ring][None]), to_lab(ref[ring][None]))))
        worst = max(worst, d)
    return ok(worst <= 4, f"mean dE00 in the ring {worst:.1f}", round(worst, 2))


@check("T-J07", scope="image")
def thin_strokes_two_encodes(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    from scipy import ndimage as ndi
    s2 = sl.sim2
    k = s2.shape[1] / sl.final.shape[1]
    bad = []
    for r in measure(sl):
        if r["ink"] < 50 or not r.get("h") or r["h"] * k < 9:
            continue
        b = r["el"]["box"]
        x0, y0, x1, y1 = int(b["x"] * k), int(b["y"] * k), int((b["x"] + b["w"]) * k) + 1, int((b["y"] + b["h"]) * k) + 1
        crop = gray(s2[y0:y1, x0:x1])
        bgv, tv = np.percentile(crop, 95), np.percentile(crop, 5)
        thr = (bgv + tv) / 2
        m2 = crop < thr if r.get("dark_text", True) else crop > thr
        n2 = ndi.label(m2, structure=np.ones((3, 3)))[1]
        n1 = sum(len([c for c in l["comps"] if c["area"] > 3 / (k * k)]) for l in r["lines"])
        if n2 < 0.85 * n1:
            bad.append(f"{r['el']['text'][:16]} {n1}->{n2}")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-J08", scope="image")
def blocking(post, sl):
    sim = sl.sim1
    g = gray(sim).astype(np.float32)
    dx = np.abs(np.diff(g, axis=1))
    cols = np.arange(dx.shape[1])
    bnd = dx[:, (cols % 8) == 7].mean()
    inner = dx[:, (cols % 8) != 7].mean()
    v = float(bnd / max(inner, 1e-3))
    return ok(v <= 1.3, f"block-boundary ratio {v:.2f}", round(v, 3))


@check("T-J09", scope="image")
def grayscale_aa(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    from scipy import ndimage as ndi
    bad = []
    for r in rows:
        lab = to_lab(r["text_rgb"][None, None, :])[0, 0]
        blab = to_lab(np.asarray(r["bg_rgb"])[None, None, :])[0, 0]
        if math.hypot(lab[1], lab[2]) > 6 or math.hypot(blab[1], blab[2]) > 6:
            continue
        edge = ndi.binary_dilation(r["mask"], iterations=1) & ~ndi.binary_erosion(r["mask"], iterations=1)
        if edge.sum() < 10:
            continue
        L = to_lab(sl.final[edge][None])[0]
        c = np.hypot(L[:, 1], L[:, 2])
        if np.percentile(c, 95) > 6:
            bad.append(f"{r['el']['text'][:16]} C* {np.percentile(c, 95):.1f}")
    return ok(not bad, "; ".join(bad[:4])) if rows else na("no text")


@check("T-J10", scope="image")
def sharpening_halos(post, sl):
    rows = contrast_rows(sl)
    if rows is None:
        return need(sl, "bg")
    from scipy import ndimage as ndi
    worst = 0
    for r in rows:
        if not r.get("dark_text"):
            continue
        ring = ndi.binary_dilation(r["mask"], iterations=3) & ~ndi.binary_dilation(r["mask"], iterations=1)
        if ring.sum() < 10:
            continue
        over = lstar(sl.final[ring]) - lstar(sl.img("bg")[ring])
        worst = max(worst, float(np.percentile(over, 99)))
    return ok(worst <= 3, f"P99 overshoot {worst:.1f} L*", round(worst, 2))


# ------------------------------------------------------------------ OCR integrity (T-O)
def ocr_crop(img, box, k=1.0, pad=10, psm=6, whitelist=None):
    x0, y0 = max(0, int(box["x"] * k - pad)), max(0, int(box["y"] * k - pad))
    x1, y1 = int((box["x"] + box["w"]) * k + pad), int((box["y"] + box["h"]) * k + pad)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return ""
    if crop.shape[0] < 40:
        f = 40 / crop.shape[0]
        crop = cv2.resize(crop, (int(crop.shape[1] * f), 40), interpolation=cv2.INTER_CUBIC)
    words = ocr(crop, psm, whitelist)
    return None if words is None else " ".join(w["text"] for w in words)


def ocr_variants(img, box, k=1.0, pad=10, psms=(7, 6), whitelist=None):
    """OCR reads of one box under a few preprocessings (as-is, x2, Otsu on L*, Otsu on chroma), yielded lazily so a
    caller stops at the first right read. A box counts as legible when any read is right: Tesseract misses clean text
    often enough that a single read is not a measurement. Yields nothing when tesseract is missing."""
    x0, y0 = max(0, int(box["x"] * k - pad)), max(0, int(box["y"] * k - pad))
    x1, y1 = int((box["x"] + box["w"]) * k + pad), int((box["y"] + box["h"]) * k + pad)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return

    def variants():
        yield crop
        lab = to_lab(crop)
        for ch in ((lab[..., 0] * 2.55).astype(np.uint8), (255 - np.clip(np.hypot(lab[..., 1], lab[..., 2]) * 3, 0, 255)).astype(np.uint8)):
            _, th = cv2.threshold(ch, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            if (th == 0).mean() > 0.5:
                th = 255 - th
            yield np.stack([th] * 3, -1)
    for v in variants():
        for f in ((1.0, 2.0) if v.shape[0] < 120 else (1.0, 0.5)):
            vv = cv2.resize(v, (max(1, int(v.shape[1] * f)), max(1, int(v.shape[0] * f))), interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)
            for psm in psms:
                words = ocr(vv, psm, whitelist)
                if words is None:
                    return
                yield " ".join(w["text"] for w in words)


def norm_cmp(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-").replace("·", " ").replace("×", "x").replace("÷", "/")
    s = re.sub(r"\s*/\s*", "/", re.sub(r"\s+-\s+", " ", s.lower()))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9$%.,'/ -]", "", s)).strip(" .")


@check("T-O01", scope="image")
def exact_after_delivery(post, sl):
    if not sl.manifest:
        if not sl.text:
            return need(sl, "manifest")
        got = " ".join(w["text"] for w in (ocr(sl.sim1, 11) or []))
        r = word_match_rate(sl.text, got)
        return estimate(r >= 0.98, f"{r:.0%} of slide words read at 1080 (OCR of the whole image)")
    bad, n = [], 0
    k = sl.sim1.shape[1] / sl.final.shape[1]
    for e in sl.elements:
        if e["role"] not in ("headline", "source", "footnote", "cta", "footer", "counter") and not re.search(r"\d", e["text"]) and "svpay.app" not in e["text"]:
            continue
        if (e.get("size") or 0) * k < 9:
            continue
        n += 1
        want = norm_cmp(e["text"])
        if not ocr_ok():
            return na("needs OCR")
        first = []
        def seen(g):
            first.append(g)
            return norm_cmp(g) == want
        if any(seen(r) for r in ocr_variants(sl.sim1, e["box"], k, pad=8, psms=(6, 7) if len(e["lines"]) > 1 else (7, 8, 10) if len(e["text"]) <= 3 else (7,))):
            continue
        reads = first
        # multi-line blocks: line by line
        per_line = []
        for l in e["lines"]:
            rr = []
            hit = next((r for r in ocr_variants(sl.sim1, l, k, pad=8, psms=(7,)) if rr.append(r) or norm_cmp(r) == norm_cmp(l["text"])), None)
            per_line.append(hit if hit is not None else (rr[0] if rr else ""))
        if norm_cmp(" ".join(per_line)) != want:
            bad.append(f"'{e['text'][:28]}' read '{(reads[0] if reads else '')[:28]}'")
    return ok(not bad, " | ".join(bad[:4]), len(bad)) if n else na("no headline, number, CTA or source text")


@check("T-O03", scope="image")
def digits_read(post, sl):
    nums = []
    if sl.manifest:
        for e in sl.elements:
            for m in re.finditer(r"\$?\d[\d,]*(?:\.\d+)?%?", e["text"]):
                if len(re.sub(r"\D", "", m.group(0))) >= 1 and (e["size"] >= 40 or len(m.group(0)) >= 3):
                    nums.append((e, m.group(0)))
    bad = []
    for e, tok in nums:
        line = next((l for l in e["lines"] if tok in l["text"]), e["box"])
        if not ocr_ok():
            return na("needs OCR")
        want = re.sub(r"[^\d.]", "", tok)
        reads = []
        if not any(reads.append(g) or want in re.sub(r"[^\d.]", "", g) for g in ocr_variants(sl.final, line, 1.0, pad=10, psms=(7,), whitelist="0123456789$%.,")):
            bad.append(f"{tok} read '{(reads[0] if reads else '')[:20]}'")
    # 3D numerals: digits-only read of the numbers on the final image
    from .threed import number_groups
    for txt, os_ in number_groups(sl) if sl.scene else []:
        if not re.search(r"\d", txt):
            continue
        ps = [p for o in os_ for p in sl.parts() if p["object"] == o["index"]]
        x0 = min(p["px_box"][0] for p in ps); y0 = min(p["px_box"][1] for p in ps)
        x1 = max(p["px_box"][2] for p in ps); y1 = max(p["px_box"][3] for p in ps)
        box = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
        reads = []
        want = re.sub(r"[^\d]", "", txt)
        def gen():
            for sc in (3, 4):
                small = resize_area(sl.final, sl.size[0] // sc, sl.size[1] // sc)
                yield from ocr_variants(small, box, 1.0 / sc, pad=10, psms=(7,), whitelist="0123456789$%.,BMK")
        # a letter suffix ($1.62B) must not be read as a digit: compare the digits of the numeric part only
        if not any(reads.append(g) or re.sub(r"[^\d]", "", re.sub(r"[BMK]+$", "", g.strip())) == want for g in gen()):
            bad.append(f"3D {txt} read {sorted(set(reads))[:3]}")
    if not sl.manifest and not sl.objects():
        if not sl.text:
            return need(sl, "manifest")
        got = " ".join(w["text"] for w in (ocr(sl.final, 11) or []))
        want = re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?", sl.text)
        miss = [w for w in want if w not in got]
        return estimate(not miss, "OCR of the whole image: " + ", ".join(miss))
    if not nums and not sl.objects():
        return na("no numbers")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-O04", scope="image")
def ocr_confidence(post, sl):
    words = sl.ocr_words("final")
    if words is None:
        return na("needs OCR")
    if not sl.manifest:
        return na("needs the manifest")
    boxes = [e["box"] for e in sl.elements if e["role"] in BODY + ("headline",)]
    inside = [w for w in words if any(b["x"] - 10 <= w["x"] and w["x"] + w["w"] <= b["x"] + b["w"] + 10 and b["y"] - 10 <= w["y"] and w["y"] + w["h"] <= b["y"] + b["h"] + 10 for b in boxes)]
    low = [w["text"] for w in inside if w["conf"] < 85 and len(w["text"]) > 1]
    return ok(len(low) <= max(1, len(inside) // 20), ", ".join(low[:6]), len(low))


@check("T-O05", scope="image")
def clipped_glyphs(post, sl):
    if not sl.has("mask", "unclipped", "manifest"):
        if sl.has("manifest"):
            return need(sl, "mask", "unclipped")
        words = sl.ocr_words("final")
        if words is None:
            return need(sl, "mask", "unclipped")
        W, H = sl.size
        edge = [w["text"] for w in words if w["conf"] > 50 and (w["x"] <= 2 or w["y"] <= 2 or w["x"] + w["w"] >= W - 2 or w["y"] + w["h"] >= H - 2)]
        return estimate(not edge, "OCR words touching the slide edge: " + ", ".join(edge))
    m = sl.text_mask
    u = gray(sl.img("unclipped")) < 128
    lost = u & ~m
    from scipy import ndimage as ndi
    lost = ndi.binary_opening(lost, iterations=1)
    bad = []
    if lost.sum() > 8:
        ys, xs = np.nonzero(lost)
        bad.append(f"{int(lost.sum())} ink px clipped near ({int(xs.mean())},{int(ys.mean())})")
    W, H = sl.size
    for e in sl.elements:
        for l in e["lines"]:
            if l["x"] < 0 or l["y"] < 0 or l["x"] + l["w"] > W or l["y"] + l["h"] > H:
                bad.append(f"line past the slide edge: {l['text'][:20]}")
        c = e.get("clip")
        if c and c["w"] > 0:
            em = sl.element_mask(e)
            x0, y0, x1, y1 = int(c["x"]), int(c["y"]), int(c["x"] + c["w"]), int(c["y"] + c["h"])
            band = np.zeros_like(em)
            band[max(0, y0):max(0, y0) + 1, :] = True
            band[min(H - 1, y1 - 1), :] = True
            band[:, max(0, x0)] = True
            band[:, min(W - 1, x1 - 1)] = True
            if (em & band).sum() > 2 and (x0 > 0 or y0 > 0 or x1 < W or y1 < H):
                bad.append(f"glyph on its clip edge: {e['text'][:20]}")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-O06", scope="image")
def card_padding(post, sl):
    if not sl.has("mask", "manifest"):
        return need(sl, "mask")
    bad, n = [], 0
    for r in measure(sl):
        p = panel_of(sl, r["el"]["box"])
        if not p or not r.get("comps") or p["h"] < 100:
            continue
        n += 1
        H = r.get("H") or r.get("h") or 20
        need_px = max(48 * sl.scale, 0.75 * H)
        x0 = min(c["x0"] for c in r["comps"]) - p["x"]
        x1 = p["x"] + p["w"] - max(c["x1"] for c in r["comps"])
        y0 = min(c["y0"] for c in r["comps"]) - p["y"]
        y1 = p["y"] + p["h"] - max(c["y1"] for c in r["comps"])
        if min(x0, x1, y0, y1) < need_px * 0.5 and min(x0, x1) < need_px:
            bad.append(f"{r['el']['text'][:18]} {min(x0, x1, y0, y1):.0f}px")
    return na("no text in cards") if not n else ok(not bad, "; ".join(bad[:4]))


@check("T-O08", scope="image")
def stray_text(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    words = sl.ocr_words("final")
    if words is None:
        return na("needs OCR")
    boxes = [e["box"] for e in sl.elements] + [{"x": i["x"], "y": i["y"], "w": i["w"], "h": i["h"]} for i in sl.manifest.get("images", []) if "hero" not in i["cls"]]
    nums3d = [{"x": p["px_box"][0], "y": p["px_box"][1], "w": p["px_box"][2] - p["px_box"][0], "h": p["px_box"][3] - p["px_box"][1]} for p in sl.parts()]
    def inside(w):
        cx, cy = w["x"] + w["w"] / 2, w["y"] + w["h"] / 2
        return any(b["x"] - 30 <= cx <= b["x"] + b["w"] + 30 and b["y"] - 30 <= cy <= b["y"] + b["h"] + 30 for b in boxes + nums3d)
    stray = [w["text"] for w in words if w["conf"] >= 60 and len(re.sub(r"\W", "", w["text"])) >= 2 and not inside(w)]
    return ok(not stray, ", ".join(stray[:6]), len(stray))


LEAKS = [r"\{\{", r"\}\}", r"\$\{", r"\bundefined\b", r"\bnull\b", r"\bNaN\b", r"\[object", r"\blorem\b", r"\bipsum\b", r"\bTODO\b", r"\bTBD\b", r"\bXXX\b",
         r"\bHeadline here\b", r"\bplaceholder\b", r"\bInsert\b", r"\[(name|company|date|link)\]"]


@check("T-O09", scope="image")
def template_leaks(post, sl):
    t = "\n".join(e["text"] for e in sl.elements) if sl.manifest else (sl.text or "")
    o = sl.ocr_all_text()
    hay = t + "\n" + (o or "")
    hits = [p for p in LEAKS if re.search(p, hay, re.I if p not in (r"\bNaN\b", r"\bTODO\b", r"\bTBD\b", r"\bXXX\b") else 0)]
    if not sl.manifest and not hits:
        return estimate(True, "slide text and OCR checked without the manifest")
    return ok(not hits, ", ".join(hits))


@check("T-O10", scope="image")
def fallback_glyphs(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    fams = {f["family"] for f in sl.manifest["fonts"]["files"]}
    bad = []
    for e in sl.elements:
        pf = e.get("platform_fonts")
        if pf is None:
            continue
        for f in pf:
            if f["familyName"] not in fams and f.get("glyphCount", 0) > 0:
                bad.append(f"{e['text'][:16]} uses {f['familyName']} ({f['glyphCount']} glyphs)")
    if any(e.get("platform_fonts") is None for e in sl.elements) and not bad:
        return Outcome(NEEDS, "needs passes: the manifest has no platform-font report (re-render with render.js --passes)")
    if sl.manifest["fonts"]["status"] != "loaded":
        bad.append("fonts not loaded at capture")
    return ok(not bad, "; ".join(bad[:4]))


@check("T-O11", scope="image")
def elements_present(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    bad = []
    for e in sl.elements:
        if e["role"] == "counter" or len(e["text"]) < 3:
            continue
        if not ocr_ok():
            return na("needs OCR")
        r = 0.0
        for g in ocr_variants(sl.final, e["box"], 1.0, pad=8, psms=(6, 7) if len(e["lines"]) > 1 else (7, 8)):
            r = max(r, word_match_rate(e["text"], g))
            if r >= 0.9:
                break
        if r < 0.9:
            best = []
            for l in e["lines"]:
                bl = ""
                for g in ocr_variants(sl.final, l, 1.0, pad=8, psms=(7,)):
                    if word_match_rate(l["text"], g) > word_match_rate(l["text"], bl):
                        bl = g
                    if word_match_rate(l["text"], g) >= 1.0:
                        break
                best.append(bl)
            r = max(r, word_match_rate(e["text"], " ".join(best)))
        if r < 0.9:
            bad.append(f"'{e['text'][:24]}' {r:.0%}")
    return ok(not bad, " | ".join(bad[:4]), len(bad))


# ------------------------------------------------------------------ grid and feed sizes (F-IG36, F-TN01, F-TN06, F-X03, T-P)
def headline_read_at(post, sl, crop_box, w, h):
    img = sl.final
    if crop_box:
        x0, y0, x1, y1 = crop_box
        img = img[y0:y1, x0:x1]
    small = resize_area(img, w, h)
    big = cv2.resize(small, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
    words = ocr(big, 11)
    if words is None:
        return None
    from .words import headline
    hl, _ = headline(sl)
    return word_match_rate(hl, " ".join(x["text"] for x in words))


@check("F-IG36", scope="post")
def grid_tile(post):
    if not post.slides or "instagram" not in post.nets:
        return na("no Instagram carousel")
    r = headline_read_at(post, post.slides[0], (68, 0, 2092, 2700), 390, 520)
    return na("needs OCR") if r is None else ok(r >= 1.0, f"{r:.0%} of headline words read in the grid tile", round(r, 2))


@check("F-TN01", scope="post")
def feed_30(post):
    if not post.slides:
        return na("no slides")
    r = headline_read_at(post, post.slides[0], None, 648, 810)
    return na("needs OCR") if r is None else ok(r >= 1.0, f"{r:.0%} of headline words at 30%", round(r, 2))


@check("F-TN06", scope="post")
def linkedin_552(post):
    if "linkedin" not in post.nets or not post.media("linkedin"):
        return na("no LinkedIn image")
    sl = post.media("linkedin")[0]
    r = headline_read_at(post, sl, None, 552, 690)
    return na("needs OCR") if r is None else ok(r >= 1.0, f"{r:.0%} of headline words at 552px", round(r, 2))


@check("F-X03", scope="post")
def multi_image_text(post):
    ms = [s for n in ("x", "threads", "facebook") for s in post.media(n) if len(post.media(n)) > 1]
    if not ms:
        return na("no multi-image post")
    bad = []
    for s in ms:
        if not s.manifest:
            return na("needs the manifest")
        small = min(e["size"] for e in s.elements if e["role"] not in ("footer", "counter"))
        if small < 150:
            bad.append(f"{s.where} {small:.0f}px")
    return ok(not bad, ", ".join(sorted(set(bad))))


def text_coverage(sl, dil=0.25):
    words = sl.ocr_words("final")
    if words is None:
        return None
    m = np.zeros(sl.final.shape[:2], bool)
    for w in words:
        if w["conf"] < 40:
            continue
        d = int(dil * w["h"])
        m[max(0, w["y"] - d):w["y"] + w["h"] + d, max(0, w["x"] - d):w["x"] + w["w"] + d] = True
    return float(m.mean())


@check("T-P01", scope="image")
def ad_coverage(post, sl):
    if not post.pkg.get("promoted"):
        return na("not promoted as a Meta ad")
    v = text_coverage(sl)
    return na("needs OCR") if v is None else ok(v <= 0.20, f"{v:.0%}", round(v, 3))


@check("T-P02", scope="post")
def hook_coverage(post):
    if not post.slides:
        return na("no slides")
    v = text_coverage(post.slides[0])
    return na("needs OCR") if v is None else ok(v <= 0.30, f"{v:.0%} of slide 1 is text", round(v, 3))


@check("F-GB05", scope="image")
def gbp_text_area(post, sl):
    if sl not in post.media("gbp"):
        return na("not a GBP image")
    v = text_coverage(sl, 0)
    return na("needs OCR") if v is None else ok(v <= 0.10, f"{v:.0%}", round(v, 3))


# ------------------------------------------------------------------ fonts (T-F)
@check("T-F01", scope="image")
def approved_families(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    ok_fams = {"Inter Tight", "Inter", "Instrument Serif"}
    used = {f["familyName"] for e in sl.elements for f in (e.get("platform_fonts") or [])} or {e["family"].split(",")[0].strip('" ') for e in sl.elements}
    bad = sorted(u for u in used if u not in ok_fams)
    return ok(not bad, ", ".join(bad))


@check("T-F02", scope="post")
def no_novelty_faces(post):
    seen, bad = set(), []
    for sl in post.slides:
        if not sl.manifest:
            return na("needs the manifest")
        for e in sl.elements:
            k = (e["family"], e["weight"])
            if k in seen:
                continue
            seen.add(k)
            fm = font_metrics(post, sl, e["family"], e["weight"])
            if fm and (fm["panose"] in (3, 4) or fm["fixed"]):
                bad.append(e["family"])
    return ok(not bad, ", ".join(bad))


@check("T-F03", scope="post")
def large_xheight_face(post):
    for sl in post.slides:
        if sl.manifest:
            bodies = [e for e in sl.elements if e["role"] in BODY]
            if bodies:
                fm = font_metrics(post, sl, bodies[0]["family"], bodies[0]["weight"])
                if fm and fm["xh"]:
                    v = fm["xh"] / fm["upm"]
                    return ok(v >= 0.50, f"x-height ratio {v:.3f}", round(v, 3))
    return na("needs the manifest and the body font file")


@check("T-F04", scope="post")
def glyph_coverage(post):
    for sl in post.slides:
        if sl.manifest:
            bodies = [e for e in sl.elements if e["role"] in BODY]
            if bodies:
                fm = font_metrics(post, sl, bodies[0]["family"], bodies[0]["weight"])
                if fm:
                    cmap = fm["font"].getBestCmap()
                    need_cp = [0x2212, 0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2026, 0xD7, 0xA2]
                    miss = [hex(c) for c in need_cp if c not in cmap]
                    gsub = "GSUB" in fm["font"]
                    feats = set()
                    if gsub:
                        feats = {fr.FeatureTag for fr in fm["font"]["GSUB"].table.FeatureList.FeatureRecord}
                    lacks = [f for f in ("tnum", "lnum") if f not in feats]
                    return ok(not miss and not lacks, f"missing {miss} {lacks}")
    return na("needs the manifest and the body font file")


@check("T-F05", scope="post")
def il1_distinct(post):
    for sl in post.slides:
        if sl.manifest:
            bodies = [e for e in sl.elements if e["role"] in BODY]
            if not bodies:
                continue
            e = bodies[0]
            fm = font_metrics(post, sl, e["family"], e["weight"])
            if not fm:
                continue
            from PIL import Image, ImageDraw, ImageFont
            font = ImageFont.truetype(fm["path"], int(e["size"]))
            def g(ch):
                im = Image.new("L", (int(e["size"] * 1.2), int(e["size"] * 1.4)), 0)
                ImageDraw.Draw(im).text((4, 4), ch, fill=255, font=font)
                return np.asarray(im) > 127
            iou = lambda a, b: (g(a) & g(b)).sum() / max(1, (g(a) | g(b)).sum())
            worst_il = max(iou("I", "l"), iou("I", "1"), iou("l", "1"))
            o0 = iou("0", "O")
            return ok(worst_il <= 0.80 and o0 <= 0.90, f"I/l/1 {worst_il:.2f}, 0/O {o0:.2f}")
    return na("needs the manifest and the body font file")


@check("T-F06", scope="image")
def no_synthesized(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    have = {(f["family"], str(f["weight"]), f["style"]) for f in sl.manifest["fonts"]["files"]}
    bad = [f"{r['family'].split(',')[0]} {r['weight']} {r['style']}" for e in sl.elements for r in e.get("runs", [])
           if (r["family"].split(",")[0].strip('" '), str(r["weight"]), r["style"]) not in have]
    return ok(not bad, ", ".join(sorted(set(bad))))


@check("T-F08", scope="image")
def paired_faces(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    mixed = [e for e in sl.elements if len({r["family"] for r in e.get("runs", [])}) > 1]
    return na("no line mixes two families") if not mixed else ok(False, "two families on one line: " + ", ".join(e["text"][:20] for e in mixed))


@check("T-F09", scope="image")
def weight_bands(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    bad = []
    for e in sl.elements:
        w = e["weight"]
        if e["role"] in BODY + SPOT and not (400 <= w <= 600) and not (e["role"] in ("label", "counter", "footer") and w <= 700):
            bad.append(f"{e['text'][:16]} {w}")
        if e["role"] == "headline" and not (600 <= w <= 800):
            bad.append(f"{e['text'][:16]} {w}")
    if post.slides and sl is post.slides[0]:
        hs = [e for e in sl.elements if e["role"] == "headline"]
        bad += [f"slide-1 headline {e['weight']}" for e in hs if e["weight"] < 600]
    return ok(not bad, "; ".join(bad[:4]))


@check("T-F10", scope="image")
def no_distortion(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    bad = []
    for e in sl.elements:
        for r in e.get("runs", []):
            t = r.get("transform") or "none"
            m = re.match(r"matrix\(([-\d.e]+), ([-\d.e]+), ([-\d.e]+), ([-\d.e]+)", t)
            if m:
                a, b, c, d = map(float, m.groups())
                if abs(b) > 1e-3 or abs(c) > 1e-3 or abs(a - d) / max(abs(a), 1e-6) > 0.005:
                    bad.append(e["text"][:16])
    return ok(not bad, ", ".join(bad))


@check("T-F11", scope="image")
def no_fake_3d_type(post, sl):
    if not sl.manifest:
        return need(sl, "manifest")
    bad = [e["text"][:16] for e in sl.elements for r in e.get("runs", []) if (r.get("stroke") not in (None, "0px")) or (r.get("text_shadow") or "none").count("px") >= 6]
    return ok(not bad, ", ".join(bad))


@check("T-F12", scope="post")
def second_family_one_job(post):
    roles = {}
    for sl in post.slides:
        if not sl.manifest:
            return na("needs the manifest")
        for e in sl.elements:
            for r in e.get("runs", []):
                roles.setdefault(r["family"].split(",")[0].strip('" '), set()).add(e["role"])
    fams = sorted(roles, key=lambda f: -len(roles[f]))
    extra = [f for f in fams[2:]]
    second = fams[1] if len(fams) > 1 else None
    serif = [f for f in fams if "Serif" in f]
    bad = [f"{f}: {sorted(roles[f])}" for f in serif if len(roles[f] - {"headline"}) > 1]
    return ok(not bad, "; ".join(bad))


@check("F-IG17", scope="post")
def headline_top_same(post):
    tops = []
    for sl in post.slides[1:-1]:
        if not sl.manifest:
            return na("needs the manifest")
        hs = [e for e in sl.elements if e["role"] == "headline"]
        if hs:
            tops.append(min(e["box"]["y"] for e in hs))
    if len(tops) < 2:
        return na("fewer than 2 content slides")
    return ok(max(tops) - min(tops) <= 16, f"headline tops {min(tops):.0f}-{max(tops):.0f}px", round(max(tops) - min(tops)))


@check("F-IG18", scope="post")
def headline_color_same(post):
    cols = []
    for sl in post.slides:
        rows = contrast_rows(sl, ("headline",))
        if rows is None:
            return na("needs passes")
        solid = [r for r in rows if not any(x.get("gradient_text") for x in r["el"].get("runs", []))]
        if solid:
            cols.append(to_lab(solid[0]["text_rgb"][None, None, :])[0, 0])
    if len(cols) < 2:
        return na("fewer than 2 headlines")
    d = max(float(de00(a, b)) for a in cols for b in cols)
    return ok(d <= 2, f"max dE00 {d:.1f}", round(d, 2))
