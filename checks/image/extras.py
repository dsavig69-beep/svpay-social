"""The remaining Script-tier checks of the Nov 11 research round: list and pair templates, PDF fidelity, screenshots and
mockups, kerning, spacing, optical sizes, thumbnail value structure, glyph gaps, disclosures near effects, company-name
styling, people and release ledgers, star ratings, claim re-checks, C2PA and the Aurora grids.

Facts that only the package can carry (release ledger, shop permissions, thread layout, claim re-check log) are read
from post.json; when the post has nothing they apply to, the check is not scored ("na"), never failed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import numpy as np

from . import check
from .core import NEEDS, Outcome, gray, lstar, na, ok, resize_area, to_lab, font_file
from .files import files_for, info
from .vseries import (C2PA_MARKS, NUM_TOKEN, W, H, all_media, box, faces, gap, hero_mask, hero_number, images, li_pdf, needm, overlap,
                      photo_regions)

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None


# ------------------------------------------------------------------ templates (F-IG24, F-X02, F-X08)
@check("F-IG24", scope="post")
def list_template(post):
    if post.pkg.get("format") != "list":
        return na("not a list deck")
    items = [s for s in post.slides if s.role in ("item", "list_item")]
    if len(items) < 2:
        return na("fewer than 2 item slides")
    tids, pos = set(), []
    for sl in items:
        if not sl.manifest:
            return needm(sl)
        tids.add(sl.spec.get("template") or sl.manifest.get("section"))
        num = [e for e in sl.elements if re.fullmatch(r"\s*\d{1,2}[.)]?\s*", e["text"]) and e["role"] not in ("counter",)]
        if num:
            pos.append(box(num[0])[:2])
    spread = float(np.ptp(np.array(pos), 0).max()) if len(pos) > 1 else 0.0
    return ok(len(tids) == 1 and len(pos) == len(items) and spread <= 8, f"{len(tids)} templates, item number moves {spread:.0f}px")


@check("F-X02", scope="post")
def x_pair(post):
    fs = files_for(post, "x")
    if len(fs) != 2:
        return na("not a 2-image X post")
    sls = [post.by_file.get(os.path.abspath(p)) for p in fs]
    if any(s is None or not s.manifest for s in sls):
        return Outcome(NEEDS, "needs passes: manifest")
    tmpl = {s.spec.get("template") or s.manifest.get("section") for s in sls}
    base = []
    for s in sls:
        hs = [e for e in s.elements if e["role"] == "headline"]
        base.append(box(hs[0])[3] if hs else None)
    d = abs(base[0] - base[1]) if None not in base else 999
    order = [s.spec.get("pair") for s in sls]
    good = len(tmpl) == 1 and d <= 8 and (order == ["before", "after"] or order == [None, None])
    return ok(good, f"{len(tmpl)} templates, headline baselines {d:.0f}px apart, order {order}")


@check("F-X08", scope="post")
def x_thread(post):
    x = post.nets.get("x", {})
    th = x.get("thread")
    if not th:
        return na("not posted as an X thread")
    want = [os.path.abspath(post.path(s["file"])) for s in post.pkg.get("slides", [])]
    got = []
    for r in th:
        m = r.get("media", [])
        if len(m) != 1:
            return ok(False, f"a reply carries {len(m)} images")
        got.append(os.path.abspath(post.path(m[0])))
    return ok(got == want[:len(got)], "replies follow the deck order, hook first" if got == want[:len(got)] else "replies out of deck order")


# ------------------------------------------------------------------ PDF fidelity (F-LD06)
@check("F-LD06", scope="post")
def pdf_pages_match(post):
    p, fp = li_pdf(post)
    if not fp:
        return na("no LinkedIn PDF file in this post")
    import subprocess
    import tempfile
    from skimage.metrics import structural_similarity as ssim
    from .core import load_rgb
    rows = []
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-r", "72", "-png", fp, os.path.join(d, "p")], check=True, timeout=300)
        pages = sorted(f for f in os.listdir(d) if f.endswith(".png"))
        for i, (pg, sl) in enumerate(zip(pages, post.slides), 1):
            a = load_rgb(os.path.join(d, pg))
            b = resize_area(sl.final, a.shape[1], a.shape[0])
            s = float(ssim(gray(a), gray(b), data_range=255))
            r = ok(s >= 0.98, f"page {i} SSIM {s:.3f}", round(s, 4))
            r.where = sl.where
            rows.append(r)
    return rows or na("no pages")


# ------------------------------------------------------------------ screenshots and mockups (F-NI02, F-NI03, T-S14)
@check("F-NI02", scope="image")
def no_third_party_screens(post, sl):
    if not sl.manifest:
        return needm(sl)
    shots = [i for i in sl.manifest.get("images", []) if "screenshot" in i.get("cls", "") or "article" in i.get("cls", "") or "tweet" in i.get("cls", "")]
    own = [i for i in shots if "svpay" in i.get("src", "").lower() or "own" in i.get("cls", "")]
    bad = [os.path.basename(i["src"]) for i in shots if i not in own]
    return ok(not bad, "third-party screenshots: " + ", ".join(bad))


@check("F-NI03", scope="image")
def no_raw_phone(post, sl):
    if not sl.manifest:
        return needm(sl)
    bad = []
    for i in sl.manifest.get("images", []):
        if not i.get("natural") or "logo" in i.get("cls", ""):
            continue
        nw, nh = i["natural"]
        tall = nh / max(1, nw) >= 1.9
        if tall and i["h"] >= 0.6 * H and not any(k in i.get("cls", "") for k in ("device", "frame", "card", "crop")):
            bad.append(os.path.basename(i["src"]))
    return ok(not bad, "raw full-bleed phone screenshots: " + ", ".join(bad))


@check("T-S14", scope="image")
def mockup_text(post, sl):
    if not sl.manifest:
        return needm(sl)
    mocks = [i for i in sl.manifest.get("images", []) if any(k in i.get("cls", "") for k in ("mockup", "phone", "device"))]
    if not mocks:
        return na("no phone mockups")
    from .core import ocr, tesseract_ok
    if not tesseract_ok():
        return na("needs OCR")
    copy = {w.lower() for e in sl.elements for w in re.findall(r"[A-Za-z]{4,}", e["text"])}
    bad, n = [], 0
    for i in mocks:
        x0, y0, x1, y1 = (int(v * sl.scale) for v in box(i))
        for w in ocr(sl.final[max(0, y0):y1, max(0, x0):x1], 11) or []:
            t = w["text"]
            if not (NUM_TOKEN.search(t) or t.lower().strip(".,") in copy):
                continue
            n += 1
            xh = 0.72 * w["h"] / sl.scale if re.search(r"[a-z]", t) else 0.72 * 0.72 * w["h"] / sl.scale / 0.72
            if xh < 34:
                bad.append(f"'{t}' x-height about {xh:.0f}px")
    return ok(not bad, "; ".join(bad[:4])) if n else na("no meaningful text inside the mockups")


# ------------------------------------------------------------------ kerning (T-K07), label gap (T-K13), paragraphs (T-L10), optical sizes (T-F07)
def shaped(path, text, size, features):
    import uharfbuzz as hb
    blob = hb.Blob.from_file_path(path)
    face = hb.Face(blob)
    font = hb.Font(face)
    upm = face.upem
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, features)
    return [p.x_advance * size / upm for p in buf.glyph_positions], [g.cluster for g in buf.glyph_infos]


@check("T-K07", scope="image")
def kerning(post, sl):
    try:
        import uharfbuzz  # noqa: F401
    except Exception:
        return na("tool not installed: uharfbuzz")
    if not sl.manifest:
        return needm(sl)
    fonts = sl.manifest.get("fonts", {}).get("files", [])
    worst, checked, bad = 0.0, 0, []
    for e in sl.elements:
        r = (e.get("runs") or [None])[0]
        if not r or e["size"] < 60 or len(e.get("runs", [])) != 1:
            continue
        fam = r["family"].split(",")[0].strip().strip('"')
        f = next((x for x in fonts if x["family"] == fam and str(x["weight"]) == str(r["weight"])), None)
        path = font_file(f["src"], os.path.join(post.base, "pipeline")) if f else None
        if not path or r.get("text_transform") not in (None, "none"):
            continue
        ls = r.get("letter_spacing") or 0.0
        for ln in e["lines"]:
            ch = ln.get("chars") or []
            t = "".join(c[0] for c in ch)
            if len(ch) < 3 or t.strip() != ln["text"].strip():
                continue
            try:
                kern, cl = shaped(path, t, r["size"], {"kern": True})
                flat, _ = shaped(path, t, r["size"], {"kern": False})
            except Exception:
                continue
            if len(kern) != len(ch) or cl != list(range(len(ch))):
                continue
            obs = np.diff([c[1] for c in ch])
            ek = np.array(kern[:-1]) + ls
            ef = np.array(flat[:-1]) + ls
            em = r["size"]
            res_k = (obs - ek) / em
            res_f = (obs - ef) / em
            checked += 1
            worst = max(worst, float(np.abs(res_k).max()))
            if np.abs(np.array(kern) - np.array(flat)).max() >= 0.02 * em and np.sqrt((res_k ** 2).mean()) >= np.sqrt((res_f ** 2).mean()):
                bad.append(f"'{t[:16]}' kerning not applied")
    if not checked:
        return na("no single-run lines of 60px or more to measure")
    if worst > 0.03:
        bad.append(f"largest pair residual {worst:.3f} em")
    return ok(not bad, "; ".join(bad[:3]) or f"largest pair residual {worst:.3f} em", round(worst, 4))


def label_gaps(sl):
    out = []
    for e in sl.elements:
        t = e["text"].strip()
        if e["role"] in ("counter", "footer", "source") or not NUM_TOKEN.fullmatch(t.rstrip(".")):
            continue
        b = box(e)
        cands = [x for x in sl.elements if x is not e and x["role"] in ("label", "body") and abs(box(x)[0] - b[0]) < 0.5 * (b[2] - b[0])]
        below = [x for x in cands if box(x)[1] >= b[3] - 2]
        if below:
            x = min(below, key=lambda x: box(x)[1])
            out.append(box(x)[1] - b[3])
    return out


@check("T-K13", scope="post")
def label_gap(post):
    gs = []
    for sl in post.slides:
        if not sl.manifest:
            return needm(sl)
        gs += label_gaps(sl)
    if len(gs) < 2:
        return na("fewer than 2 number-and-label pairs")
    d = max(gs) - min(gs)
    return ok(d <= 8, f"label gaps {min(gs):.0f}-{max(gs):.0f}px", round(d))


@check("T-L10", scope="image")
def paragraph_spacing(post, sl):
    if not sl.manifest:
        return needm(sl)
    rows = []
    for p in sl.manifest.get("panels", []):
        inside = sorted([e for e in sl.elements if e["role"] == "body" and overlap(box(p), box(e)) >= 0.9 * e["box"]["w"] * e["box"]["h"]], key=lambda e: e["box"]["y"])
        for a, b in zip(inside, inside[1:]):
            if box(b)[1] < box(a)[3] - 2 or abs(box(a)[0] - box(b)[0]) > 8:
                continue                      # side by side, or not one column: not a paragraph break
            r = (a.get("runs") or [{}])[0]
            pitch = r.get("line_height") if isinstance(r.get("line_height"), (int, float)) else a["size"] * 1.4
            bb = b["lines"][0]["y"] - a["lines"][-1]["y"]
            rows.append((bb / pitch, a["text"][:14]))
    if not rows:
        return na("no paragraph breaks inside a card")
    bad = [f"after '{t}' {v:.2f}x" for v, t in rows if v < 1.5]
    return ok(not bad, "; ".join(bad[:3]))


@check("T-F07", scope="image")
def optical_sizes(post, sl):
    if not sl.manifest:
        return needm(sl)
    from fontTools.ttLib import TTFont
    fams = {}
    for f in sl.manifest.get("fonts", {}).get("files", []):
        fams.setdefault(f["family"], []).append(f)
    offer = {}
    for fam, fs in fams.items():
        p = font_file(fs[0]["src"], os.path.join(post.base, "pipeline"))
        opsz = False
        if p:
            t = TTFont(p, lazy=True)
            opsz = "fvar" in t and any(a.axisTag == "opsz" for a in t["fvar"].axes)
        display = any(f2.endswith("Display") and f2.startswith(fam.split()[0]) for f2 in fams) or fam.endswith("Display")
        offer[fam] = (opsz, display)
    if not any(o or d for o, d in offer.values()):
        return na("the families in use offer no optical sizes")
    bad = []
    for e in sl.elements:
        r = (e.get("runs") or [e])[0]
        fam = r["family"].split(",")[0].strip().strip('"')
        o, d = offer.get(fam, (False, False))
        if o and "opsz" in (r.get("features") or "") and "auto" not in (r.get("optical_sizing") or "auto"):
            bad.append(f"'{e['text'][:14]}' opsz off")
        if d and e["size"] >= 187 * 1.3 and not fam.endswith("Display"):
            bad.append(f"'{e['text'][:14]}' Text cut at {e['size']:.0f}px")
        if fam.endswith("Display") and e["size"] < 98 * 1.3:
            bad.append(f"'{e['text'][:14]}' Display cut at {e['size']:.0f}px")
    return ok(not bad, "; ".join(bad[:3]))


# ------------------------------------------------------------------ 3D and thumbnails (R-A06, R-D09, R-Q03)
@check("R-A06", scope="image")
def text_after_aa(post, sl):
    if not sl.manifest:
        return needm(sl)
    if not sl.is3d:
        return na("no 3D hero")
    bad = [e["text"][:16] for e in sl.elements if e.get("html_layer") is False]
    post_aa = [p for p in ((sl.scene or {}).get("renderer", {}).get("post_passes") or []) if re.search(r"(?i)fxaa|smaa|taa", str(p))]
    in_scene = [o.get("text") for o in sl.objects() if o.get("role") not in ("numeral",) and o.get("text") and re.search(r"[A-Za-z]{3,}", o.get("text"))]
    if post_aa and in_scene:
        bad += [f"3D text '{t[:12]}' under {post_aa[0]}" for t in in_scene]
    return ok(not bad, "; ".join(bad[:3]) or "all words are HTML text composited over the 3D layer")


@check("R-D09", scope="image")
def glyph_gaps(post, sl):
    from .threed import has3d, number_groups
    h = has3d(sl)
    if h is None:
        return na("no 3D hero")
    if isinstance(h, Outcome):
        return h
    rows = []
    for txt, os_ in number_groups(sl):
        ps = [p for o in os_ for p in sl.parts() if p["object"] == o["index"]]
        glyphs = {}
        for p in ps:
            glyphs.setdefault((p["object"], p.get("char")), []).append(p)
        gl = sorted(glyphs.values(), key=lambda g: min(p["px_box"][0] for p in g))
        if len(gl) < 3:
            continue
        masks = []
        for g in gl:
            m = None
            for p in g:
                c = sl.cover(p["part"])
                if c is not None:
                    m = c if m is None else (m | c)
            masks.append(m)
        if any(m is None for m in masks):
            return Outcome(NEEDS, "needs passes: cover")
        gh = max(p["px_box"][3] for p in ps) - min(p["px_box"][1] for p in ps)
        meds = []
        for a, b in zip(masks, masks[1:]):
            rows_ = np.where(a.any(1) & b.any(1))[0]
            if len(rows_) < 5:
                continue
            ra = np.array([np.where(a[y])[0].max() for y in rows_])
            lb = np.array([np.where(b[y])[0].min() for y in rows_])
            meds.append(float(np.median(lb - ra)))
        if len(meds) < 2:
            continue
        meds = np.array(meds)
        cv = float(meds.std() / max(1e-6, meds.mean()))
        frac = meds / gh
        r = ok(cv <= 0.08 and frac.min() >= 0.06 and frac.max() <= 0.20, f"{txt}: gap CV {cv:.0%}, gaps {frac.min():.0%}-{frac.max():.0%} of glyph height", round(cv, 3))
        rows.append(r)
    return rows or na("no 3D number of 3 or more glyphs")


@check("R-Q03", scope="image")
def value_groups(post, sl):
    a = sl.scaled(270)
    L = lstar(a.astype(float)).reshape(-1, 1).astype(np.float32)
    _, lbl, cen = cv2.kmeans(L, 3, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.2), 3, cv2.KMEANS_PP_CENTERS)
    c = np.sort(cen.ravel())
    sep = float(np.diff(c).min())
    counts = np.bincount(lbl.ravel(), minlength=3)
    bgc = float(cen.ravel()[int(np.argmax(counts))])
    hero = None
    hm = hero_mask(sl)
    if hm is not None and hm.any():
        hs = cv2.resize(hm.astype(np.uint8), (a.shape[1], a.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        if hs.any():
            hero = float(lstar(a[hs].astype(float)).mean())
    good = sep >= 12 and (hero is None or abs(hero - bgc) >= 12)
    return ok(good, f"value centers {np.round(c).astype(int).tolist()}" + (f", hero L* {hero:.0f} vs background {bgc:.0f}" if hero is not None else ""), round(sep, 1))


# ------------------------------------------------------------------ disclosures and offers (MC-F01, MC-I09, MC-K06, MC-M03)
@check("MC-F01", scope="slide")
def example_label(post, sl):
    if not sl.manifest:
        return needm(sl)
    ex = [e for e in sl.elements if re.search(r"\bExample\b", e["text"])]
    if not ex:
        return na("no 'Example' label")
    hn = hero_number(sl)
    if not hn:
        return na("no hero number")
    hb = hn["box"]
    eb = box(ex[0])
    d = gap(hb, eb)
    panels = sl.manifest.get("panels", [])
    def panel_of(b):
        for i, p in enumerate(panels):
            if overlap(box(p), b) >= 0.8 * (b[2] - b[0]) * (b[3] - b[1]):
                return i
        return None
    same = panel_of(hb) == panel_of(eb)
    return ok(d <= 200 and same, f"'Example' {d:.0f}px from the hero number, {'same' if same else 'different'} panel", round(d))


DISCLOSURE = re.compile(r"(?i)^(\*|†|‡)|\b(example|assumes|figures are|source|not affiliated|terms|results vary|savings)\b")


@check("MC-I09", scope="image")
def disclosure_clear(post, sl):
    if not sl.manifest:
        return needm(sl)
    ds = [e for e in sl.elements if e["role"] in ("footnote", "source", "legal") or DISCLOSURE.search(e["text"])]
    if not ds:
        return na("no disclosures")
    hm = hero_mask(sl)
    if hm is None and sl.is3d:
        return Outcome(NEEDS, "needs passes: 3D id pass")
    bad = []
    from scipy import ndimage as ndi
    for e in ds:
        x0, y0, x1, y1 = (int(v * sl.scale) for v in box(e))
        p = int(40 * sl.scale)
        if hm is not None and hm[max(0, y0 - p):y1 + p, max(0, x0 - p):x1 + p].any():
            bad.append(f"3D object within 40px of '{e['text'][:16]}'")
        for i in sl.manifest.get("images", []):
            if any(k in i.get("cls", "") for k in ("glow", "holo", "anim")) and gap(box(i), box(e)) < 40:
                bad.append(f"{i['cls']} within 40px of '{e['text'][:16]}'")
    return ok(not bad, "; ".join(bad[:3]))


FREE_OFFER = re.compile(r"(?i)\bfree\b[^.\n]{0,40}\b(with|when you|if you)\b[^.\n]{0,30}\b(annual|year|purchase|plan|sign)")


@check("MC-K06", scope="post")
def free_offer_window(post):
    from .words import img_text
    now = any(FREE_OFFER.search(img_text(post, s) or "") for s in post.slides)
    if not now:
        return na("no 'free with purchase' offer")
    d0 = dt.date.fromisoformat(post.date)
    months = {(d0.year, d0.month)}
    for h in post.history:
        d = h.get("date")
        if d and (d0 - dt.date.fromisoformat(d)).days <= 365 and FREE_OFFER.search(h.get("text", "") + " " + h.get("hook", "")):
            dd = dt.date.fromisoformat(d)
            months.add((dd.year, dd.month))
    return ok(len(months) <= 6, f"promoted as free in {len(months)} of the last 12 months", len(months))


@check("MC-M03", scope="image")
def company_names_plain(post, sl):
    from .words import COMPANIES
    if not sl.manifest:
        return needm(sl)
    bad, n = [], 0
    for e in sl.elements:
        runs = e.get("runs") or []
        if len(runs) < 2:
            if any(re.search(r"\b" + re.escape(c) + r"\b", e["text"]) for c in COMPANIES):
                n += 1
            continue
        main = max(runs, key=lambda r: len(r["text"]))
        for r in runs:
            if r is main or not any(re.search(r"\b" + re.escape(c) + r"\b", r["text"]) for c in COMPANIES):
                continue
            n += 1
            if (r["family"], r["weight"], r["color"]) != (main["family"], main["weight"], main["color"]):
                bad.append(r["text"][:16])
    return ok(not bad, "company name styled apart: " + ", ".join(bad[:3])) if n else na("no company names")


# ------------------------------------------------------------------ people and permissions (MC-O)
def people(post):
    """Photos with faces, per slide: [(slide, image, n_faces)]."""
    out = []
    for sl in post.slides:
        if not sl.manifest:
            continue
        for i, a in photo_regions(sl):
            n = len(faces(a)) if a.size else 0
            if n:
                out.append((sl, i, n))
    return out


def ledger(post):
    return {r.get("image"): r for r in post.pkg.get("releases", [])}


@check("MC-O01", scope="post")
def releases(post):
    if any(not s.manifest for s in post.slides):
        return Outcome(NEEDS, "needs passes: manifest")
    ppl = [x for x in people(post) if "bystander" not in x[1].get("cls", "")]
    if not ppl:
        return na("no identifiable people in the images")
    led = ledger(post)
    nets = set(post.nets)
    bad = []
    for sl, i, n in ppl:
        r = led.get(i["src"]) or led.get(os.path.basename(i["src"]))
        if not r or not {"paid", "organic"} <= set(r.get("uses", [])) or not nets <= set(r.get("networks", [])) or r.get("expires", "0") <= post.date:
            bad.append(f"{sl.where} {os.path.basename(i['src'])}")
    return ok(not bad, "no covering release: " + ", ".join(bad))


@check("MC-O02", scope="post")
def bystanders(post):
    if any(not s.manifest for s in post.slides):
        return Outcome(NEEDS, "needs passes: manifest")
    ppl = people(post)
    if not ppl:
        return na("no faces in the photos")
    led = ledger(post)
    unmatched = 0
    for sl, i, n in ppl:
        r = led.get(i["src"]) or led.get(os.path.basename(i["src"]))
        covered = (r or {}).get("people", 1 if r else 0)
        unmatched += max(0, n - covered)
    return ok(unmatched == 0, f"{unmatched} faces with no release and no blur", unmatched)


@check("MC-O05", scope="post")
def release_ids(post):
    ppl = people(post)
    if not ppl:
        return na("no identifiable people in the images")
    led = ledger(post)
    bad = []
    for sl, i, n in ppl:
        r = led.get(i["src"]) or led.get(os.path.basename(i["src"]))
        if not r or not r.get("id") or not r.get("expires") or r["expires"] < post.date:
            bad.append(os.path.basename(i["src"]))
    return ok(not bad, "release ID missing or expired on the publish date: " + ", ".join(bad))


@check("MC-O06", scope="post")
def shop_permission(post):
    shops = post.pkg.get("real_shops") or []
    if not shops:
        return na("no real shop shown")
    bad = [s.get("ref", "?") for s in shops if not s.get("permission_file") or not os.path.exists(post.path(s["permission_file"]))]
    return ok(not bad, f"{len(bad)} shops without written permission on file")


# ------------------------------------------------------------------ ratings, claims, C2PA (MC-Q06, MC-S12, QM08)
@check("MC-Q06", scope="post")
def star_fill(post):
    rows = []
    for c in post.pkg.get("charts", []):
        if c.get("type") != "stars":
            continue
        want = c["rating"] / c.get("of", 5)
        d = abs(c["fill"] - want)
        r = ok(d <= 0.01, f"fill {c['fill']:.0%} for {c['rating']} of {c.get('of', 5)}", round(d, 4))
        r.where = f"slide {c.get('slide')}"
        rows.append(r)
    return rows or na("no star ratings")


@check("MC-S12", scope="post")
def claim_rechecks(post):
    from .words import claim_numbers, img_text
    if not any(claim_numbers(img_text(post, s) or "") for s in post.slides):
        return na("no claim numbers on the images")
    log = post.pkg.get("claim_checks") or (post.extra.get("delivery") or {}).get("claim_checks") if isinstance(post.extra.get("delivery"), dict) else post.pkg.get("claim_checks")
    if not log:
        return na("not scheduled yet: no claim re-check log")
    render = [r for r in log if r.get("stage") == "render"]
    pre = [r for r in log if r.get("stage") == "pre_publish"]
    good = bool(render and pre and all(r.get("match") and r.get("hash") for r in render + pre))
    if good and post.pkg.get("publish_at"):
        t = dt.datetime.fromisoformat(post.pkg["publish_at"])
        good = any(0 <= (t - dt.datetime.fromisoformat(r["at"])).total_seconds() <= 86400 for r in pre)
    return ok(good, "claims re-checked at render and within 24 hours of publish, hashes logged" if good else "missing or failed re-check")


@check("QM08", scope="post")
def no_c2pa(post):
    files = all_media(post)
    if not files:
        return na("no media files")
    rows = []
    for p in files:
        data = open(p, "rb").read()
        segs = info(p).get("app_segments", [])
        hit = any(s.startswith("APP11") for s in segs) or b"c2pa" in data or b"caBX" in data[:200000]
        r = ok(not hit, "C2PA manifest present" if hit else "none")
        r.where = os.path.basename(p)
        rows.append(r)
    return rows


# ------------------------------------------------------------------ Aurora grids across posts (QB14, QB15)
TOKENS = os.path.join(os.path.dirname(__file__), "aurora_tokens.json")


def tokens():
    return json.load(open(TOKENS, encoding="utf-8")) if os.path.exists(TOKENS) else None


@check("QB14", scope="post")
def column_grid_token(post):
    t = tokens()
    if not t or "grid" not in t:
        return na("no column grid token (checks/image/aurora_tokens.json)")
    g = t["grid"]
    cols, margin, gutter = g["columns"], g["margin"], g["gutter"]
    cw = (W - 2 * margin - (cols - 1) * gutter) / cols
    lines = [margin + k * (cw + gutter) for k in range(cols)]
    bad = []
    for sl in post.slides:
        if not sl.manifest:
            return needm(sl)
        for e in sl.elements:
            if e["role"] in ("headline", "body", "label", "source") and e.get("align") in ("start", "left"):
                d = min(abs(box(e)[0] - x) for x in lines)
                if d > 4:
                    bad.append(f"{sl.where} '{e['text'][:12]}' {d:.0f}px off")
    return ok(not bad, "; ".join(bad[:4]))


@check("QB15", scope="post")
def baseline_grid(post):
    t = tokens()
    if not t or "baseline" not in t:
        return na("no baseline token (checks/image/aurora_tokens.json)")
    u = t["baseline"]
    from .textpx import measure
    bad = []
    for sl in post.slides:
        if not sl.has("mask", "manifest"):
            return Outcome(NEEDS, "needs passes: mask")
        for r in measure(sl):
            for ln in r["lines"]:
                b = ln.get("baseline")
                if b is None:
                    continue
                d = abs(b / sl.scale - round(b / sl.scale / u) * u)
                if d > 2:
                    bad.append(f"{sl.where} '{ln['text'][:12]}' {d:.0f}px")
    return ok(not bad, "; ".join(bad[:4]))
