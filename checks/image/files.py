"""Files sent to each network: pre-sizing (ruling 3), formats, caps, metadata, and which images each network gets."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from fractions import Fraction

import numpy as np

from . import check
from .core import Outcome, gray, hamming, load_rgb, na, ok, phash, png_bytes_info, resize_area

CAPS = {"instagram": 8_000_000, "threads": 8_000_000, "linkedin": 5_000_000, "facebook": 30_000_000}
META = ("instagram", "facebook", "threads")


def files_for(post, net):
    out = []
    for m in post.nets.get(net, {}).get("media", []):
        p = post.path(m)
        if p and os.path.exists(p):
            out.append(p)
    return out


def info(path, cache={}):
    k = (path, os.path.getmtime(path))
    if k not in cache:
        cache[k] = png_bytes_info(path)
    return cache[k]


def each(post, nets, fn):
    rows = []
    for net in nets:
        for p in files_for(post, net):
            r = fn(net, p, info(p))
            if r is not None:
                r.where = f"{net}: {os.path.basename(p)}"
                rows.append(r)
    return rows or na("no media for these networks")


@check("QF01", scope="post")
def meta_presized(post):
    def f(net, p, i):
        good = i["size"] == (1440, 1800) and i["format"] == "JPEG" and i.get("subsampling") == 0 and 88 <= (i.get("quality") or 0) <= 96
        return ok(good, f"{i['size'][0]}x{i['size'][1]} {i['format']} q{i.get('quality')} sampling {i.get('subsampling')}")
    return each(post, META, f)


@check("QF02", scope="post")
def ig_jpeg(post):
    return each(post, ["instagram"], lambda n, p, i: ok(i["format"] == "JPEG" and i["magic"] == "JPEG", f"{i['format']} (magic {i['magic']})"))


@check("QF03", scope="post")
def ig_aspect(post):
    def f(net, p, i):
        r = Fraction(*i["size"])
        return ok(Fraction(4, 5) <= r <= Fraction(191, 100) and r != Fraction(3, 4), f"{i['size'][0]}x{i['size'][1]}")
    return each(post, ["instagram"], f)


@check("QF04", scope="post")
def size_caps(post):
    # ruling 3: the master goes to LinkedIn when it is under 5 MB, so LinkedIn's cap carries no 10% headroom
    return each(post, CAPS, lambda n, p, i: ok(i["bytes"] < CAPS[n] if n == "linkedin" else i["bytes"] <= 0.9 * CAPS[n],
                                               f"{i['bytes'] // 1000} KB of {CAPS[n] // 1000} KB", i["bytes"]))


@check("QF05", scope="post")
def gbp_floor(post):
    return each(post, ["gbp"], lambda n, p, i: ok(i["bytes"] >= 10_000 and min(i["size"]) >= 250, f"{i['bytes'] // 1000} KB, short side {min(i['size'])}px"))


@check("QF06", scope="post")
def li_pixels(post):
    return each(post, ["linkedin"], lambda n, p, i: ok(i["size"][0] * i["size"][1] < 36_152_320, f"{i['size'][0] * i['size'][1]} px"))


@check("QF07", scope="post")
def aspect_windows(post):
    def f(net, p, i):
        r = Fraction(*i["size"])
        if net == "linkedin":
            return ok(Fraction(2, 3) <= r <= 3, f"{i['size']}")
        return ok(max(r, 1 / r) <= 10, f"{i['size']}")
    return each(post, ["linkedin", "threads"], f)


@check("QF08", scope="post")
def format_whitelist(post):
    return each(post, ["instagram", "facebook", "threads", "linkedin", "x", "gbp"], lambda n, p, i: ok(i["format"] in ("JPEG", "PNG") and i["magic"] in ("JPEG", "PNG"), i["format"]))


@check("QF09", scope="post")
def chroma_444(post):
    return each(post, ["instagram", "facebook", "threads", "linkedin", "x", "gbp"], lambda n, p, i: ok(i["format"] != "JPEG" or i.get("subsampling") == 0,
                                                                                                  {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}.get(i.get("subsampling"), "?")) if i["format"] == "JPEG" else None)


@check("QF11", scope="post")
def eight_bit_rgb(post):
    return each(post, ["instagram", "facebook", "threads", "linkedin", "x", "gbp"], lambda n, p, i: ok(i["mode"] in ("RGB",) or (i["format"] == "PNG" and i["mode"] in ("RGB", "RGBA")), i["mode"]))


@check("QF12", scope="post")
def png_opaque(post):
    return each(post, ["instagram", "facebook", "threads", "linkedin", "x", "gbp"], lambda n, p, i: ok(i.get("alpha_min", 255) == 255, f"alpha min {i.get('alpha_min', 255)}") if i["format"] == "PNG" else None)


@check("QF29", scope="post")
def master_to_li_x(post):
    def f(net, p, i):
        big = i["size"] == (2160, 2700)
        if big:
            return ok(i["bytes"] < 5_000_000, f"master {i['bytes'] // 1000} KB")
        master = p.replace("-1440", "")
        if os.path.exists(master) and master != p:
            return ok(os.path.getsize(master) >= 5_000_000, f"1440 file sent although the master is {os.path.getsize(master) // 1000} KB")
        return ok(i["size"] == (1440, 1800), f"{i['size']}")
    return each(post, ["linkedin", "x"], f)


@check("QF15", scope="post")
def downscale_quality(post):
    from skimage.metrics import structural_similarity as ssim
    def f(net, p, i):
        if i["size"] != (1440, 1800):
            return None
        master = p.replace("-1440", "")
        if master == p or not os.path.exists(master):
            return na("no master next to the 1440 file")
        ref = resize_area(load_rgb(master), 1440, 1800)
        v = ssim(gray(load_rgb(p)), gray(ref), data_range=255)
        return ok(v >= 0.995, f"SSIM {v:.4f}", round(float(v), 4))
    return each(post, META, f)


@check("QF16", scope="post")
def hairlines(post):
    rows = []
    for sl in post.slides:
        m = sl.manifest
        if not m:
            return Outcome(None, "needs the manifest")
        widths = []
        for pnl in m.get("panels", []):
            w = re.match(r"([\d.]+)px", pnl.get("border") or "")
            if w and float(w.group(1)) > 0:
                widths.append(float(w.group(1)) * m["capture"]["scale"])
        for s in m.get("svgs", []):
            for sw in re.findall(r'stroke-width="([\d.]+)"', s["html"]):
                vb = re.search(r'viewBox="0 0 ([\d.]+)', s["html"])
                k = s["w"] / float(vb.group(1)) if vb else m["capture"]["scale"]
                widths.append(float(sw) * k)
        if widths:
            rows.append(ok(min(widths) >= 3, f"thinnest line {min(widths):.1f}px", round(min(widths), 2)))
            rows[-1].where = sl.where
    return rows or na("no lines")


def banding_index(img):
    g = gray(img).astype(np.float32)
    sm = np.abs(np.diff(g, axis=0))
    rows = np.abs(np.diff(g.mean(1)))
    flat = np.percentile(sm, 50)
    steps = (rows > 0.6) & (rows < 4)
    return float(steps.mean())


@check("QF19", scope="image")
def banding_recompressed(post, sl):
    a = banding_index(resize_area(sl.final, 1080, round(sl.final.shape[0] * 1080 / sl.final.shape[1])))
    b = banding_index(sl.sim1)
    return ok(b <= 1.5 * max(a, 0.002) and b <= 0.02, f"banded rows {a:.2%} -> {b:.2%}", round(b, 4))


@check("QF30", scope="post")
def not_blank(post):
    def f(net, p, i):
        a = gray(load_rgb(p))
        floor = 150_000 * (i["size"][0] * i["size"][1]) / (1440 * 1800)
        return ok(a.std() >= 3 and i["bytes"] >= floor, f"sd {a.std():.1f}, {i['bytes'] // 1000} KB")
    rows = each(post, ["instagram", "facebook", "threads", "linkedin", "x", "gbp"], f)
    return rows


@check("QF27", scope="post")
def files_manifest(post):
    folder = os.path.dirname(post.slides[0].file) if post.slides else None
    p = os.path.join(folder, "files.json") if folder else None
    if not p or not os.path.exists(p):
        return ok(False, "no files.json from pipeline/export.py next to the slides")
    data = json.load(open(p, encoding="utf-8"))
    bad = []
    for r in data.get("files", []):
        f = os.path.join(folder, r["file"])
        if not os.path.exists(f):
            bad.append(f"missing {r['file']}")
            continue
        i = info(f)
        if [i["size"][0], i["size"][1]] != [r["w"], r["h"]] or i["bytes"] != r["bytes"]:
            bad.append(f"{r['file']} differs from the manifest")
    return ok(not bad, "; ".join(bad[:4]))


@check("QF28", scope="post")
def file_names(post):
    names = {os.path.basename(m) for v in post.nets.values() for m in v.get("media", [])}
    bad = [n for n in names if not re.fullmatch(r"(svp-\d{8}-[a-z0-9-]+-(ig|th|fb|li|x|gbp)-s\d+\.(jpg|png))|(slide-\d+(-[a-z0-9]+)*\.(jpg|png))", n)]
    return ok(not bad, ", ".join(sorted(bad)))


@check("QF10", scope="post")
def one_lossy(post):
    side = provenance(post)
    if side is None:
        return na("no provenance sidecar (QM09)")
    chains = [f.get("encode_chain", []) for f in side.get("files", [])]
    bad = [c for c in chains if sum(1 for s in c if s.lower().startswith(("jpeg", "jpg"))) != 1]
    return ok(not bad, f"{len(bad)} files with more than one lossy encode")


def provenance(post):
    folder = os.path.dirname(post.slides[0].file) if post.slides else None
    p = os.path.join(folder, "provenance.json") if folder else None
    return json.load(open(p, encoding="utf-8")) if p and os.path.exists(p) else None


@check("QF14", scope="post")
def wide_gamut_inputs(post):
    side = provenance(post)
    if side is None or not side.get("inputs"):
        return na("no input asset list (provenance sidecar)")
    bad = [a["file"] for a in side["inputs"] if re.search(r"P3|Adobe|ProPhoto", a.get("icc", ""))]
    return ok(not bad, ", ".join(bad))


@check("QM09", scope="post")
def sidecar(post):
    side = provenance(post)
    if side is None:
        return ok(False, "no provenance.json next to the slides (pipeline/export.py writes it)")
    need = ("template", "git_commit", "chromium", "three", "fonts", "files")
    miss = [k for k in need if not side.get(k)]
    return ok(not miss, "missing " + ", ".join(miss))


# ------------------------------------------------------------------ metadata (QM)
def meta_rows(post, fn):
    return each(post, ["instagram", "facebook", "threads", "linkedin", "x", "gbp"], fn)


@check("QM01", scope="post")
def exif_removed(post):
    return meta_rows(post, lambda n, p, i: ok(not i["exif"], "EXIF present" if i["exif"] else ""))


@check("QM02", scope="post")
def no_gps(post):
    def f(n, p, i):
        from PIL import Image
        with Image.open(p) as im:
            ex = im.getexif()
            gps = ex.get_ifd(0x8825) if ex else {}
            owner = {k: ex.get(k) for k in (0x010F, 0x0110, 0x013B, 0x8298, 0xA430, 0xA431) if ex and ex.get(k)}
        raw = open(p, "rb").read(200000)
        xgps = b"exif:GPS" in raw
        return ok(not gps and not owner and not xgps, ("GPS " if gps or xgps else "") + (f"device/owner fields {sorted(owner)}" if owner else ""))
    return meta_rows(post, f)


@check("QM03", scope="post")
def xmp_removed(post):
    return meta_rows(post, lambda n, p, i: ok(not i["xmp"], "XMP present" if i["xmp"] else ""))


@check("QM05", scope="post")
def no_thumbnail(post):
    def f(n, p, i):
        raw = open(p, "rb").read(65536)
        return ok(b"JFXX" not in raw and not (i["exif"] and b"\x01\x02" in raw[:2000] and b"JPEGInterchangeFormat" in raw), "")
    return meta_rows(post, f)


@check("QM06", scope="post")
def app_segments(post):
    def f(n, p, i):
        if i["format"] != "JPEG":
            return None
        bad = [s for s in i["app_segments"] if not (s.startswith("APP0") or s.startswith("APP2:ICC"))]
        return ok(not bad, ", ".join(bad))
    return meta_rows(post, f)


@check("QM12", scope="post")
def personal_text_meta(post):
    def f(n, p, i):
        from PIL import Image
        with Image.open(p) as im:
            txt = " ".join(str(v) for k, v in im.info.items() if isinstance(v, (str, bytes)) and k not in ("icc_profile", "exif"))
        raw = open(p, "rb").read(200000).decode("latin1")
        bad = re.search(r"/Users/|C:\\\\|/home/|[\w.+-]+@[\w-]+\.\w+", txt + " " + raw[:20000])
        return ok(not bad, bad.group(0) if bad else "")
    return meta_rows(post, f)


@check("V-A03", scope="post")
def srgb(post):
    def f(n, p, i):
        from PIL import Image
        with Image.open(p) as im:
            icc = im.info.get("icc_profile")
        if not icc:
            return ok(True, "no profile (sRGB assumed)")
        return ok(b"sRGB" in icc or b"srgb" in icc.lower(), "non-sRGB ICC profile")
    return meta_rows(post, f)


@check("V-A08", scope="post")
def jpeg_quality(post):
    return meta_rows(post, lambda n, p, i: ok(80 <= (i.get("quality") or 0) <= 95, f"q{i.get('quality')}") if i["format"] == "JPEG" else None)


# ------------------------------------------------------------------ network subsets and counts
@check("F-NI15", scope="post")
def fb_count(post):
    k = len(files_for(post, "facebook"))
    return na("no Facebook post") if "facebook" not in post.nets else ok(k == 1 or 2 <= k <= 5, f"{k} images", k)


@check("F-X01", scope="post")
def x_count(post):
    if "x" not in post.nets:
        return na("no X post")
    k = len(files_for(post, "x"))
    if post.pkg.get("kind") == "news" and post.pkg.get("format") != "before_after":
        return ok(k <= 1, f"{k} images for a news post", k)
    return ok(k <= 2 or post.pkg.get("x_crops_checked"), f"{k} images", k)


@check("F-TH03", scope="post")
def threads_subset(post):
    fs = files_for(post, "threads")
    if not fs:
        return na("no Threads images")
    ratios = {Fraction(*info(p)["size"]) for p in fs}
    return ok(len(fs) <= 5 and len(ratios) == 1, f"{len(fs)} images, {len(ratios)} ratios")


@check("F-TN17", scope="post")
def subsets_defined(post):
    bad = []
    deck = [os.path.abspath(s.file) for s in post.slides]
    if "x" in post.nets and len(files_for(post, "x")) > 2:
        bad.append("X has more than hook plus payoff")
    if "facebook" in post.nets and not (len(files_for(post, "facebook")) == 1 or len(files_for(post, "facebook")) <= 5):
        bad.append("Facebook over 5")
    if "threads" in post.nets and len(files_for(post, "threads")) > 5:
        bad.append("Threads over 5")
    if "instagram" in post.nets and [os.path.abspath(p) for p in files_for(post, "instagram")] != deck:
        bad.append("Instagram is not the full deck in order")
    if "gbp" in post.nets and any(os.path.abspath(p) in deck for p in files_for(post, "gbp")):
        bad.append("GBP gets a deck slide, not a native single image")
    return ok(not bad, "; ".join(bad))


@check("F-TN16", scope="post")
def subset_counters(post):
    bad = []
    for net in ("x", "threads", "facebook"):
        imgs = post.media(net)
        if not imgs or len(imgs) == len(post.slides):
            continue
        for s in imgs:
            if s.manifest:
                c = [e["text"] for e in s.elements if e["role"] == "counter"]
                if c:
                    bad.append(f"{net} {s.where} shows '{c[0]}'")
    return ok(not bad, "; ".join(bad)) if any(post.media(n) for n in ("x", "threads", "facebook")) else na("no subsets")


@check("F-GB01", scope="post")
def gbp_native(post):
    return each(post, ["gbp"], lambda n, p, i: ok(i["size"] in ((1200, 900), (1200, 1200)), f"{i['size'][0]}x{i['size'][1]}"))


@check("F-GB11", scope="post")
def gbp_filters(post):
    gb = post.media("gbp")
    if not gb or not post.slides:
        return na("no GBP image")
    import cv2
    def stats(a):
        hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV)
        return hsv[..., 1].mean(), gray(a).std()
    s0, c0 = stats(post.slides[0].final)
    s1, c1 = stats(gb[0].final)
    good = abs(s1 - s0) / max(s0, 1) <= 0.15 and abs(c1 - c0) / max(c0, 1) <= 0.15
    return ok(good, f"saturation {s0:.0f}->{s1:.0f}, contrast {c0:.0f}->{c1:.0f}")


@check("F-GB15", scope="post")
def gbp_names_svpay(post):
    gb = post.media("gbp")
    if not gb:
        return na("no GBP image")
    sl = gb[0]
    t = " ".join(e["text"] for e in sl.elements) if sl.manifest else (sl.ocr_all_text() or "")
    o = sl.ocr_all_text() or ""
    return ok(bool(re.search(r"(?i)svpay", t + " " + o)), "")


@check("F-GB16", scope="post")
def gbp_focus(post):
    gb = post.media("gbp")
    if not gb:
        return na("no GBP image")
    import cv2
    a = resize_area(gb[0].final, 1200, round(gb[0].final.shape[0] * 1200 / gb[0].final.shape[1]))
    v = float(cv2.Laplacian(gray(a).astype(np.float64), cv2.CV_64F).var())
    return ok(v >= 20, f"Laplacian variance {v:.0f} (floor 20, set from the approved renders)", round(v, 1))


@check("F-GB14", scope="post")
def gbp_inset(post):
    gb = post.media("gbp")
    if not gb:
        return na("no GBP image")
    sl = gb[0]
    if not sl.manifest:
        return na("needs the manifest")
    W, H = sl.size
    m = 0.08 * min(W, H)
    bad = [e["text"][:20] for e in sl.elements if e["box"]["x"] < m or e["box"]["y"] < m or e["box"]["x"] + e["box"]["w"] > W - m or e["box"]["y"] + e["box"]["h"] > H - m]
    return ok(not bad, ", ".join(bad[:4]))


@check("F-GB10", scope="post")
def gbp_corner_logo(post):
    gb = post.media("gbp")
    if not gb or not gb[0].manifest:
        return na("no GBP image with a manifest")
    sl = gb[0]
    W, H = sl.size
    corner = [i for i in sl.manifest.get("images", []) if "logo" in i["cls"] and (i["x"] < 0.2 * W or i["x"] + i["w"] > 0.8 * W) and (i["y"] > 0.8 * H or i["y"] < 0.2 * H)]
    return ok(not corner, "corner logo overlay" if corner else "")


def history_images(post, net=None, days=None):
    out = []
    d0 = dt.date.fromisoformat(post.date) if post.date else None
    for h in post.history:
        if net and h.get("network") != net:
            continue
        if days and d0 and h.get("date") and (d0 - dt.date.fromisoformat(h["date"])).days > days:
            continue
        for p in h.get("images", []):
            fp = post.path(p)
            if fp and os.path.exists(fp):
                out.append((h, fp))
    return out


@check("F-GB09", scope="post")
def gbp_duplicate(post):
    gb = post.media("gbp")
    if not gb:
        return na("no GBP image")
    prev = history_images(post, "gbp")
    if not prev:
        return na("no earlier GBP images in history.json")
    h0 = phash(gb[0].final)
    d = min(hamming(h0, phash(load_rgb(p))) for _, p in prev)
    return ok(d >= 10, f"closest earlier upload {d} bits", d)


@check("F-GB17", scope="post")
def gbp_pace(post):
    if "gbp" not in post.nets:
        return na("no GBP post")
    same = [h for h in post.history if h.get("network") == "gbp" and h.get("date") == post.date and h.get("images")]
    return ok(not same, f"{len(same)} GBP images already today")


@check("F-GB18", scope="post")
def gbp_new_profile(post):
    if "gbp" not in post.nets:
        return na("no GBP post")
    v = post.extra.get("gbp_verified_on")
    if not v:
        return na("profile verification date not given")
    return ok((dt.date.fromisoformat(post.date) - dt.date.fromisoformat(v)).days >= 14, f"verified {v}")


@check("F-NI12", scope="post")
def single_not_doc_page(post):
    li = post.media("linkedin")
    if len(li) != 1:
        return na("not a single LinkedIn image")
    prev = [p for h, p in history_images(post, "linkedin", 7) if h.get("format") in ("document", "pdf")]
    if not prev:
        return na("no LinkedIn document in the last 7 days")
    d = min(hamming(phash(li[0].final), phash(load_rgb(p))) for p in prev)
    return ok(d >= 10, f"{d} bits from a recent document page", d)


@check("F-LD01", scope="post")
def ld_pages(post):
    li = post.nets.get("linkedin", {})
    if not (li.get("pdf") or li.get("pdf_title")):
        return na("no LinkedIn document")
    n = li.get("pdf_pages") or len(li.get("media", []))
    return ok(10 <= n <= 12, f"{n} pages", n)


@check("F-LD07", scope="post")
def ld_order(post):
    li = post.nets.get("linkedin", {})
    if not (li.get("pdf") or li.get("pdf_title")):
        return na("no LinkedIn document")
    pdf = post.path(li.get("pdf")) if li.get("pdf") else None
    if pdf and os.path.exists(pdf):
        try:
            import subprocess, tempfile
            d = tempfile.mkdtemp()
            subprocess.run(["pdftoppm", "-r", "30", "-png", pdf, os.path.join(d, "p")], check=True, capture_output=True)
            pages = sorted(f for f in os.listdir(d) if f.endswith(".png"))
            bad = [i + 1 for i, (pg, sl) in enumerate(zip(pages, post.slides)) if hamming(phash(load_rgb(os.path.join(d, pg))), phash(sl.final)) > 4]
            return ok(not bad and len(pages) == len(post.slides), f"pages out of order: {bad}" if bad else f"{len(pages)} pages")
        except Exception as e:
            return ok(False, f"could not read the PDF: {e}")
    media = [os.path.abspath(post.path(m)) for m in li.get("media", [])]
    deck = [os.path.abspath(s.file) for s in post.slides]
    return ok(media == deck, "the document's pages are the deck in order" if media == deck else "page order differs from the slides")


@check("F-LD09", scope="post")
def ld_pdf_size(post):
    pdf = post.nets.get("linkedin", {}).get("pdf")
    if not pdf or not os.path.exists(post.path(pdf)):
        return na("no PDF file in the package")
    b = os.path.getsize(post.path(pdf))
    return ok(b <= 10_000_000, f"{b // 1000} KB", b)


@check("F-LD14", scope="post")
def ld_top_band(post):
    li = post.nets.get("linkedin", {})
    if not (li.get("pdf") or li.get("pdf_title")) or not post.slides:
        return na("no LinkedIn document")
    sl = post.slides[0]
    if not sl.manifest:
        return na("needs the manifest")
    bad = [e["text"][:20] for e in sl.elements if e["box"]["y"] < 216 and (e["role"] == "headline" or re.search(r"\d", e["text"])) and e["role"] != "counter"]
    return ok(not bad, ", ".join(bad))


@check("F-LD12", scope="post")
def ld_section_labels(post):
    li = post.nets.get("linkedin", {})
    if not (li.get("pdf") or li.get("pdf_title")) or len(post.slides) < 3:
        return na("no LinkedIn document")
    pos = []
    for sl in post.slides[1:-1]:
        if not sl.manifest:
            return na("needs the manifest")
        lab = [e for e in sl.elements if e["role"] == "label"]
        if not lab:
            return ok(False, f"{sl.where} has no section label")
        pos.append((lab[0]["box"]["x"], lab[0]["box"]["y"], len(lab[0]["text"].split())))
    good = max(p[0] for p in pos) - min(p[0] for p in pos) <= 16 and max(p[1] for p in pos) - min(p[1] for p in pos) <= 16 and all(p[2] <= 4 for p in pos)
    return ok(good, f"{len(pos)} labels")


@check("F-LD03", scope="post")
def ld_cover_author(post):
    li = post.nets.get("linkedin", {})
    if not (li.get("pdf") or li.get("pdf_title")) or not post.slides or not post.slides[0].manifest:
        return na("no LinkedIn document with a manifest")
    sl = post.slides[0]
    photo = [i for i in sl.manifest.get("images", []) if re.search(r"founder|headshot|author", i["cls"] + i["src"])]
    W, H = sl.size
    good = bool(photo) and photo[0]["w"] >= 240 and photo[0]["w"] * photo[0]["h"] <= 0.12 * W * H and "Derrik Savig" in " ".join(e["text"] for e in sl.elements)
    return ok(good, "no founder photo and name on the cover" if not good else "")
