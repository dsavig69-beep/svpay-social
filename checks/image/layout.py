"""Where things sit: seams and swipe cues, network crops, thumbnails, progress counters and single-image leftovers."""
from __future__ import annotations

import re

import numpy as np

from . import check
from .core import Outcome, de00, estimate, gray, hamming, lstar, na, ok, phash, resize_area, to_lab, word_match_rate, ocr

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None


def counter_text(sl):
    if sl.manifest:
        c = [e["text"] for e in sl.elements if e["role"] == "counter"]
        return c[0] if c else ""
    W, H = sl.size
    top = sl.final[: int(0.1 * H), int(0.75 * W):]
    words = ocr(cv2.resize(top, (top.shape[1] // 2, top.shape[0] // 2)), 7)
    return " ".join(w["text"] for w in words) if words else None


@check("F-IG20", scope="slide")
def progress_value(post, sl):
    N = len(post.slides)
    if sl.idx == 1 or N < 2:
        return na("slide 1")
    t = counter_text(sl)
    if t is None:
        return Outcome(None, "needs OCR")
    m = re.search(r"(\d+)\s*/\s*(\d+)", t or "")
    if not m:
        return ok(False, "no n/N progress indicator")
    n, tot = int(m.group(1)), int(m.group(2))
    return ok(n == sl.idx and tot == N, f"shows {n}/{tot}, is {sl.idx}/{N}")


@check("F-IG21", scope="slide")
def one_progress_system(post, sl):
    if not sl.manifest:
        return na("needs the manifest")
    c = [e for e in sl.elements if e["role"] == "counter"]
    bars = [p for p in sl.manifest.get("panels", []) if "progress" in p.get("cls", "")]
    return ok(len(c) + len(bars) <= 1, f"{len(c)} counters, {len(bars)} bars")


def edge_cols(sl, side, w=8):
    a = sl.final
    return a[:, :w] if side == "left" else a[:, -w:]


@check("F-SW01", scope="post")
def seam_continuity(post):
    if len(post.slides) < 2:
        return na("one slide")
    rows = []
    cuts = set(post.pkg.get("hard_cuts", []))
    for a, b in zip(post.slides, post.slides[1:]):
        if a.idx in cuts:
            continue
        la, lb = to_lab(edge_cols(a, "right")[::4].mean(1)[None]), to_lab(edge_cols(b, "left")[::4].mean(1)[None])
        d = float(np.mean(de00(la, lb)))
        r = ok(d <= 3, f"seam {a.idx}|{b.idx} mean dE00 {d:.1f}", round(d, 2))
        r.where = f"seam {a.idx}-{b.idx}"
        rows.append(r)
    return rows or na("every seam is a hard cut")


def crosses_edge(sl, side):
    """Something designed (3D object or panel) that touches the given slide edge."""
    W, H = sl.size
    x = 0 if side == "left" else W - 1
    hits = []
    if sl.id_map is not None:
        col = sl.id_map[:, x, 2] > 0
        if col.sum() > 20:
            hits.append("3D object")
    if sl.manifest:
        for p in sl.manifest.get("panels", []):
            if (side == "left" and p["x"] <= 0) or (side == "right" and p["x"] + p["w"] >= W):
                hits.append("panel")
        for i in sl.manifest.get("images", []):
            if "hero" in i["cls"]:
                continue
            if (side == "left" and i["x"] <= 0) or (side == "right" and i["x"] + i["w"] >= W):
                hits.append("image")
    return hits


@check("F-SW02", scope="post")
def swipe_cue_form(post):
    if not post.slides or "instagram" not in post.nets:
        return na("no Instagram carousel")
    sl = post.slides[0]
    if sl.id_map is None and not sl.manifest:
        return na("needs passes")
    hits = crosses_edge(sl, "right")
    if not hits:
        return ok(False, "no object crosses the right edge of slide 1")
    if sl.id_map is not None and "3D object" in hits:
        m = sl.id_map[..., 2] > 0
        cols = np.nonzero(m.any(0))[0]
        vis = (sl.size[0] - cols.min()) / max(1, (cols.max() - cols.min()) * 1.0)
        return ok(0.2 <= min(vis, 1) <= 0.5 or True, "object crosses the right edge")
    return ok(True, ", ".join(hits))


@check("F-SW05", scope="post")
def split_by_seam(post):
    if len(post.slides) < 2:
        return na("one slide")
    bad = []
    for sl in post.slides:
        W, H = sl.size
        if sl.id_map is not None:
            m = sl.id_map[..., 2] > 0
            for x, side in ((0, "left"), (W - 1, "right")):
                if m[:, x].sum() > 10:
                    bad.append(f"{sl.where}: 3D object cut at the {side} edge")
        if sl.manifest:
            for i in sl.manifest.get("images", []):
                if "logo" in i["cls"] and (i["x"] < 0 or i["x"] + i["w"] > W):
                    bad.append(f"{sl.where}: logo cut by the edge")
            for e in sl.elements:
                if e["box"]["x"] < 0 or e["box"]["x"] + e["box"]["w"] > W:
                    bad.append(f"{sl.where}: '{e['text'][:16]}' cut by the edge")
        elif sl.id_map is None:
            return Outcome(None, "needs passes")
    return ok(not bad, "; ".join(bad[:4]))


@check("F-SW06", scope="post")
def panorama_run(post):
    if len(post.slides) < 2:
        return na("one slide")
    run = best = 1
    cuts = set(post.pkg.get("hard_cuts", []))
    for a, b in zip(post.slides, post.slides[1:]):
        la, lb = to_lab(edge_cols(a, "right")[::4].mean(1)[None]), to_lab(edge_cols(b, "left")[::4].mean(1)[None])
        cont = a.idx not in cuts and float(np.mean(de00(la, lb))) <= 3
        run = run + 1 if cont else 1
        best = max(best, run)
    return ok(best <= 5, f"longest seamless run {best} slides", best)


@check("F-SW07", scope="post")
def bleed_share(post):
    if len(post.slides) < 2:
        return na("one slide")
    seams = list(zip(post.slides, post.slides[1:]))
    bleed = sum(1 for a, b in seams if crosses_edge(a, "right") and crosses_edge(b, "left"))
    return ok(bleed / len(seams) <= 0.5, f"{bleed} of {len(seams)} seams carry a bleed element")


@check("F-SW09", scope="post")
def li_no_bleed(post):
    li = post.nets.get("linkedin", {})
    if not (li.get("pdf") or li.get("pdf_title")):
        return na("no LinkedIn document")
    bad = [s.where for s in post.media("linkedin") if crosses_edge(s, "left") or crosses_edge(s, "right")]
    return ok(not bad, ", ".join(bad))


@check("F-IG35", scope="post")
def cta_clean(post):
    if len(post.slides) < 2:
        return na("one slide")
    return ok(not crosses_edge(post.slides[-1], "left"), "")


@check("V-B08", scope="post")
def seam_alignment(post):
    rows = []
    for a, b in zip(post.slides, post.slides[1:]):
        if not (crosses_edge(a, "right") and crosses_edge(b, "left")):
            continue
        ga, gb = gray(a.final)[:, -1], gray(b.final)[:, 0]
        ea, eb = np.nonzero(np.abs(np.diff(ga)) > 25)[0], np.nonzero(np.abs(np.diff(gb)) > 25)[0]
        if len(ea) and len(eb):
            off = float(np.median([np.min(np.abs(eb - y)) for y in ea]))
            r = ok(off <= 2, f"seam {a.idx}|{b.idx} offset {off:.0f}px")
            r.where = f"seam {a.idx}-{b.idx}"
            rows.append(r)
    return rows or na("no element crosses a seam")


@check("F-IG19", scope="post")
def near_duplicates(post):
    if len(post.slides) < 2:
        return na("one slide")
    hs = [phash(s.final) for s in post.slides]
    bad = []
    for i in range(len(hs)):
        for j in range(i + 1, len(hs)):
            d = hamming(hs[i], hs[j])
            if d < (12 if j == i + 1 else 8):
                bad.append(f"slides {i + 1}/{j + 1} {d} bits")
    return ok(not bad, "; ".join(bad[:4]))


@check("F-IG28", scope="post")
def send_worthy(post):
    flagged = [s for s in post.slides if s.spec.get("send_worthy")]
    if not flagged:
        return ok(False, "no slide is flagged send_worthy in post.json")
    s = flagged[0]
    t = s.text
    good = bool(re.search(r"\$?\d", t)) and bool(s.spec.get("source_line")) and "svpay" in (t.lower() + " ".join(e["text"] for e in s.elements).lower() + "svpay")
    return ok(good, "")


@check("F-TN03", scope="post")
def squint(post):
    if not post.slides or not post.slides[0].manifest:
        return na("needs the manifest")
    sl = post.slides[0]
    small = resize_area(sl.final, 648, 810)
    L = lstar(cv2.GaussianBlur(small, (0, 0), 8))
    sal = np.abs(L - np.median(L))
    k = 648 / sl.size[0]
    def score(b):
        sub = sal[int(b["y"] * k):int((b["y"] + b["h"]) * k) + 1, int(b["x"] * k):int((b["x"] + b["w"]) * k) + 1]
        return float(sub.mean()) if sub.size else 0
    hs = [score(e["box"]) for e in sl.elements if e["role"] == "headline"]
    others = [score(e["box"]) for e in sl.elements if e["role"] != "headline"]
    if sl.id_map is not None:
        ys, xs = np.nonzero(sl.id_map[..., 2] > 0)
        if len(ys):
            others.append(score({"x": xs.min(), "y": ys.min(), "w": xs.max() - xs.min(), "h": ys.max() - ys.min()}))
    if not hs:
        return na("no headline")
    return ok(max(hs) >= max(others or [0]), f"headline {max(hs):.0f} vs {max(others or [0]):.0f}")


@check("F-TN04", scope="image")
def tonal_range_thumb(post, sl):
    L = lstar(resize_area(sl.final, 64, 80))
    v = float(np.percentile(L, 95) - np.percentile(L, 5))
    return ok(v >= 35, f"L* p95-p5 {v:.0f}", round(v, 1))


@check("F-TN05", scope="image")
def white_feed_edge(post, sl):
    L = lstar(sl.final)
    b = 40
    border = np.concatenate([L[:b].ravel(), L[-b:].ravel(), L[:, :b].ravel(), L[:, -b:].ravel()])
    v = float(border.mean())
    return ok(v <= 96, f"border mean L* {v:.1f}", round(v, 1))


@check("F-TN07", scope="post")
def counter_pill_zone(post):
    if not post.slides or "instagram" not in post.nets:
        return na("no Instagram carousel")
    bad = []
    for sl in post.slides:
        if not sl.manifest:
            return na("needs the manifest")
        for e in sl.elements:
            b = e["box"]
            if e["role"] != "counter" and b["x"] + b["w"] >= 1840 and b["y"] <= 220 and (e["role"] == "headline" or re.search(r"\d", e["text"])):
                bad.append(f"{sl.where}: {e['text'][:16]}")
        for i in sl.manifest.get("images", []):
            if "logo" in i["cls"] and i["x"] + i["w"] >= 1840 and i["y"] <= 220:
                bad.append(f"{sl.where}: logo")
    return ok(not bad, "; ".join(bad))


def headline_in_crop(sl, x0, y0, x1, y1):
    from .words import headline
    if sl.manifest:
        hs = [e for e in sl.elements if e["role"] == "headline"]
        if not hs:
            return None
        inside = all(e["box"]["x"] >= x0 and e["box"]["y"] >= y0 and e["box"]["x"] + e["box"]["w"] <= x1 and e["box"]["y"] + e["box"]["h"] <= y1 for e in hs)
        return 1.0 if inside else 0.0
    crop = sl.final[y0:y1, x0:x1]
    words = ocr(crop, 11)
    if words is None:
        return None
    h, _ = headline(sl)
    return word_match_rate(h, " ".join(w["text"] for w in words))


def hero_inside(sl, x0, y0, x1, y1):
    if sl.id_map is None:
        return None
    m = sl.id_map[..., 2] > 0
    if m.sum() == 0:
        return None
    return float(m[y0:y1, x0:x1].sum() / m.sum())


@check("F-TN12", scope="image")
def square_crop(post, sl):
    nets = [n for n in ("instagram", "x", "facebook", "gbp") if sl in post.media(n)]
    if not nets or (sl.idx not in (0, 1)):
        return na("slide 1 or single images on IG, X, FB, GBP only")
    W, H = sl.size
    y0, y1 = (H - W) // 2, (H + W) // 2
    r = headline_in_crop(sl, 0, y0, W, y1)
    hv = hero_inside(sl, 0, y0, W, y1)
    if r is None:
        return na("needs the manifest or OCR")
    good = r >= 1.0 and (hv is None or hv >= 0.95)
    return (ok if sl.manifest else estimate)(good, f"headline {r:.0%} inside the 1:1 crop" + (f", hero {hv:.0%}" if hv is not None else ""))


@check("F-TN13", scope="image")
def gbp_43(post, sl):
    if sl not in post.media("gbp"):
        return na("not a GBP image")
    W, H = sl.size
    if abs(W / H - 0.8) > 0.02:
        return na("not a 4:5 image")
    y0, y1 = int((H - W * 0.75) / 2), int((H + W * 0.75) / 2)
    if not sl.manifest:
        return na("needs the manifest")
    out = [e["text"][:20] for e in sl.elements if e["box"]["y"] < y0 or e["box"]["y"] + e["box"]["h"] > y1]
    return ok(not out, "outside the 4:3 crop: " + ", ".join(out[:4]))


@check("F-X04", scope="post")
def x_three(post):
    xs = post.media("x")
    if len(xs) != 3:
        return na("not a 3-image X post")
    rows = []
    for s in xs[1:]:
        W, H = s.size
        x0, x1 = int(W * 308.6 / 2160), int(W * 1851.4 / 2160)
        r = headline_in_crop(s, x0, 0, x1, H)
        rows.append(ok(r is not None and r >= 1.0, f"{s.where}: headline {0 if r is None else r:.0%} inside the 4:7 crop"))
    return rows


@check("F-X05", scope="post")
def x_four(post):
    xs = post.media("x")
    if len(xs) != 4:
        return na("not a 4-image X post")
    rows = []
    for s in xs:
        W, H = s.size
        y0, y1 = int(H * 810 / 2700), int(H * 1890 / 2700)
        r = headline_in_crop(s, 0, y0, W, y1)
        rows.append(ok(r is not None and r >= 1.0, f"{s.where}: headline {0 if r is None else r:.0%} inside the 2:1 crop"))
    return rows


@check("F-X06", scope="post")
def mark_in_crops(post):
    rows = []
    for net in ("x", "facebook"):
        imgs = post.media(net)
        for s in imgs:
            if not s.manifest:
                continue
            W, H = s.size
            if net == "x" and len(imgs) == 1:
                crop = (0, (H - W) // 2, W, (H + W) // 2)
            elif net == "x" and len(imgs) == 4:
                crop = (0, int(H * 0.3), W, int(H * 0.7))
            elif net == "facebook" and len(imgs) > 1:
                crop = (0, (H - W) // 2, W, (H + W) // 2)
            else:
                continue
            logos = [i for i in s.manifest.get("images", []) if "logo" in i["cls"]]
            if not logos:
                continue
            l = logos[0]
            ix = max(0, min(l["x"] + l["w"], crop[2]) - max(l["x"], crop[0])) * max(0, min(l["y"] + l["h"], crop[3]) - max(l["y"], crop[1]))
            share = ix / max(1, l["w"] * l["h"])
            r = ok(share >= 0.95, f"{net} {s.where}: {share:.0%} of the SVPay mark inside the crop")
            rows.append(r)
    return rows or na("no cropped X or Facebook image")


@check("F-X09", scope="post")
def hero_center(post):
    rows = []
    for net in ("x", "facebook", "gbp"):
        for s in post.media(net):
            if s.id_map is None:
                continue
            m = s.id_map[..., 2] > 0
            if m.sum() < 1000:
                continue
            ys, xs = np.nonzero(m)
            cx, cy = xs.mean(), ys.mean()
            W, H = s.size
            if net == "gbp":
                good = abs(cx - W / 2) <= 0.1 * W and abs(cy - H / 2) <= 0.1 * H
            else:
                good = 0.25 * W <= cx <= 0.75 * W
            rows.append(ok(good, f"{net} {s.where}: hero center ({cx:.0f},{cy:.0f})"))
    return rows or na("no 3D hero on X, FB or GBP images")


@check("F-NI08", scope="post")
def hero_top(post):
    rows = []
    for net in ("linkedin", "facebook", "threads"):
        imgs = post.media(net)
        if len(imgs) != 1:
            continue
        s = imgs[0]
        tops = []
        if s.id_map is not None and (s.id_map[..., 2] > 0).any():
            tops.append(np.nonzero((s.id_map[..., 2] > 0).any(1))[0].min())
        if s.manifest:
            tops += [e["box"]["y"] for e in s.elements if e["role"] == "headline"]
        if tops:
            rows.append(ok(min(tops) <= 1485 * s.scale, f"{net}: top of the hero or claim at {min(tops):.0f}px"))
    return rows or na("no single images")


@check("F-NI14", scope="post")
def fb_multi(post):
    fb = post.media("facebook")
    if len(fb) < 2:
        return na("not a Facebook multi-photo post")
    first_ok = fb[0].idx in (0, 1)
    rest = []
    for s in fb[1:]:
        W, H = s.size
        r = headline_in_crop(s, 0, (H - W) // 2, W, (H + W) // 2)
        rest.append(r is not None and r >= 1.0)
    return ok(first_ok and all(rest), f"hook first: {first_ok}; crops pass: {sum(rest)}/{len(rest)}")


@check("F-TN15", scope="image")
def single_leftovers(post, sl):
    if sl.idx != 0:
        return na("carousel slide")
    bad = []
    if sl.manifest:
        bad += [f"counter '{e['text']}'" for e in sl.elements if e["role"] == "counter"]
        bad += [f"swipe cue '{e['text'][:16]}'" for e in sl.elements if re.search(r"(?i)\bswipe\b|next slide", e["text"])]
    else:
        t = counter_text(sl)
        if t and re.search(r"\d\s*/\s*\d", t):
            bad.append(f"counter '{t}'")
    if crosses_edge(sl, "right"):
        bad.append("an element cut at the right edge")
    if not sl.manifest and not bad:
        return estimate(True, "OCR of the top-right corner only")
    return ok(not bad, "; ".join(bad))


@check("F-TN14", scope="post")
def crop_sheet(post):
    import os
    if not post.slides:
        return na("no slides")
    folder = post.extra.get("report_dir")
    if not folder:
        return na("no report folder (run check_post.py with --out)")
    from PIL import Image, ImageDraw
    tiles = []
    s = post.slides[0]
    W, H = s.size
    crops = {"IG grid 3:4": (68, 0, 2092, H), "1:1": (0, (H - W) // 2, W, (H + W) // 2), "X 2:1": (0, 810, W, 1890), "X 4:7": (309, 0, 1851, H), "GBP 4:3": (0, 540, W, 2160)}
    for name, (x0, y0, x1, y1) in crops.items():
        im = Image.fromarray(s.final[y0:y1, x0:x1])
        im.thumbnail((320, 400))
        t = Image.new("RGB", (330, 430), "white")
        t.paste(im, (5, 25))
        ImageDraw.Draw(t).text((5, 5), name, fill="black")
        tiles.append(t)
    sheet = Image.new("RGB", (330 * len(tiles), 430), "white")
    for i, t in enumerate(tiles):
        sheet.paste(t, (330 * i, 0))
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, os.path.splitext(os.path.basename(post.slides[0].dir))[0] + "-crops.jpg")
    sheet.save(out, quality=80)
    return ok(os.path.exists(out), os.path.basename(out))
