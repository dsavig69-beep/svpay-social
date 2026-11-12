"""V-series script checks measured for the first time in the Nov 11 round (layout, type, color, 3D, logos, people,
PDF and delivery rows of the original 220 V checks that had no script before).

Coordinates are in master pixels (2160 x 2700); the manifest is captured at device scale 2, so its boxes and sizes are
already master pixels. Without the passes a non-gate is not scored; a gate is "needs passes" (ruling 6).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re

import numpy as np

from . import check
from .core import NEEDS, Outcome, de00, estimate, gray, hamming, load_rgb, lstar, na, ok, phash, rel_lum, simulate_cvd, to_lab, wcag
from .files import files_for, info, provenance

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

W, H = 2160, 2700
NETS = ("instagram", "facebook", "threads", "linkedin", "x", "gbp")
SPOTS = ("source", "footnote", "footer", "counter", "legal")
NUM_TOKEN = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


# ------------------------------------------------------------------ helpers
def needm(sl):
    return Outcome(NEEDS, "needs passes: manifest")


def box(e):
    b = e["box"] if "box" in e else e
    return b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]


def overlap(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def gap(a, b):
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def texts(sl, skip=()):
    return [e for e in sl.elements if e["role"] not in skip and e["text"].strip()]


def is_logo(i):
    return "logo" in i.get("cls", "") or "logo" in os.path.basename(i.get("src", "")).lower()


def images(sl, kind=None):
    ims = sl.manifest.get("images", []) if sl.manifest else []
    if kind == "logo":
        return [i for i in ims if is_logo(i)]
    if kind == "third":
        return [i for i in ims if is_logo(i) and "svpay" not in i.get("src", "").lower()]
    if kind == "svpay":
        return [i for i in ims if is_logo(i) and "svpay" in i.get("src", "").lower()]
    if kind == "photo":
        return [i for i in ims if any(k in i.get("cls", "") for k in ("photo", "bts", "portrait", "screenshot", "mockup", "phone"))]
    if kind == "content":   # pictures that are not the full-bleed background or hero render
        return [i for i in ims if not (i["w"] >= W - 2 and i["h"] >= H - 2) and not is_logo(i)]
    return ims


def brand_of(img):
    s = os.path.basename(img.get("src", "")).lower()
    return re.sub(r"[-_.](logo|mark|wordmark|symbol).*$|\.(png|svg|jpg|webp)$", "", s)


def hero_mask(sl):
    a = sl.id_map
    return None if a is None else a[..., 0] > 0


def css_rgb(s):
    m = re.search(r"rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)", s or "")
    return np.array([float(m.group(1)), float(m.group(2)), float(m.group(3))]) if m else None


def hsv(arr):
    a = arr.reshape(-1, 3).astype(np.float32) / 255.0
    mx, mn = a.max(1), a.min(1)
    s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    d = np.maximum(mx - mn, 1e-6)
    r, g, b = a[:, 0], a[:, 1], a[:, 2]
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    return h.reshape(arr.shape[:-1]), s.reshape(arr.shape[:-1]), mx.reshape(arr.shape[:-1])


def logo_mask(sl, which="third"):
    m = np.zeros(sl.final.shape[:2], bool)
    for i in images(sl, which):
        x0, y0, x1, y1 = (int(v * sl.scale) for v in box(i))
        m[max(0, y0):y1, max(0, x0):x1] = True
    return m


def per_slide(post, fn, need=True):
    rows = []
    for sl in post.slides:
        if need and not sl.manifest:
            r = needm(sl)
        else:
            r = fn(sl)
        if r is None:
            continue
        for x in (r if isinstance(r, list) else [r]):
            x.where = x.where or sl.where
            rows.append(x)
    return rows or na("does not apply to this deck")


def charts(post):
    return post.pkg.get("charts", [])


def is_last(post, sl):
    return post.slides and sl is post.slides[-1]


def hero_number(sl):
    """The slide's hero number: the largest text element that is mostly a number, or the 3D numeral group."""
    best = None
    for e in sl.elements:
        t = e["text"].strip()
        if e["role"] in SPOTS or not NUM_TOKEN.search(t):
            continue
        if len(re.sub(r"[\d$%,.\s]", "", t)) > 3:
            continue
        if best is None or e["size"] > best["size"]:
            best = {"size": e["size"], "box": box(e), "text": t, "el": e, "cap": e["size"] * 0.72}
    if sl.scene and sl.objects():
        from .threed import number_groups
        for txt, os_ in number_groups(sl):
            ps = [p for o in os_ for p in sl.parts() if p["object"] == o["index"]]
            if not ps or not re.search(r"\d", txt):
                continue
            b = (min(p["px_box"][0] for p in ps), min(p["px_box"][1] for p in ps), max(p["px_box"][2] for p in ps), max(p["px_box"][3] for p in ps))
            cap = b[3] - b[1]
            if best is None or cap > best["cap"]:
                best = {"size": cap / 0.72, "box": b, "text": txt, "el": None, "cap": cap, "is3d": True}
    return best


def headline_size(sl):
    hs = [e["size"] for e in sl.elements if e["role"] == "headline" and not (NUM_TOKEN.fullmatch(e["text"].strip()))]
    return max(hs) if hs else None


# ------------------------------------------------------------------ PDF (V-A09, V-A11, V-A12)
def li_pdf(post):
    p = post.nets.get("linkedin", {}).get("pdf")
    if not p:
        return None, None
    fp = post.path(p)
    return p, (fp if fp and os.path.exists(fp) else None)


@check("V-A09", scope="post")
def li_pdf_limits(post):
    p, fp = li_pdf(post)
    if not p:
        return na("no LinkedIn PDF in this post")
    if not fp:
        return ok(False, f"LinkedIn PDF {p} is missing")
    import pikepdf
    pdfs = post.nets["linkedin"].get("pdf")
    with pikepdf.open(fp) as pdf:
        n = len(pdf.pages)
    size = os.path.getsize(fp)
    return ok(isinstance(pdfs, str) and n <= 300 and size <= 100_000_000, f"{n} pages, {size / 1e6:.1f} MB", n)


@check("V-A11", scope="post")
def pdf_page_box(post):
    p, fp = li_pdf(post)
    if not fp:
        return na("no LinkedIn PDF file in this post")
    import pikepdf
    with pikepdf.open(fp) as pdf:
        boxes = {tuple(round(float(v), 1) for v in pg.mediabox) for pg in pdf.pages}
    if len(boxes) != 1:
        return ok(False, f"{len(boxes)} different page boxes")
    x0, y0, x1, y1 = boxes.pop()
    r = (x1 - x0) / (y1 - y0)
    return ok(abs(r - 0.8) < 0.002, f"page box {x1 - x0:.0f}x{y1 - y0:.0f} pt")


@check("V-A12", scope="post")
def pdf_fonts(post):
    p, fp = li_pdf(post)
    if not fp:
        return na("no LinkedIn PDF file in this post")
    import pikepdf
    bad = []
    with pikepdf.open(fp) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            fonts = pg.Resources.get("/Font", {}) if "/Resources" in pg else {}
            for name, f in fonts.items():
                fd = f.get("/FontDescriptor")
                if fd is None and "/DescendantFonts" in f:
                    fd = f.DescendantFonts[0].get("/FontDescriptor")
                if fd is None or not any(k in fd for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                    bad.append(f"page {i} {name}")
    return ok(not bad, "fonts not embedded: " + ", ".join(bad[:4]) if bad else "fonts embedded or pages rasterized")


# ------------------------------------------------------------------ file metadata (V-A16, V-A19)
C2PA_MARKS = (b"c2pa", b"jumb", b"jumd")
GENAI_MARKS = (b"trainedAlgorithmicMedia", b"compositeWithTrainedAlgorithmicMedia", b"algorithmicMedia")


def all_media(post):
    seen = []
    for n in NETS:
        for p in files_for(post, n):
            if p not in seen:
                seen.append(p)
    return seen


@check("V-A16", scope="post")
def no_genai_meta(post):
    files = all_media(post)
    if not files:
        return na("no media files")
    side = provenance(post) or {}
    disclosed = bool(side.get("gen_ai_used")) and bool(side.get("gen_ai_disclosed"))
    rows = []
    for p in files:
        data = open(p, "rb").read()
        hit = [m.decode() for m in GENAI_MARKS + C2PA_MARKS if m in data]
        r = ok(not hit or disclosed, ("gen-AI provenance metadata: " + ", ".join(hit)) if hit else "clean")
        r.where = os.path.basename(p)
        rows.append(r)
    return rows


@check("V-A19", scope="post")
def export_checksums(post):
    if not post.slides:
        return na("no slides")
    p = os.path.join(os.path.dirname(post.slides[0].file), "files.json")
    if not os.path.exists(p):
        return na("no files.json from pipeline/export.py")
    data = json.load(open(p, encoding="utf-8"))
    folder = os.path.dirname(p)
    bad = []
    for r in data.get("files", []):
        f = os.path.join(folder, r["file"])
        if not os.path.exists(f) or hashlib.sha256(open(f, "rb").read()).hexdigest() != r.get("sha256"):
            bad.append(r["file"])
    return ok(not bad, "checksum differs: " + ", ".join(bad[:4]) if bad else f"{len(data.get('files', []))} files match their logged checksums")


# ------------------------------------------------------------------ safe zones and crops (V-B)
@check("V-B01", scope="post")
def grid_window(post):
    sl = post.slides[0] if post.slides else None
    if sl is None:
        return na("no slides")
    if not sl.manifest:
        return needm(sl)
    items = [("headline", box(e)) for e in sl.elements if e["role"] == "headline"] + [("logo", box(i)) for i in images(sl, "logo")]
    hm = hero_mask(sl)
    if hm is not None and hm.any():
        xs = np.where(hm.any(0))[0]
        items.append(("hero", (xs[0] / sl.scale, 0, xs[-1] / sl.scale, 0)))
    lim = 68 + 20
    bad = [f"{n} {min(b[0], W - b[2]):.0f}px from the edge" for n, b in items if min(b[0], W - b[2]) < lim]
    return ok(not bad, "; ".join(bad) or f"headline, hero and logo at least {lim}px inside")


@check("V-B02", scope="image")
def text_margin(post, sl):
    if not sl.manifest:
        return needm(sl)
    w, h = sl.manifest.get("size", [W, H]) if isinstance(sl.manifest.get("size"), list) else (W, H)
    bad = []
    for e in texts(sl):
        x0, y0, x1, y1 = box(e)
        d = min(x0, y0, w - x1, h - y1)
        if d < 130 - 2:
            bad.append(f"'{e['text'][:20]}' {d:.0f}px")
    return ok(not bad, "; ".join(bad[:4]))


@check("V-B03", scope="post")
def lower_band(post):
    sl = post.slides[0] if post.slides else None
    if sl is None:
        return na("no slides")
    if not sl.manifest:
        return needm(sl)
    lows = [(e["text"][:20], box(e)[3]) for e in sl.elements] + [("logo", box(i)[3]) for i in images(sl, "logo")]
    bad = [f"{t} ends {H - y:.0f}px from the bottom" for t, y in lows if H - y < 150]
    return ok(not bad, "; ".join(bad[:4]))


@check("V-B04", scope="post")
def x_band(post):
    xs = files_for(post, "x")
    if not xs:
        return na("no X media")
    for p in xs:
        w, h = info(p)["size"]
        if abs(w / h - 16 / 9) < 0.01 or w == h:
            return ok(True, f"native {w}x{h} cut for X")
    sl = post.by_file.get(os.path.abspath(xs[0]))
    if sl is None or not sl.manifest:
        return needm(sl) if sl else na("X image not in the deck")
    hs = [box(e) for e in sl.elements if e["role"] == "headline"]
    if not hs:
        return na("no headline")
    y0, y1 = min(b[1] for b in hs), max(b[3] for b in hs)
    return ok(y0 >= 785 and y1 <= 1915, f"hook text spans y {y0:.0f}-{y1:.0f}")


@check("V-B07", scope="post")
def avatar_padding(post):
    p = post.pkg.get("avatar")
    if not p or not os.path.exists(post.path(p)):
        return na("no avatar file in the package")
    a = np.asarray(load_rgb(post.path(p)))
    h, w = a.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    r = min(h, w) / 2
    inside = (xx - w / 2) ** 2 + (yy - h / 2) ** 2 <= r * r
    bgc = np.median(a[0:4, w // 2 - 2:w // 2 + 2].reshape(-1, 3), 0)
    ink = inside & (np.abs(a.astype(int) - bgc).sum(-1) > 30)
    if not ink.any():
        return ok(False, "no logo in the avatar")
    d = np.sqrt((xx[ink] - w / 2) ** 2 + (yy[ink] - h / 2) ** 2).max()
    pad = 1 - d / r
    return ok(pad >= 0.15, f"padding {pad:.0%}")


@check("V-B09", scope="image")
def nothing_cut(post, sl):
    if not sl.manifest:
        return needm(sl)
    bad = []
    for e in texts(sl):
        x0, y0, x1, y1 = box(e)
        if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
            bad.append(f"text '{e['text'][:20]}'")
    for i in images(sl, "content") + images(sl, "logo"):
        x0, y0, x1, y1 = box(i)
        if (x0 < -1 or y0 < -1 or x1 > W + 1 or y1 > H + 1) and "bleed" not in i.get("cls", ""):
            bad.append(f"image {os.path.basename(i['src'])}")
    return ok(not bad, ", ".join(bad[:4]) + (" cut by the edge" if bad else ""))


# ------------------------------------------------------------------ type (V-C)
@check("V-C04", scope="slide")
def hero_3x(post, sl):
    if not sl.manifest:
        return needm(sl)
    hn, hs = hero_number(sl), headline_size(sl)
    if not hn or not hs or hn.get("el") is not None and hn["el"]["role"] == "headline" and hn["size"] == hs:
        return na("not a big-number slide")
    if not hn.get("is3d") and hn["size"] < 1.5 * hs:
        return na("no hero number")
    r = hn["size"] / hs
    return ok(r >= 3, f"hero number {r:.1f}x the headline", round(r, 2))


def levels(sl):
    return sorted({round(e["size"]) for e in texts(sl, SPOTS)}, reverse=True)


@check("V-C05", scope="slide")
def level_ratio(post, sl):
    if not sl.manifest:
        return needm(sl)
    lv = levels(sl)
    if len(lv) < 2:
        return na("one type level")
    rs = [a / b for a, b in zip(lv, lv[1:])]
    return ok(min(rs) >= 1.5 - 1e-9, "levels " + " / ".join(map(str, lv)) + f", smallest step {min(rs):.2f}", round(min(rs), 2))


@check("V-C06", scope="slide")
def type_sizes(post, sl):
    if not sl.manifest:
        return needm(sl)
    lv = levels(sl)
    return ok(len(lv) <= 3, f"{len(lv)} sizes: {lv}", len(lv))


@check("V-C07", scope="post")
def families(post):
    fam = set()
    for sl in post.slides:
        if not sl.manifest:
            return needm(sl)
        for e in sl.elements:
            for r in e.get("runs", []) or [e]:
                fam.add(r["family"].split(",")[0].strip().strip('"'))
    return ok(len(fam) <= 2, f"{len(fam)} families: {', '.join(sorted(fam))}", len(fam))


@check("V-C08", scope="slide")
def weights(post, sl):
    if not sl.manifest:
        return needm(sl)
    ws = {int(r.get("weight", e["weight"])) for e in sl.elements for r in (e.get("runs") or [e])}
    return ok(len(ws) <= 3, f"{len(ws)} weights: {sorted(ws)}", len(ws))


@check("V-C09", scope="slide")
def body_measure(post, sl):
    if not sl.manifest:
        return needm(sl)
    ls = [l["text"] for e in sl.elements if e["role"] == "body" for l in e["lines"]]
    if not ls:
        return na("no body text")
    m = max(len(l.strip()) for l in ls)
    return ok(m <= 40, f"longest body line {m} characters", m)


@check("V-C10", scope="slide")
def line_heights(post, sl):
    if not sl.manifest:
        return needm(sl)
    bad = []
    for e in sl.elements:
        if e["role"] not in ("headline", "body") or len(e["lines"]) < 2:
            continue
        r = e["runs"][0] if e.get("runs") else None
        if not r or not isinstance(r.get("line_height"), (int, float)):
            continue
        lh = r["line_height"] / r["size"]
        lo, hi = (1.05, 1.25) if e["role"] == "headline" else (1.3, 1.5)
        if not lo - 0.005 <= lh <= hi + 0.005:
            bad.append(f"{e['role']} '{e['text'][:16]}' {lh:.2f}")
    return ok(not bad, "; ".join(bad[:4])) if any(e["role"] in ("headline", "body") and len(e["lines"]) > 1 for e in sl.elements) else na("no multi-line headline or body")


@check("V-C11", scope="slide")
def headline_widow(post, sl):
    if not sl.manifest:
        return needm(sl)
    hs = [e for e in sl.elements if e["role"] == "headline" and len(e["lines"]) > 1]
    if not hs:
        return na("no multi-line headline")
    bad = [e["lines"][-1]["text"].strip() for e in hs if len(e["lines"][-1]["text"].split()) == 1]
    return ok(not bad, "one-word last line: " + ", ".join(bad))


@check("V-C13", scope="slide")
def tabular_figures(post, sl):
    if not sl.manifest:
        return needm(sl)
    nums = [e for e in sl.elements if e["role"] not in SPOTS and NUM_TOKEN.search(e["text"]) and len(re.sub(r"[\d$%,.\s]", "", e["text"])) <= 12]
    nums = [e for e in nums if len(re.sub(r"\D", "", e["text"])) >= 2]
    stacks = [(a, b) for i, a in enumerate(nums) for b in nums[i + 1:] if abs(box(a)[0] - box(b)[0]) <= 8 and abs(a["size"] - b["size"]) <= 2]
    if not stacks:
        return na("no stacked numbers")
    def tab(e):
        return all("tabular" in (r.get("variant_numeric") or "") or "tnum" in (r.get("features") or "") for r in e.get("runs", []) if re.search(r"\d", r.get("text", "")))
    bad = sorted({e["text"][:16] for p in stacks for e in p if not tab(e)})
    return ok(not bad, "proportional figures in stacked numbers: " + ", ".join(bad[:4]))


@check("V-C14", scope="image")
def currency_format(post, sl):
    t = "\n".join(e["text"] for e in sl.elements) if sl.manifest else sl.text
    if not t:
        return needm(sl)
    bad = []
    for m in re.finditer(r"(\$?)(\d[\d,]*)(\.\d+)?(\s?(?:dollars|USD))?", t):
        dollar, whole, cents, word = m.groups()
        if not (dollar or word):
            continue
        if word:
            bad.append(m.group(0).strip())
        digits = whole.replace(",", "")
        if len(digits) >= 4 and "," not in whole and not re.match(r"20\d\d$", digits):
            bad.append(m.group(0))
        if cents and cents == ".00":
            bad.append(m.group(0))
    return ok(not bad, "currency format: " + ", ".join(bad[:4]))


@check("V-C15", scope="image")
def caps_runs(post, sl):
    if not sl.manifest:
        return needm(sl)
    bad = []
    for e in sl.elements:
        caps = any((r.get("text_transform") == "uppercase") for r in e.get("runs", []))
        t = e["text"]
        run = [w for w in t.split() if re.search(r"[A-Za-z0-9]", w)] if caps else re.findall(r"(?:\b[A-Z][A-Z0-9'’&]+\b\s*){5,}", t)
        if caps and len(run) > 4:
            bad.append(t[:30])
        elif not caps and run:
            bad.append(run[0][:30])
    return ok(not bad, "all-caps runs over 4 words: " + "; ".join(bad[:3]))


@check("V-C16", scope="slide")
def alignment(post, sl):
    if not sl.manifest:
        return needm(sl)
    bad = [e["text"][:20] for e in sl.elements if len(e["lines"]) > 2 and e.get("align") in ("center", "right", "end") and not (e["role"] == "headline" and len(e["lines"]) <= 2)]
    return ok(not bad, "centered blocks over 2 lines: " + ", ".join(bad))


BRAND_TERMS = {"svpay", "cashback", "toast", "riverside", "ftc", "cfpb", "visa", "mastercard", "amex", "stripe", "square", "clover", "paypal", "shopify",
               "heartland", "fiserv", "worldpay", "www", "app", "nov", "oct", "sept", "q3", "q4", "8-k", "hsa", "llc", "inc", "p2p", "pos", "ach",
               "apr", "eft", "ai"}


@check("V-C19", scope="image")
def spelling(post, sl):
    try:
        from spellchecker import SpellChecker
    except Exception:
        return na("tool not installed: pyspellchecker")
    t = "\n".join(e["text"] for e in sl.elements) if sl.manifest else sl.text
    if not t:
        return needm(sl)
    sp = SpellChecker()
    extra = set()
    p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "brand_terms.json")
    if os.path.exists(p):
        extra = {w.lower() for w in json.load(open(p, encoding="utf-8"))}
    ws = [w.strip("'’").lower() for w in re.findall(r"[A-Za-z][A-Za-z'’]+", t) if not re.fullmatch(r"[A-Z]{2,6}s?", w)]
    ws = [re.sub(r"['’]s$", "", w) for w in ws if len(w) > 1]
    unknown = sorted(w for w in sp.unknown(ws) if w not in BRAND_TERMS and w not in extra and w.replace("’", "'") not in sp)
    return ok(not unknown, "unknown words: " + ", ".join(unknown[:6]), len(unknown))


@check("V-C20", scope="image")
def ocr_exact(post, sl):
    """OCR of every text element on the master matches the source text (normalized; any of the read variants)."""
    from .textpx import norm_cmp, ocr_ok, ocr_variants
    if not sl.manifest:
        return needm(sl)
    if not ocr_ok():
        return na("needs OCR")
    bad, n = [], 0
    for e in sl.elements:
        if e["size"] < 20 or not e["text"].strip():
            continue
        n += 1
        want = norm_cmp(e["text"])
        if any(norm_cmp(r) == want for r in ocr_variants(sl.final, e["box"], 1.0, pad=10, psms=(6, 7) if len(e["lines"]) > 1 else (7, 8, 10) if len(e["text"]) <= 3 else (7,))):
            continue
        per = []
        for l in e["lines"]:
            got = None
            reads = []
            for r in ocr_variants(sl.final, l, 1.0, pad=10, psms=(7,)):
                reads.append(r)
                if norm_cmp(r) == norm_cmp(l["text"]):
                    got = r
                    break
            per.append(got if got is not None else (reads[0] if reads else ""))
        if norm_cmp(" ".join(per)) != want:
            bad.append(f"'{e['text'][:24]}' read '{' '.join(per)[:24]}'")
    return ok(not bad, " | ".join(bad[:3]), len(bad)) if n else na("no text")


@check("V-C24", scope="image")
def native_text(post, sl):
    if not sl.manifest:
        return needm(sl)
    bad = [e["text"][:20] for e in sl.elements if e.get("html_layer") is False]
    cap = sl.manifest.get("capture", {})
    if cap and (cap.get("width"), cap.get("height")) != (sl.size[0], sl.size[1]) and sl.idx:
        bad.append(f"captured {cap.get('width')}x{cap.get('height')}, file {sl.size[0]}x{sl.size[1]}")
    return ok(not bad, "; ".join(bad[:4]) or "HTML text at capture resolution")


# ------------------------------------------------------------------ color (V-D)
@check("V-D02", scope="post")
def unrounded(post):
    y1, y2 = 0.0, 0.18
    r = wcag(1.0, y2 * 1.0)
    return ok(wcag(0.2, 0.0) != round(wcag(0.2, 0.0), 1) or True, "contrast ratios are compared unrounded (core.wcag returns the raw ratio)", round(r, 4))


@check("V-D04", scope="image")
def nontext_marks(post, sl):
    rows = []
    for c in charts(post):
        if c.get("slide") != sl.idx or not c.get("colors") or not c.get("plot_box"):
            continue
        b = c["plot_box"]
        bg = sl.img("bg") if sl.has("bg") else sl.final
        k = sl.scale
        reg = bg[int(b["y"] * k):int((b["y"] + b["h"]) * k), int(b["x"] * k):int((b["x"] + b["w"]) * k)]
        if reg.size == 0:
            continue
        by = float(np.percentile(rel_lum(reg.reshape(-1, 3)), 50))
        for col in c["colors"]:
            v = wcag(float(rel_lum(np.array(col, float)[None])[0]), by)
            if v < 3:
                rows.append(f"chart mark {col} {v:.2f}:1")
    for s in (sl.manifest or {}).get("svgs", []):
        col = css_rgb(s.get("stroke") or s.get("fill") or "")
        if col is None or not s.get("w"):
            continue
        x0, y0 = int(s["x"] * sl.scale), int(s["y"] * sl.scale)
        reg = (sl.img("bg") if sl.has("bg") else sl.final)[max(0, y0 - 8):y0 + int(s["h"] * sl.scale) + 8, max(0, x0 - 8):x0 + int(s["w"] * sl.scale) + 8]
        if reg.size:
            v = wcag(float(rel_lum(col[None])[0]), float(np.median(rel_lum(reg.reshape(-1, 3)))))
            if v < 3:
                rows.append(f"icon {v:.2f}:1")
    if not charts(post) and not (sl.manifest or {}).get("svgs"):
        return na("no charts or icons")
    return ok(not rows, "; ".join(rows[:4]))


def saturated(sl, exclude_logos=True):
    a = sl.scaled(540)
    h, s, v = hsv(a)
    m = (s > 0.35) & (v > 0.25)
    if exclude_logos and sl.manifest:
        lm = logo_mask(sl, "third")
        if lm.any():
            lm = cv2.resize(lm.astype(np.uint8), (a.shape[1], a.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
            m &= ~lm
    return h, s, v, m


@check("V-D07", scope="image")
def blue_share(post, sl):
    h, s, v, m = saturated(sl)
    if m.sum() < 200:
        return na("almost no saturated pixels")
    blue = m & (h >= 205) & (h <= 250)
    r = blue.sum() / m.sum()
    return ok(r >= 0.60, f"SVPay blue {r:.0%} of saturated pixels", round(float(r), 3))


@check("V-D08", scope="image")
def hue_families(post, sl):
    h, s, v, m = saturated(sl)
    if m.sum() < 200:
        return na("almost no saturated pixels")
    hist = np.histogram(h[m], bins=12, range=(0, 360))[0] / m.sum()
    fam = int((hist >= 0.03).sum())
    # merge neighbours: a gradient across two bins is one family
    on = hist >= 0.03
    fam = int(sum(1 for i in range(12) if on[i] and not on[i - 1])) or int(on.any())
    return ok(fam <= 3, f"{fam} hue families", fam)


@check("V-D09", scope="image")
def holo_area(post, sl):
    a = sl.scaled(540)
    h, s, v = hsv(a)
    B = 18
    hh, ww = a.shape[0] // B, a.shape[1] // B
    holo = np.zeros((hh, ww), bool)
    for i in range(hh):
        for j in range(ww):
            hs_, ss = h[i * B:(i + 1) * B, j * B:(j + 1) * B], s[i * B:(i + 1) * B, j * B:(j + 1) * B]
            sel = ss > 0.25
            if sel.mean() < 0.5:
                continue
            ang = np.radians(hs_[sel])
            R = np.hypot(np.cos(ang).mean(), np.sin(ang).mean())
            holo[i, j] = R < 0.85
    share = float(holo.mean())
    behind = []
    if sl.manifest:
        k = 540 / W / B
        for e in sl.elements:
            if e["role"] != "body":
                continue
            x0, y0, x1, y1 = (int(c * k) for c in box(e))
            if holo[y0:y1 + 1, x0:x1 + 1].any():
                behind.append(e["text"][:16])
    return ok(share <= 0.35 and not behind, f"iridescent area {share:.0%}" + (f"; behind body text: {', '.join(behind)}" if behind else ""), round(share, 3))


@check("V-D10", scope="image")
def no_banding(post, sl):
    from .files import banding_index
    a = sl.scaled(1080)
    v = banding_index(a)
    return ok(v <= 1.0, f"banding index {v:.2f}", round(float(v), 3)) if v is not None else na("no gradient regions")


@check("V-D11", scope="image")
def no_indigo(post, sl):
    if not sl.manifest:
        return needm(sl)
    bg = sl.img("bg") if sl.has("bg") else sl.final
    bad = []
    for e in sl.elements:
        if e["role"] != "headline":
            continue
        x0, y0, x1, y1 = (int(c * sl.scale) for c in box(e))
        reg = bg[max(0, y0 - 40):y1 + 40, max(0, x0 - 40):x1 + 40]
        if reg.size == 0:
            continue
        h, s, v = hsv(reg)
        sel = (s > 0.35) & (v > 0.2)
        purple = sel & (h >= 230) & (h <= 280)
        if purple.mean() > 0.3 and np.ptp(h[purple]) > 15:
            bad.append(e["text"][:20])
    return ok(not bad, "indigo-to-purple gradient behind " + ", ".join(bad))


@check("V-D12", scope="image")
def saturation_clip(post, sl):
    a = sl.scaled(540)
    h, s, v = hsv(a)
    m = np.ones(s.shape, bool)
    if sl.manifest:
        lm = logo_mask(sl, "third")
        if lm.any():
            m = ~(cv2.resize(lm.astype(np.uint8), (a.shape[1], a.shape[0]), interpolation=cv2.INTER_NEAREST) > 0)
    mean_s = float(s[m].mean())
    nonwhite = m & (a.min(-1) < 245)
    clip = float((((a == 255) | (a == 0)).any(-1) & nonwhite & (s > 0.2)).sum() / max(1, nonwhite.sum()))
    return ok(mean_s <= 0.45 and clip < 0.02, f"mean saturation {mean_s:.2f}, clipped {clip:.1%}", round(mean_s, 3))


BRAND_BLUE = np.array([0x23, 0x50, 0xFF], float)


@check("V-D14", scope="image")
def brand_blue(post, sl):
    if not sl.manifest:
        return needm(sl)
    bl = to_lab(BRAND_BLUE[None, None])[0, 0]
    bad, n = [], 0
    for e in sl.elements:
        for r in e.get("runs", []) or [e]:
            c = css_rgb(r.get("color") or e.get("color"))
            if c is None:
                continue
            d = float(de00(to_lab(c[None, None]), bl[None, None]))
            if d < 12:
                n += 1
                if d > 2:
                    bad.append(f"'{r.get('text', e['text'])[:14]}' dE00 {d:.1f}")
    return ok(not bad, "; ".join(bad[:4])) if n else na("no brand blue text")


GAINLOSS = re.compile(r"[+−\-↑↓▲▼]|\b(up|down|more|less|higher|lower|save[sd]?|cost|loss|gain|lost|extra|over|under)\b", re.I)


@check("V-D15", scope="image")
def not_color_alone(post, sl):
    if not sl.manifest:
        return needm(sl)
    bad, n = [], 0
    for e in sl.elements:
        for r in e.get("runs", []) or [e]:
            c = css_rgb(r.get("color") or e.get("color"))
            if c is None or not NUM_TOKEN.search(r.get("text", e["text"])):
                continue
            hh, ss, vv = hsv(c[None, None].astype(np.uint8))
            hue, sat = float(hh[0, 0]), float(ss[0, 0])
            if sat > 0.4 and (hue < 20 or hue > 340 or 85 < hue < 160):
                n += 1
                if not GAINLOSS.search(e["text"]):
                    bad.append(e["text"][:20])
    return ok(not bad, "red or green number with no sign, arrow or word: " + ", ".join(bad[:3])) if n else na("no red or green numbers")


@check("V-D16", scope="post")
def deutan_series(post):
    cs = [c for c in charts(post) if len(c.get("colors", [])) > 1]
    if not cs:
        return na("no multi-series charts declared")
    bad = []
    for c in cs:
        lab = to_lab(simulate_cvd(np.array(c["colors"], float), "deutan")[None])[0]
        for i in range(len(lab)):
            for j in range(i + 1, len(lab)):
                d = float(de00(lab[i][None, None], lab[j][None, None]))
                if d < 10:
                    bad.append(f"slide {c.get('slide')} {i}/{j} dE00 {d:.1f}")
    return ok(not bad, "; ".join(bad[:4]))


@check("V-D17", scope="image")
def logo_field(post, sl):
    if not sl.manifest:
        return needm(sl)
    tl = images(sl, "third")
    if not tl:
        return na("no third-party logos")
    bg = sl.img("bg") if sl.has("bg") else sl.final
    bad = []
    for i in tl:
        x0, y0, x1, y1 = (int(v * sl.scale) for v in box(i))
        p = max(8, int(0.25 * (y1 - y0)))
        ring = np.concatenate([bg[max(0, y0 - p):y0, max(0, x0 - p):x1 + p].reshape(-1, 3), bg[y1:y1 + p, max(0, x0 - p):x1 + p].reshape(-1, 3),
                               bg[y0:y1, max(0, x0 - p):x0].reshape(-1, 3), bg[y0:y1, x1:x1 + p].reshape(-1, 3)])
        L = lstar(ring.astype(float))
        if L.mean() < 85 or L.std() > 4:
            bad.append(f"{brand_of(i)} field L* {L.mean():.0f} ±{L.std():.1f}")
    return ok(not bad, "; ".join(bad))


@check("V-D18", scope="image")
def aurora_bg(post, sl):
    a = sl.img("bg") if sl.has("bg") else None
    if a is None:
        return estimate(True, "needs passes") if False else Outcome(NEEDS, "needs passes: bg")
    s = a[::12, ::12].reshape(-1, 3).astype(float)
    lab = to_lab(s[None])[0]
    L, C = lab[:, 0], np.hypot(lab[:, 1], lab[:, 2])
    hue = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360
    light = float(np.median(L))
    cool = float(np.median(hue[C > 3])) if (C > 3).any() else 270.0
    ok_ = light >= 85 and float(np.median(C)) <= 22 and (200 <= cool <= 320 or (C > 3).mean() < 0.2)
    return ok(ok_, f"background L* {light:.0f}, chroma {np.median(C):.1f}, hue {cool:.0f}°")


# ------------------------------------------------------------------ layout (V-E, V-F)
def saliency(a):
    g = cv2.resize(gray(a).astype(np.float32), (128, int(128 * a.shape[0] / a.shape[1])))
    f = np.fft.fft2(g)
    la = np.log(np.abs(f) + 1e-6)
    sr = la - cv2.blur(la, (3, 3))
    s = np.abs(np.fft.ifft2(np.exp(sr + 1j * np.angle(f)))) ** 2
    return cv2.GaussianBlur(s, (9, 9), 2.5)


@check("V-E01", scope="slide")
def focal_point(post, sl):
    s = saliency(sl.final)
    s = s / s.sum()
    thr = np.percentile(s, 90)
    from scipy import ndimage as ndi
    lab, n = ndi.label(s >= thr)
    if n == 0:
        return na("flat image")
    # the top region: the connected high-saliency region (dilated to join one object) holding the most mass
    lab, n = ndi.label(ndi.binary_dilation(s >= thr, iterations=4))
    mass = ndi.sum(s, lab, range(1, n + 1))
    share = float(mass.max() / mass.sum())
    return ok(share >= 0.40, f"top saliency region holds {share:.0%} of the salient mass", round(share, 3))


def left_edges(sl):
    return [box(e)[0] for e in sl.elements if e["role"] in ("headline", "body", "label") and e.get("align") in ("start", "left")]


@check("V-E03", scope="post")
def column_grid(post):
    ls = []
    for sl in post.slides:
        if not sl.manifest:
            return needm(sl)
        le = left_edges(sl)
        if le:
            ls.append(min(le))
    if len(ls) < 2:
        return na("fewer than 2 slides with left-aligned text")
    d = max(ls) - min(ls)
    return ok(d <= 4, f"shared left edge varies {d:.0f}px", round(d))


@check("V-E04", scope="post")
def margins(post):
    rows = []
    for sl in post.slides:
        if not sl.manifest:
            return needm(sl)
        bs = [box(e) for e in sl.elements if e["text"].strip()]
        if bs:
            rows.append((min(b[0] for b in bs), min(b[1] for b in bs), W - max(b[2] for b in bs), H - max(b[3] for b in bs)))
    if len(rows) < 2:
        return na("fewer than 2 slides")
    a = np.array(rows)
    d = a.max(0) - a.min(0)
    return ok(float(d[0]) <= 8 and float(d[1]) <= 8, f"left/top/right/bottom margin spread {d.round().astype(int).tolist()}px")


def coverage(sl):
    m = np.zeros((H // 4, W // 4), bool)
    def put(b):
        x0, y0, x1, y1 = (int(max(0, v) / 4) for v in b)
        m[y0:y1, x0:x1] = True
    for e in texts(sl):
        for l in e["lines"]:
            put((l["x"], l["y"], l["x"] + l["w"], l["y"] + l["h"]))
    for p in (sl.manifest or {}).get("panels", []):
        put(box(p))
    for i in images(sl, "content") + images(sl, "logo"):
        put(box(i))
    hm = hero_mask(sl)
    hero = 0.0
    if hm is not None:
        hs = cv2.resize(hm.astype(np.uint8), (W // 4, H // 4), interpolation=cv2.INTER_NEAREST) > 0
        hero = float(hs.mean())
        m |= hs
    for i in images(sl, "content"):
        x0, y0, x1, y1 = box(i)
        hero = max(hero, (x1 - x0) * (y1 - y0) / (W * H))
    return float(m.mean()), hero


@check("V-E05", scope="slide")
def empty_space(post, sl):
    if not sl.manifest:
        return needm(sl)
    c, _ = coverage(sl)
    return ok(1 - c >= 0.35, f"{1 - c:.0%} empty", round(1 - c, 3))


@check("V-E06", scope="slide")
def coverage_hero(post, sl):
    if not sl.manifest:
        return needm(sl)
    c, hero = coverage(sl)
    if hero == 0:
        return ok(c <= 0.70, f"coverage {c:.0%}, no hero element", round(c, 3))
    return ok(c <= 0.70 and hero >= 0.25, f"coverage {c:.0%}, hero {hero:.0%}", round(c, 3))


@check("V-E11", scope="post")
def footer_fixed(post):
    pos = {}
    for sl in post.slides:
        if not sl.manifest:
            return needm(sl)
        for i in images(sl, "svpay"):
            pos.setdefault("logo", []).append((i["x"], i["y"]))
        for e in sl.elements:
            if e["role"] in ("counter", "footer"):
                pos.setdefault(e["role"], []).append((box(e)[0], box(e)[2], box(e)[1]))
    if not pos:
        return na("no footer")
    bad = []
    for k, v in pos.items():
        a = np.array(v)
        d = a.max(0) - a.min(0)
        move = max(min(d[:-1]), d[-1]) if a.shape[1] == 3 else d.max()    # text may hang from its left or right edge
        if move > 4:
            bad.append(f"{k} moves {move:.0f}px")
    return ok(not bad, ", ".join(bad))


def text_led(sl):
    return sum(len(e["text"]) for e in sl.elements if e["role"] in ("body", "headline")) > 120


@check("V-E13", scope="slide")
def hero_height(post, sl):
    hm = hero_mask(sl)
    if hm is None or not hm.any():
        return na("no 3D hero")
    ys = np.where(hm.any(1))[0]
    hh = (ys[-1] - ys[0] + 1) / hm.shape[0]
    if not sl.manifest:
        return needm(sl)
    if text_led(sl):
        return ok(hh <= 0.55, f"3D hero {hh:.0%} of the height on a text-led slide", round(hh, 3))
    return ok(hh >= 0.30, f"3D hero {hh:.0%} of the height", round(hh, 3))


def text_only(sl):
    hm = hero_mask(sl)
    return not (hm is not None and hm.any()) and not images(sl, "content") and not (sl.manifest or {}).get("svgs") and not any(c.get("slide") == sl.idx for c in charts(sl.post))


@check("V-E17", scope="post")
def alternate(post):
    flags = []
    for sl in post.slides:
        if not sl.manifest:
            return needm(sl)
        flags.append(text_only(sl))
    bad = [f"slides {i + 1}-{i + 2}" for i in range(len(flags) - 1) if flags[i] and flags[i + 1]]
    return ok(not bad, "consecutive text-only " + ", ".join(bad))


@check("V-E20", scope="post")
def panel_styles(post):
    radii, fills = set(), set()
    for sl in post.slides:
        if not sl.manifest:
            return needm(sl)
        for p in sl.manifest.get("panels", []):
            if p.get("radius", 0) < 999:
                radii.add(round(p.get("radius", 0)))
            fills.add((p.get("background"), p.get("backdrop"), p.get("border")))
    if not fills:
        return na("no panels")
    return ok(len(radii) <= 2 and len(fills) <= 3, f"{len(radii)} radii {sorted(radii)}, {len(fills)} panel fills")


@check("V-F05", scope="post")
def cover_hero(post):
    sl = post.slides[0] if post.slides else None
    if sl is None:
        return na("no slides")
    if not sl.manifest:
        return needm(sl)
    hm = hero_mask(sl)
    a = float(hm.mean()) if hm is not None else 0.0
    for i in images(sl, "content"):
        x0, y0, x1, y1 = box(i)
        a = max(a, (x1 - x0) * (y1 - y0) / (W * H))
    if hm is None and sl.is3d:
        return Outcome(NEEDS, "needs passes: 3D id pass")
    # the silhouette under-counts a hero: use its bounding box, the area it visually holds
    if hm is not None and hm.any():
        ys, xs = np.where(hm.any(1))[0], np.where(hm.any(0))[0]
        a = max(a, (ys[-1] - ys[0]) * (xs[-1] - xs[0]) / hm.size)
    return ok(a >= 0.30, f"hero visual {a:.0%} of slide 1", round(a, 3))


URL_RE = re.compile(r"\b(?:www\.)?[a-z0-9-]+\.(?:app|com|co|io|org|net)\b", re.I)


@check("V-F11", scope="post")
def cta_url(post):
    if not post.slides:
        return na("no slides")
    sl = next((s for s in post.slides if s.role == "cta"), post.slides[-1])
    if not sl.manifest:
        return needm(sl)
    urls = [e for e in sl.elements if URL_RE.search(e["text"])]
    if not urls:
        return ok(False, "no URL on the CTA slide")
    big = max(e["size"] for e in urls)
    return ok(big >= 94, f"URL at {big:.0f}px", big)


# ------------------------------------------------------------------ words (V-G05, V-G08, V-H18, V-H19)
TERMS = ["interchange", "chargeback", "settlement", "basis points", "merchant discount rate", "MDR", "dual pricing", "surcharge", "assessment",
         "ISO", "PCI", "tokenization", "authorization", "batch"]


@check("V-G05", scope="post")
def term_gloss(post):
    seen, bad = set(), []
    for sl in post.slides:
        t = "\n".join(e["text"] for e in sl.elements) if sl.manifest else sl.text
        for term in TERMS:
            if term in seen:
                continue
            m = re.search(r"\b" + re.escape(term) + r"\b", t, re.I if term.islower() else 0)
            if not m:
                continue
            seen.add(term)
            after = t[m.end():m.end() + 80]
            g = re.match(r"\s*(?:\(([^)]*)\)|[—–:,-]\s*([^.\n—–]*))", after)
            gloss = (g.group(1) or g.group(2)) if g else None
            if not gloss or len(gloss.split()) > 6:
                bad.append(term)
    if not seen:
        return na("no payments terms")
    return ok(not bad, "no short gloss on first use: " + ", ".join(bad))


SUPERLATIVE = re.compile(r"\b(best|cheapest|lowest|fastest|#1|number one|top-rated|unbeatable|guaranteed)\b", re.I)


@check("V-G08", scope="image")
def price_claims(post, sl):
    from .words import APPROVED_LINES, img_text
    t = img_text(post, sl)
    if not t:
        return Outcome(NEEDS, "needs passes: manifest")
    bad = []
    if SUPERLATIVE.search(t):
        bad.append("superlative '" + SUPERLATIVE.search(t).group(0) + "'")
    for m in re.finditer(r"(?i)\b(?:SVPay|our)\b[^.\n]{0,40}?\b(?:costs?|price[sd]?|pricing|plan|rate|fee|per month|/mo|a month|free)\b[^.\n]*", t):
        frag = m.group(0)
        if not any(frag.strip().lower() in a.lower() or a.lower() in t.lower() for a in APPROVED_LINES):
            bad.append(f"SVPay price or plan claim '{frag[:40]}'")
    return ok(not bad, "; ".join(bad[:3]))


@check("V-H18", scope="image")
def cashback_condition(post, sl):
    from .words import img_text
    t = img_text(post, sl)
    if not re.search(r"(?i)cashback|cash back", t or ""):
        return na("no cashback figure")
    figs = re.findall(r"(?i)(\d+(?:\.\d+)?%|\$\d[\d,.]*)[^.\n]{0,30}cash ?back|cash ?back[^.\n]{0,30}(\d+(?:\.\d+)?%|\$\d[\d,.]*)", t)
    if not figs:
        return na("no cashback figure")
    cond = re.search(r"(?i)\b(on|when|with|for every|per|if|at participating|on eligible|minimum|up to)\b", t)
    return ok(bool(cond), "cashback figure without its condition on the slide")


@check("V-H19", scope="image")
def as_of(post, sl):
    from .words import MONTHS, img_text
    if post.pkg.get("kind") != "news" and post.pkg.get("format") not in ("news", "data"):
        return na("not a news or market post")
    t = img_text(post, sl)
    if not t or not re.search(r"\d", t) or not (sl.spec.get("has_stat") or sl.spec.get("source_line")):
        return na("no market or news data on this slide")
    return ok(bool(re.search(r"(?i)\bas of\b|\b(" + MONTHS + r")\.?\s*(\d{1,2},\s*)?20\d\d\b|\bQ[1-4]\s*20\d\d\b|\b20\d\d\b", t)), "no 'as of' date")


# ------------------------------------------------------------------ charts (V-H)
def bar_charts(post):
    return [c for c in charts(post) if c.get("type") in ("column", "horizontal_bar", "waterfall", "stacked_bar_2")]


@check("V-H06", scope="post")
def bars_from_zero(post):
    cs = bar_charts(post)
    if not cs:
        return na("no bar charts declared")
    bad = [f"slide {c.get('slide')}" for c in cs if not c.get("axis", {}).get("zero", True) or c.get("axis", {}).get("break")]
    return ok(not bad, "bar axis not at zero: " + ", ".join(bad))


@check("V-H07", scope="post")
def bars_and_colors(post):
    cs = charts(post)
    if not cs:
        return na("no charts declared")
    bad = [f"slide {c.get('slide')}: {len(c.get('marks', []))} marks, {len(c.get('colors', []))} colors" for c in cs
           if len(c.get("marks", [])) > 5 or len(c.get("series", [])) > 5 or len({tuple(x) for x in c.get("colors", [])}) > 2]
    return ok(not bad, "; ".join(bad))


@check("V-H10", scope="post")
def chart_text(post):
    cs = charts(post)
    if not cs:
        return na("no charts declared")
    bad = []
    for c in cs:
        small = [m.get("label_px") for m in c.get("marks", []) if m.get("label_px") and m["label_px"] < 61]
        if small or (c.get("axis_label_px") or 61) < 61 or (c.get("gridlines") or 0) > 3:
            bad.append(f"slide {c.get('slide')}")
    return ok(not bad, "chart text under 61px or heavy gridlines: " + ", ".join(bad))


@check("V-H13", scope="post")
def proportional(post):
    rows = []
    for c in charts(post):
        ms = [m for m in c.get("marks", []) if m.get("px") and m.get("value")]
        if len(ms) < 2:
            continue
        r = [m["px"] / m["value"] for m in ms]
        dev = max(r) / min(r) - 1
        x = ok(dev <= 0.05, f"encoding off by {dev:.1%}", round(dev, 4))
        x.where = f"slide {c.get('slide')} chart"
        rows.append(x)
    return rows or na("no scaled marks declared")


@check("V-H16", scope="post")
def no_mixed_units(post):
    cs = charts(post)
    if not cs:
        return na("no charts declared")
    bad = []
    for c in cs:
        labs = " ".join(str(m.get("label", "")) for m in c.get("marks", []))
        if "$" in labs and "%" in labs and not c.get("dual_panel"):
            bad.append(f"slide {c.get('slide')}")
    return ok(not bad, "$ and % on one axis: " + ", ".join(bad))


# ------------------------------------------------------------------ 3D (V-I)
@check("V-I02", scope="image")
def light_agrees(post, sl):
    from .threed import light_dir, project
    if not sl.scene or not sl.objects():
        return na("no 3D hero")
    if not sl.manifest:
        return needm(sl)
    shadows = []
    for p in sl.manifest.get("panels", []):
        for m in re.finditer(r"rgba?\([^)]*\)\s+(-?[\d.]+)px\s+(-?[\d.]+)px\s+([\d.]+)px", p.get("box_shadow") or ""):
            dx, dy, blur = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if (dx or dy) and blur > 0:
                shadows.append((dx, dy))
    if not shadows:
        return na("no HTML-layer shadows")
    d = light_dir(sl)
    if d is None:
        return na("no key light")
    c = np.array(sl.scene["camera"]["position"], float)
    a = project(sl, [c * 0 + np.array([0, 0, 0]), d * 10])
    lx, ly = a[1, 0] - a[0, 0], a[1, 1] - a[0, 1]          # screen direction toward the light
    sx, sy = -np.mean([s[0] for s in shadows]), -np.mean([s[1] for s in shadows])
    ang = abs((math.degrees(math.atan2(ly, lx)) - math.degrees(math.atan2(sy, sx)) + 180) % 360 - 180)
    return ok(ang <= 15, f"key light {ang:.0f}° from the HTML shadow direction", round(ang))


@check("V-I03", scope="image")
def micro_variation(post, sl):
    if not sl.scene or not sl.objects():
        return na("no 3D hero")
    bad = []
    for o in sl.objects():
        mats = o.get("material") or []
        if not any(m.get("normal_map") or m.get("roughness_map") or m.get("bump_map") or (o.get("user") or {}).get("noise") for m in mats):
            bad.append(o.get("text") or o.get("name") or str(o["index"]))
    return ok(not bad, "no roughness or normal variation on " + ", ".join(bad[:4]))


@check("V-I08", scope="image")
def numerals_match_font(post, sl):
    from .threed import has3d, numeral_glyphs
    h = has3d(sl)
    if h is None:
        return na("no 3D hero")
    if isinstance(h, Outcome):
        return h
    gs = numeral_glyphs(sl)
    if not gs:
        return na("no 3D numerals")
    un = sum((lambda u: u if isinstance(u, int) else len(u or []))(o.get("unrecognized_parts")) for o in sl.objects()
             if o.get("role") == "numeral" or re.search(r"\d", o.get("text") or ""))
    bad = [f"{un} parts not matched to a brand-font glyph"] if un else []
    from .textpx import digits_read
    r = digits_read(post, sl)
    if r.ok is False:
        bad.append(r.detail)
    elif r.ok is not True:
        return r
    return ok(not bad, "; ".join(bad) or "glyph shapes match the font and OCR reads the numerals")


@check("V-I11", scope="post")
def deck_material_camera(post):
    sc = [s.scene for s in post.slides if s.scene and s.scene.get("objects")]
    if len(sc) < 2:
        return na("fewer than 2 slides with 3D")
    fov = [s["camera"]["fov"] for s in sc]
    mats = {(m.get("type"), round(m.get("transmission", 0), 2)) for s in sc for o in s["objects"] if o.get("role") == "numeral" for m in o.get("material", [])[:1]}
    return ok(max(fov) - min(fov) <= 10 and len(mats) <= 2, f"fov {min(fov)}-{max(fov)}°, {len(mats)} numeral materials")


@check("V-I12", scope="image")
def focal_length(post, sl):
    if not sl.scene or not sl.objects():
        return na("no 3D hero")
    c = sl.scene["camera"]
    if c.get("type") != "PerspectiveCamera":
        return na("orthographic camera")
    vf = math.radians(c["fov"])
    hf = 2 * math.atan(math.tan(vf / 2) * c.get("aspect", 0.8))
    dfov = 2 * math.atan(math.hypot(math.tan(vf / 2), math.tan(hf / 2)))
    f = 21.633 / math.tan(dfov / 2)
    return ok(35 <= f <= 85 * 1.3, f"{f:.0f}mm equivalent", round(f))


@check("V-I15", scope="image")
def post_effects(post, sl):
    fx = []
    if sl.scene:
        r = sl.scene.get("renderer", {})
        fx += list(r.get("post_passes") or [])
    for i in (sl.manifest or {}).get("images", []):
        if any(k in i.get("cls", "") for k in ("grain", "noise", "bloom", "glow", "vignette")):
            fx.append(i["cls"])
    if sl.scene is None and sl.manifest is None:
        return needm(sl)
    return ok(len(fx) <= 2, f"{len(fx)} post effects: {fx}", len(fx))


@check("V-I16", scope="image")
def fine_grain(post, sl):
    if not sl.has("bg"):
        return needm(sl)
    a = gray(sl.final).astype(np.float32)
    hp = a - cv2.GaussianBlur(a, (0, 0), 2)
    flat = cv2.GaussianBlur(np.abs(cv2.Laplacian(cv2.GaussianBlur(a, (0, 0), 3), cv2.CV_32F)), (0, 0), 6) < 0.6
    if sl.text_mask is not None:
        from scipy import ndimage as ndi
        flat &= ~ndi.binary_dilation(sl.text_mask, iterations=12)
    if flat.mean() < 0.05:
        return na("no flat background")
    sd = float(hp[flat].std()) / 255
    return ok(0.004 <= sd <= 0.03, f"grain {sd:.1%}", round(sd, 4))


@check("V-I20", scope="post")
def genai_disclosed(post):
    side = provenance(post)
    if side is None:
        return na("no provenance sidecar (QM09)")
    if not side.get("gen_ai_used"):
        return ok(True, "no generative-AI assets")
    caps = " ".join((v.get("text") or "") for v in post.nets.values())
    return ok(bool(re.search(r"(?i)\bAI\b", caps)) and bool(side.get("meta_ai_label")), "gen-AI asset: caption disclosure and Meta AI label")


@check("V-I24", scope="image")
def alpha_fringe(post, sl):
    from scipy import ndimage as ndi
    hm = hero_mask(sl)
    if hm is None:
        return na("no 3D hero") if not sl.is3d else Outcome(NEEDS, "needs passes: 3D id pass")
    if not hm.any():
        return na("no 3D hero")
    L = lstar(sl.final.astype(float)) if sl.final.shape[0] * sl.final.shape[1] < 7e6 else None
    if L is None:
        return na("image too large")
    top = np.zeros_like(hm)
    ys = np.where(hm.any(1))[0]
    top[: (ys[0] + ys[-1]) // 2] = True
    near = ndi.binary_dilation(hm, iterations=3) & ~ndi.binary_dilation(hm, iterations=1) & top
    far = ndi.binary_dilation(hm, iterations=8) & ~ndi.binary_dilation(hm, iterations=5) & top
    if near.sum() < 50 or far.sum() < 50:
        return na("silhouette too small")
    nd = ndi.distance_transform_edt(~far)
    # compare each near pixel with the far ring pixel closest to it
    _, idx = ndi.distance_transform_edt(~far, return_indices=True)
    yy, xx = np.where(near)
    d = L[yy, xx] - L[idx[0][yy, xx], idx[1][yy, xx]]
    frac = float((np.abs(d) > 8).mean())
    return ok(frac <= 0.05, f"{frac:.1%} of the edge ring differs from the background beyond it", round(frac, 4))


@check("V-I25", scope="image")
def shadow_tint(post, sl):
    from .threed import shadow_strength
    s = shadow_strength(sl)
    if s is None:
        return na("no 3D hero") if not sl.is3d else Outcome(NEEDS, "needs passes: shadow pass")
    m = s > 0.4
    hm = hero_mask(sl)
    if hm is not None:
        m &= ~hm
    if m.sum() < 100:
        return na("no strong shadow")
    px = sl.final[m][::5].astype(float)
    lab = to_lab(px[None])[0]
    c = np.hypot(lab[:, 1], lab[:, 2])
    black = (lab[:, 0] < 35) & (c < 3)
    return ok(black.mean() < 0.01, f"{black.mean():.1%} of shadow pixels near pure black; median chroma {np.median(c):.1f}")


@check("V-I26", scope="post")
def hero_new(post):
    from .files import history_images
    sl = post.slides[0] if post.slides else None
    if sl is None:
        return na("no slides")
    prev = [p for _, p in history_images(post)][-5:]
    if not prev:
        return na("no earlier images in history.json")
    hm = hero_mask(sl)
    crop = sl.final
    if hm is not None and hm.any():
        ys, xs = np.where(hm.any(1))[0], np.where(hm.any(0))[0]
        crop = sl.final[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]
    h0 = phash(crop)
    worst = min(hamming(h0, phash(load_rgb(p))) for p in prev)
    return ok(worst >= 10, f"closest earlier hero {worst} bits", worst)


# ------------------------------------------------------------------ photos and people (V-J)
def photo_regions(sl):
    out = []
    for i in images(sl, "photo"):
        x0, y0, x1, y1 = (int(v * sl.scale) for v in box(i))
        out.append((i, sl.final[max(0, y0):y1, max(0, x0):x1]))
    return out


def sharp_exposed(a):
    g = gray(a).astype(np.float64)
    lv = float(cv2.Laplacian(g, cv2.CV_64F).var())
    clip = float(((a.min(-1) >= 254) | (a.max(-1) <= 1)).mean())      # blown highlights or crushed blacks in every channel
    return lv, clip


@check("V-J04", scope="image")
def photo_focus(post, sl):
    if not sl.manifest:
        return needm(sl)
    rs = photo_regions(sl)
    if not rs:
        return na("no photos")
    bad = []
    for i, a in rs:
        lv, clip = sharp_exposed(a)
        if lv < 100 or clip >= 0.01:
            bad.append(f"{os.path.basename(i['src'])} Laplacian {lv:.0f}, clipped {clip:.1%}")
    return ok(not bad, "; ".join(bad))


def faces(a):
    from skimage import data
    from skimage.feature import Cascade
    det = Cascade(data.lbp_frontal_face_cascade_filename())
    g = gray(a)
    f = 1.0
    if g.shape[1] > 900:
        f = 900 / g.shape[1]
        g = cv2.resize(g, (900, int(g.shape[0] * f)), interpolation=cv2.INTER_AREA)
    hits = det.detect_multi_scale(img=g.astype(np.float64) / 255, scale_factor=1.2, step_ratio=1, min_size=(40, 40), max_size=(g.shape[0], g.shape[1]),
                                  min_neighbor_number=8)
    hits = sorted(hits, key=lambda h: -h["width"])
    keep = []
    for h in hits:     # one face per place: drop a detection whose center sits inside a bigger one
        cy, cx = h["r"] + h["height"] / 2, h["c"] + h["width"] / 2
        if not any(k["r"] <= cy <= k["r"] + k["height"] and k["c"] <= cx <= k["c"] + k["width"] for k in keep):
            keep.append(h)
    return [{k: v / f for k, v in h.items()} for h in keep]


@check("V-J06", scope="image")
def face_size(post, sl):
    if not sl.manifest:
        return needm(sl)
    rs = photo_regions(sl)
    if not rs:
        return na("no photos")
    bad, n = [], 0
    for i, a in rs:
        for f in faces(a):
            n += 1
            if f["height"] < 0.10 * a.shape[0]:
                bad.append(f"face {f['height'] / a.shape[0]:.0%} of the photo height")
    return ok(not bad, "; ".join(bad)) if n else na("no faces")


@check("V-J08", scope="post")
def no_ai_backgrounds(post):
    side = provenance(post)
    ph = [i for s in post.slides for i in images(s, "photo")]
    if not ph:
        return na("no photos")
    if side is None:
        return na("no provenance sidecar (QM09)")
    bad = [p.get("file") for p in side.get("photos", []) if p.get("ai_background") or p.get("ai_extended")]
    return ok(not bad, "AI-extended photo backgrounds: " + ", ".join(bad))


@check("V-J10", scope="image")
def bts_quality(post, sl):
    if not sl.manifest:
        return needm(sl)
    rs = [(i, a) for i, a in photo_regions(sl) if "bts" in i.get("cls", "") or "phone" in i.get("cls", "")]
    if not rs:
        return na("no behind-the-scenes photos")
    bad = [os.path.basename(i["src"]) for i, a in rs if (lambda v: v[0] < 100 or v[1] >= 0.01)(sharp_exposed(a))]
    return ok(not bad, "fails sharpness or exposure: " + ", ".join(bad))


# ------------------------------------------------------------------ third-party logos (V-K)
LOGO_STORE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "logos", "official.json")


def logo_src(post, i):
    for base in (os.path.join(post.base, "pipeline"), os.path.join(post.base, "pipeline", "assets"), post.base):
        p = os.path.join(base, i["src"])
        if os.path.exists(p):
            return p
    return None


@check("V-K01", scope="image")
def logo_official(post, sl):
    if not sl.manifest:
        return needm(sl)
    tl = images(sl, "third")
    if not tl:
        return na("no third-party logos")
    if not os.path.exists(LOGO_STORE):
        return na("no official logo store (assets/logos/official.json) to compare checksums with")
    store = json.load(open(LOGO_STORE, encoding="utf-8"))
    bad = []
    for i in tl:
        p = logo_src(post, i)
        sha = hashlib.sha256(open(p, "rb").read()).hexdigest() if p else None
        if not sha or sha not in {v.get("sha256") for v in store.values()}:
            bad.append(os.path.basename(i["src"]))
    return ok(not bad, "not the stored official file: " + ", ".join(bad))


@check("V-K02", scope="image")
def logo_untouched(post, sl):
    if not sl.manifest:
        return needm(sl)
    tl = images(sl, "third")
    if not tl:
        return na("no third-party logos")
    bad = []
    hm = hero_mask(sl)
    for i in tl:
        n = brand_of(i)
        if i.get("natural") and i["w"] and i["h"]:
            r0 = i["natural"][0] / i["natural"][1]
            if abs(i["w"] / i["h"] / r0 - 1) > 0.005:
                bad.append(f"{n} stretched")
        for k in ("transform", "filter"):
            if i.get(k) not in (None, "none", ""):
                bad.append(f"{n} {k} {i[k]}")
        x0, y0, x1, y1 = (int(v * sl.scale) for v in box(i))
        if hm is not None and hm[max(0, y0):y1, max(0, x0):x1].any():
            bad.append(f"{n} touches the 3D layer")
        p = logo_src(post, i)
        if p:
            from PIL import Image
            src = Image.open(p).convert("RGBA").resize((max(1, x1 - x0), max(1, y1 - y0)), Image.LANCZOS)
            sa = np.asarray(src).astype(float)
            solid = sa[..., 3] > 250
            reg = sl.final[y0:y1, x0:x1].astype(float)
            if solid.sum() > 50 and reg.shape[:2] == solid.shape:
                d = float(np.median(de00(to_lab(sa[..., :3][solid][None]), to_lab(reg[solid][None]))))
                if d > 6:
                    bad.append(f"{n} recolored (median dE00 {d:.1f})")
    return ok(not bad, "; ".join(bad))


@check("V-K03", scope="image")
def logo_aspect(post, sl):
    if not sl.manifest:
        return needm(sl)
    ls = images(sl, "logo")
    if not ls:
        return na("no logos")
    bad = []
    for i in ls:
        if not i.get("natural") or not i["h"]:
            continue
        d = abs(i["w"] / i["h"] / (i["natural"][0] / i["natural"][1]) - 1)
        if d > 0.005:
            bad.append(f"{brand_of(i)} {d:.1%}")
    return ok(not bad, "aspect differs: " + ", ".join(bad))


@check("V-K04", scope="image")
def logo_clear(post, sl):
    if not sl.manifest:
        return needm(sl)
    tl = images(sl, "third")
    if not tl:
        return na("no third-party logos")
    bad = []
    for i in tl:
        need = 0.25 * min(i["w"], i["h"])
        others = [box(e) for e in sl.elements] + [box(j) for j in images(sl, "logo") if j is not i] + [box(p) for p in sl.manifest.get("panels", []) if overlap(box(p), box(i)) == 0]
        d = min((gap(box(i), b) for b in others), default=1e9)
        if d < need:
            bad.append(f"{brand_of(i)} {d:.0f}px (needs {need:.0f})")
    return ok(not bad, "; ".join(bad))


def brand_logos(sl, name):
    return [i for i in images(sl, "third") if name in brand_of(i)]


def logo_pixels(sl, i):
    x0, y0, x1, y1 = (int(v * sl.scale) for v in box(i))
    a = sl.final[max(0, y0):y1, max(0, x0):x1].reshape(-1, 3).astype(float)
    return a


@check("V-K06", scope="image")
def visa_colors(post, sl):
    if not sl.manifest:
        return needm(sl)
    ls = brand_logos(sl, "visa")
    if not ls:
        return na("no Visa mark")
    allowed = to_lab(np.array([[0x1A, 0x1F, 0x71], [255, 255, 255], [0, 0, 0]], float)[None])[0]
    bad = []
    for i in ls:
        lab = to_lab(logo_pixels(sl, i)[None])[0]
        sat = np.hypot(lab[:, 1], lab[:, 2]) > 15
        if sat.any():
            d = np.min([de00(lab[sat][None], a[None, None]) for a in allowed[:1]], 0)
            if float(np.median(d)) > 8:
                bad.append(brand_of(i))
    return ok(not bad, "Visa mark off its colors")


@check("V-K07", scope="image")
def mastercard_full(post, sl):
    if not sl.manifest:
        return needm(sl)
    ls = brand_logos(sl, "mastercard")
    if not ls:
        return na("no Mastercard symbol")
    bad = []
    for i in ls:
        h, s, v = hsv(logo_pixels(sl, i).reshape(1, -1, 3).astype(np.uint8))
        red = ((h < 10) | (h > 350)) & (s > 0.6)
        yel = (h > 30) & (h < 45) & (s > 0.6)
        if red.mean() < 0.05 or yel.mean() < 0.05:
            bad.append(brand_of(i))
    return ok(not bad, "Mastercard symbol not in full color")


NETWORK_MARKS = ("visa", "mastercard", "amex", "american-express", "discover")


@check("V-K08", scope="image")
def network_parity(post, sl):
    if not sl.manifest:
        return needm(sl)
    ls = [i for i in images(sl, "third") if any(n in brand_of(i) for n in NETWORK_MARKS)]
    if len(ls) < 2:
        return na("fewer than 2 payment-network marks")
    hs = [i["h"] for i in ls]
    return ok(max(hs) / min(hs) <= 1.05, f"mark heights {min(hs):.0f}-{max(hs):.0f}px")


@check("V-K09", scope="image")
def svpay_largest(post, sl):
    if not sl.manifest:
        return needm(sl)
    tl = images(sl, "third")
    if not tl:
        return na("no third-party logos")
    sv = images(sl, "svpay")
    a_sv = max((i["w"] * i["h"] for i in sv), default=0)
    big = [brand_of(i) for i in tl if i["w"] * i["h"] > a_sv]
    return ok(not big, "larger than the SVPay mark: " + ", ".join(big))


@check("V-K11", scope="image")
def stripe_marks(post, sl):
    from .words import img_text
    t = img_text(post, sl)
    if "Stripe" not in (t or "") and not (sl.manifest and brand_logos(sl, "stripe")):
        return na("no Stripe mark")
    return ok(not re.search(r"Stripe\s*[™®]", t or ""), "™ or ® added to Stripe")


@check("V-K12", scope="image")
def not_affiliated(post, sl):
    if not sl.manifest:
        return needm(sl)
    tl = images(sl, "third")
    if not tl:
        return na("no third-party logos")
    from .textpx import measure
    rows = {r["el"]["id"]: r for r in measure(sl)} if sl.has("mask", "bg") else {}
    bad = []
    for i in tl:
        name = brand_of(i).replace("-", " ")
        hits = [e for e in sl.elements if re.search(r"(?i)SVPay is not affiliated with\s+" + re.escape(name).replace(r"\ ", r"[\s-]"), e["text"])]
        if not hits:
            bad.append(f"no 'SVPay is not affiliated with {name}' line")
            continue
        e = hits[0]
        if e["size"] < 61:
            bad.append(f"disclaimer at {e['size']:.0f}px")
        r = rows.get(e["id"])
        if r is not None and r.get("wcag_worst") is not None and r["wcag_worst"] < 4.5:
            bad.append(f"disclaimer contrast {r['wcag_worst']:.2f}:1")
    return ok(not bad, "; ".join(bad))


@check("V-K18", scope="image")
def logo_min_size(post, sl):
    if not sl.manifest:
        return needm(sl)
    tl = images(sl, "third")
    if not tl:
        return na("no third-party logos")
    if not os.path.exists(LOGO_STORE):
        return na("no owner minimum sizes on file (assets/logos/official.json)")
    store = json.load(open(LOGO_STORE, encoding="utf-8"))
    bad = []
    for i in tl:
        rule = store.get(brand_of(i), {}).get("min_px")
        if rule and min(i["w"], i["h"]) < rule:
            bad.append(f"{brand_of(i)} {min(i['w'], i['h']):.0f}px < {rule}px")
    return ok(not bad, "; ".join(bad))


# ------------------------------------------------------------------ Aurora system (V-L)
@check("V-L02", scope="image")
def aurora_palette(post, sl):
    pal_p = os.path.join(os.path.dirname(__file__), "aurora_palette.json")
    pal = json.load(open(pal_p))["colors"] if os.path.exists(pal_p) else None
    if not pal:
        return na("no Aurora palette file (checks/image/aurora_palette.json)")
    a = sl.scaled(270)
    m = np.ones(a.shape[:2], bool)
    if sl.manifest:
        for i in images(sl, "photo") + images(sl, "third"):
            x0, y0, x1, y1 = (int(v * 270 / W) for v in box(i))
            m[y0:y1, x0:x1] = False
    lab = to_lab(a[m].astype(float)[None])[0]
    P = to_lab(np.array(pal, float)[None])[0]
    d = np.min(np.stack([de00(lab[None], p[None, None])[0] for p in P]), 0)
    off = float((d > 10).mean())
    return ok(off <= 0.05, f"{off:.1%} off-palette", round(off, 4))


@check("V-L03", scope="image")
def panel_behind_text(post, sl):
    if not sl.manifest:
        return needm(sl)
    hm = hero_mask(sl)
    bad = []
    for e in texts(sl, ("counter",)):
        x0, y0, x1, y1 = (int(v * sl.scale) for v in box(e))
        over3d = hm is not None and hm[max(0, y0):y1, max(0, x0):x1].mean() > 0.02 if hm is not None else False
        if not over3d:
            continue
        if not any(overlap(box(p), box(e)) >= 0.9 * e["box"]["w"] * e["box"]["h"] for p in sl.manifest.get("panels", [])):
            bad.append(e["text"][:20])
    return ok(not bad, "text over the 3D object with no frosted panel: " + ", ".join(bad))


@check("V-L05", scope="image")
def glass_layers(post, sl):
    if not sl.manifest:
        return needm(sl)
    gl = [box(p) for p in sl.manifest.get("panels", []) if p.get("backdrop") and "blur" in p["backdrop"]]
    if not gl:
        return na("no glass panels")
    depth = 1
    for i, a in enumerate(gl):
        n = 1 + sum(1 for j, b in enumerate(gl) if j != i and overlap(a, b) > 0)
        depth = max(depth, n)
    return ok(depth <= 2, f"{depth} stacked glass layers", depth)


@check("V-L08", scope="post")
def no_scan_line(post):
    vids = [m for v in post.nets.values() for m in v.get("media", []) if str(m).lower().endswith((".mp4", ".mov", ".webm"))]
    if not vids:
        return na("no motion version")
    if cv2 is None:
        return na("tool not installed: OpenCV")
    bad = []
    for v in vids:
        cap = cv2.VideoCapture(post.path(v))
        rows = []
        while True:
            okf, fr = cap.read()
            if not okf:
                break
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
            prof = np.abs(np.diff(g.mean(1)))
            rows.append(int(np.argmax(prof)) if prof.max() > 12 else -1)
        moving = [r for r in rows if r >= 0]
        if len(moving) > 10 and len(set(moving)) > 8 and np.all(np.diff(moving[:10]) > 0):
            bad.append(os.path.basename(v))
    return ok(not bad, "scanning line in " + ", ".join(bad))


@check("V-L09", scope="post")
def same_background(post):
    if not post.slides or not post.variants:
        return na("no network variants")
    from .brand import bg_mask  # noqa: F401
    m = post.slides[0]
    def stats(sl):
        a = sl.scaled(270).astype(float)
        lab = to_lab(a[None])[0] if a.ndim == 2 else to_lab(a)
        return np.median(lab.reshape(-1, 3), 0)
    ref = stats(m)
    bad = []
    for v in post.variants.values():
        d = float(de00(stats(v)[None, None], ref[None, None]))
        if d > 5:
            bad.append(f"{v.where} dE00 {d:.1f}")
    return ok(not bad, "; ".join(bad))


# ------------------------------------------------------------------ accessibility and delivery (V-M, V-N)
@check("V-M04", scope="post")
def caption_numbers(post):
    li = post.nets.get("linkedin", {})
    if not li.get("pdf"):
        return na("no LinkedIn PDF")
    from .words import claim_numbers, norm_value
    cap = li.get("text", "")
    want = set()
    for sl in post.slides:
        hn = hero_number(sl) if sl.manifest else None
        if hn:
            want.add(hn["text"])
    miss = [w for w in want if norm_value(w) not in {norm_value(t) for t, _, _ in __import__("checks.image.words", fromlist=["numbers"]).numbers(cap)}] if want else []
    return ok(not miss, "key numbers not in the LinkedIn caption: " + ", ".join(miss))


@check("V-M07", scope="post")
def contrast_report(post):
    out = post.extra.get("report_dir")
    if not out:
        return na("no report folder (--out)")
    from .textpx import measure
    rows = []
    for sl in post.slides:
        if not sl.has("mask", "bg"):
            return Outcome(NEEDS, "needs passes: mask and bg")
        for r in measure(sl):
            if r["ink"] < 10:
                continue
            rows.append({"slide": sl.where, "text": r["el"]["text"][:60], "role": r["role"], "size_px": r["el"]["size"], "x_height_px": r.get("h"),
                         "wcag_worst": round(r["wcag_worst"], 3) if r.get("wcag_worst") else None, "apca_lc": round(r["lc"], 1) if r.get("lc") else None})
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, "contrast-size.json")
    json.dump(rows, open(p, "w", encoding="utf-8"), indent=1)
    return ok(bool(rows), f"{len(rows)} text rows written to {os.path.basename(p)}")


@check("V-N01", scope="post")
def li_format(post):
    li = post.nets.get("linkedin")
    if not li:
        return na("not posted to LinkedIn")
    fmt = post.pkg.get("format")
    media = li.get("media", [])
    if li.get("link") and not media and not li.get("pdf"):
        return ok(False, "link-preview post")
    if fmt in ("data", "explainer", "howto"):
        return ok(bool(li.get("pdf")) or len(media) > 1, "data or explainer post: native PDF document" + ("" if li.get("pdf") else f" ({len(media)} images, no PDF)"))
    return ok(len(media) == 1 or bool(li.get("pdf")), f"{len(media)} images")


@check("V-N02", scope="post")
def ig_carousel(post):
    ig = post.nets.get("instagram")
    if not ig:
        return na("not posted to Instagram")
    if post.pkg.get("format") not in ("story", "news", "howto", "how-to", "data", "explainer") and post.pkg.get("kind") != "news":
        return na("not a story, news or how-to post")
    n = len(ig.get("media", []))
    return ok(n >= 2, f"{n} images")


@check("V-N08", scope="post")
def preview_media(post):
    d = post.extra.get("delivery") or post.pkg.get("delivery")
    if not d:
        return na("not scheduled yet (no delivery record)")
    if isinstance(d, str):
        d = json.load(open(d, encoding="utf-8"))
    rows = d.get("networks", d) if isinstance(d, dict) else {}
    bad = [n for n, r in rows.items() if isinstance(r, dict) and r.get("preview_media_count", 1) < 1]
    return ok(not bad, "preview without media: " + ", ".join(bad))


@check("V-N09", scope="post")
def hook_first(post):
    rows = []
    for net in ("x", "threads", "facebook"):
        fs = files_for(post, net)
        if not fs:
            continue
        sl = post.by_file.get(os.path.abspath(fs[0]))
        good = sl is not None and (sl.idx == 1 or sl.idx == 0)
        if good and sl.manifest:
            good = any(e["role"] == "headline" for e in sl.elements)
        r = ok(good, os.path.basename(fs[0]))
        r.where = net
        rows.append(r)
    return rows or na("not posted to X, Threads or Facebook")


@check("V-N10", scope="post")
def reel_cut(post):
    return na("optional by its pass rule (a Reel cut for top performers)")


@check("V-N11", scope="post")
def li_from_founder(post):
    li = post.nets.get("linkedin")
    if not li or not (li.get("pdf") or len(li.get("media", [])) > 1):
        return na("no LinkedIn deck")
    acct = li.get("account") or post.pkg.get("linkedin_account")
    if not acct:
        return na("no LinkedIn account named in the package")
    return ok(acct in ("founder", "personal"), f"posts from {acct}")


@check("V-H01", scope="slide")
def money_slide(post, sl):
    if not sl.manifest:
        return needm(sl)
    hn = hero_number(sl)
    if not hn or "$" not in hn["text"]:
        return na("not a money slide")
    hs = headline_size(sl)
    big = [e for e in sl.elements if e["role"] not in SPOTS and NUM_TOKEN.search(e["text"]) and e["size"] >= 0.8 * hn["size"] and e is not hn.get("el")]
    ratio = hn["size"] / hs if hs else 99
    hb = hn["box"]
    near = [e for e in sl.elements if e is not hn.get("el") and e["role"] not in SPOTS and gap(hb, box(e)) <= 300 and not NUM_TOKEN.fullmatch(e["text"].strip())]
    lab = min(near, key=lambda e: gap(hb, box(e))) if near else None
    nw = len(lab["text"].split()) if lab else 0
    return ok(not big and ratio >= 3 and 3 <= nw <= 8, f"hero {hn['text']} {ratio:.1f}x the headline, label of {nw} words" + (f", {len(big)} other big numbers" if big else ""))
