"""Checks on the render process (R-P, QR) and on the published post (QD).

QD checks run after publishing (ALGORITHM step 8) on a read-back file passed with --delivery delivery.json:
  {"networks": {"instagram": {"status": "published", "media_type": "CAROUSEL_ALBUM", "children": 6,
                "served": ["https://..."], "order_ok": true, "alt": [...], "ssim": [...], "de00_mean": [...], ...}},
   "linkedin_images": {"urn": "AVAILABLE"}, "sweeps": {"1h": true, "24h": true, "7d": null}, "og": {...}}
Before publishing they are not scored.
"""
from __future__ import annotations

import json
import os
import re

from . import check
from .core import REPO, na, ok

LOCK = os.path.join(os.path.dirname(__file__), "render.lock.json")
RESULTS = os.path.join(REPO, "checks", "regression", "results.json")


def lock():
    return json.load(open(LOCK, encoding="utf-8")) if os.path.exists(LOCK) else {}


def manifests(post):
    return [s.manifest for s in post.all_images() if s.manifest]


def scenes(post):
    out = {}
    for s in post.all_images():
        if s.scene:
            out[s.hero_stem] = s.scene
    return list(out.values())


@check("R-P02", scope="post")
def regression_set(post):
    if not os.path.exists(RESULTS):
        return ok(False, "no regression results (python3 checks/regression/run.py)")
    r = json.load(open(RESULTS, encoding="utf-8"))
    return ok(r.get("all_pass"), f"{r.get('summary', '')}")


@check("R-P04", scope="post")
def three_upgrade(post):
    sc = scenes(post)
    if not sc or not os.path.exists(RESULTS):
        return na("no 3D passes or no regression results")
    rev = json.load(open(RESULTS, encoding="utf-8")).get("three_revision")
    bad = [s["three_revision"] for s in sc if s.get("three_revision") != rev]
    return ok(not bad, f"rendered with three r{bad[0]} but the regression set ran on r{rev}" if bad else f"r{rev}")


@check("R-P10", scope="post")
def calibration_set(post):
    lab = os.path.join(REPO, "checks", "labels")
    n = 0
    for f in os.listdir(lab):
        if f.endswith("APPROVED.json"):
            n += len(json.load(open(os.path.join(lab, f), encoding="utf-8")).get("slides", []))
    return ok(n >= 20, f"{n} approved slides in the calibration set (20 needed)", n)


@check("QR05", scope="post")
def renderer_pinned(post):
    sc = scenes(post)
    if not sc:
        return na("no 3D passes")
    want = lock().get("gpu")
    bad = [s.get("gpu") for s in sc if want and s.get("gpu") != want]
    return ok(not bad, f"GPU {sc[0].get('gpu')}")


@check("QR08", scope="post")
def versions_pinned(post):
    ms = manifests(post)
    if not ms:
        return na("no render manifest")
    L = lock()
    bad = []
    for m in ms:
        if L.get("chromium") and m["capture"].get("chromium") != L["chromium"]:
            bad.append(f"chromium {m['capture'].get('chromium')}")
    for s in scenes(post):
        if L.get("three") and s.get("three_revision") != L["three"]:
            bad.append(f"three r{s.get('three_revision')}")
    return ok(not bad, ", ".join(sorted(set(bad))))


@check("QR10", scope="post")
def viewport_pinned(post):
    ms = manifests(post)
    if not ms:
        return na("no render manifest")
    bad = [m["slide"] for m in ms if m["capture"]["scale"] != 2 or m["capture"]["viewport"] != [1080, 1400] or [m["capture"]["width"], m["capture"]["height"]] != [2160, 2700]]
    return ok(not bad, ", ".join(bad))


@check("QR12", scope="post")
def capture_reliable(post):
    sc = scenes(post)
    if not sc:
        return na("no 3D passes")
    return ok(all(s["renderer"].get("preserve_drawing_buffer") for s in sc), "")


@check("QR15", scope="post")
def fonts_pinned(post):
    import hashlib
    L = json.load(open(os.path.join(os.path.dirname(__file__), "fonts.lock.json"), encoding="utf-8"))
    ms = manifests(post)
    if not ms:
        return na("no render manifest")
    bad = []
    for f in {x["src"] for m in ms for x in m["fonts"]["files"] if x.get("src")}:
        p = os.path.join(post.base, "pipeline", f)
        if not os.path.exists(p):
            bad.append(f"missing {os.path.basename(f)}")
            continue
        sha = hashlib.sha256(open(p, "rb").read()).hexdigest()
        if L.get(os.path.basename(p)) and L[os.path.basename(p)] != sha:
            bad.append(os.path.basename(p))
        elif not L.get(os.path.basename(p)):
            bad.append(f"{os.path.basename(p)} not in the lock")
    return ok(not bad, ", ".join(bad[:4]))


@check("QR16", scope="post")
def no_network_surprises(post):
    ms = manifests(post)
    if not ms:
        return na("no render manifest")
    bad = sorted({f for m in ms for f in m.get("failed_requests", []) if f})
    errs = [c for s in scenes(post) for c in s.get("console", []) if c.startswith(("ERR", "error", "failed"))]
    ext = [x["src"] for m in ms for x in m.get("images", []) if re.match(r"https?://", x["src"] or "")]
    return ok(not bad and not errs and not ext, "; ".join(bad + errs[:2] + ext[:2]))


@check("QR22", scope="post")
def render_time(post):
    sc = scenes(post)
    if not sc:
        return na("no 3D passes")
    worst = max(s.get("render_seconds", 0) for s in sc)
    return ok(worst < 30, f"slowest 3D render {worst:.0f}s", worst)


@check("QR11", scope="post")
def frozen(post):
    return na("needs a second render with the clock and random seed changed (run3d.js --repeat)")


@check("QR04", scope="post")
def deterministic(post):
    return na("needs two renders of the same slide (run3d.js --repeat)")


@check("QR01", scope="post")
def goldens(post):
    d = os.path.join(REPO, "checks", "goldens")
    return na("no golden renders yet (checks/goldens)") if not os.path.isdir(d) else ok(True, "")


@check("QR02", scope="post")
def pixel_diff(post):
    return goldens(post)


@check("QR03", scope="post")
def diff_clusters(post):
    return goldens(post)


@check("QR23", scope="post")
def variant_goldens(post):
    return goldens(post)


@check("QR07", scope="post")
def gpu_cpu(post):
    return na("needs a GPU host render to compare")


@check("QR17", scope="post")
def glyph_metric(post):
    return na("needs a measureText probe in the render manifest")


@check("QR19", scope="post")
def jpeg_deterministic(post):
    from .core import load_rgb
    import io, hashlib
    from PIL import Image
    if not post.slides:
        return na("no slides")
    a = load_rgb(post.slides[0].file)
    h = []
    for _ in range(2):
        b = io.BytesIO()
        Image.fromarray(a).save(b, "JPEG", quality=92, subsampling=0)
        h.append(hashlib.sha256(b.getvalue()).hexdigest())
    return ok(h[0] == h[1], "")


@check("QR21", scope="post")
def stress(post):
    return na("needs the stress fixtures (longest headline, 7-digit amounts)")


@check("QR24", scope="post")
def masks_from_dom(post):
    return goldens(post)


@check("QR25", scope="post")
def container(post):
    return na("renders do not run in a pinned container on this machine")


@check("QR26", scope="post")
def two_shots(post):
    return na("needs two captures 250 ms apart (render.js --stability)")


# ------------------------------------------------------------------ after publishing
def delivery(post):
    return post.extra.get("delivery")


def per_net(post, fn):
    d = delivery(post)
    if not d:
        return na("runs after publishing (pass --delivery)")
    rows = []
    for net, v in d.get("networks", {}).items():
        r = fn(net, v)
        if r is not None:
            r.where = net
            rows.append(r)
    return rows or na("no read-back for this check")


@check("QD01", scope="post")
def published(post):
    return per_net(post, lambda n, v: ok(v.get("status") == "published", v.get("status")))


@check("QD02", scope="post")
def li_available(post):
    d = delivery(post)
    if not d or not d.get("linkedin_images"):
        return na("runs after publishing (LinkedIn image status)")
    bad = [k for k, v in d["linkedin_images"].items() if v != "AVAILABLE"]
    return ok(not bad, ", ".join(bad))


@check("QD03", scope="post")
def x_media(post):
    return per_net(post, lambda n, v: ok(v.get("media_count") == len(post.nets.get("x", {}).get("media", [])), "") if n == "x" else None)


@check("QD04", scope="post")
def ig_type(post):
    def f(n, v):
        if n != "instagram":
            return None
        k = len(post.nets.get("instagram", {}).get("media", []))
        return ok(v.get("media_type") == ("CAROUSEL_ALBUM" if k > 1 else "IMAGE") and v.get("children", k) == k, "")
    return per_net(post, f)


@check("QD05", scope="post")
def readback(post):
    return per_net(post, lambda n, v: ok(v.get("children", len(post.nets.get(n, {}).get("media", []))) == len(post.nets.get(n, {}).get("media", [])), "") if n in ("threads", "facebook", "gbp") else None)


def served_metric(key, test, label):
    def f(post):
        return per_net(post, lambda n, v: ok(all(test(x) for x in v[key]), f"{label} {v[key]}") if v.get(key) else None)
    return f


check("QD06", scope="post")(served_metric("aspect_err", lambda x: x <= 0.005, "aspect error"))
check("QD07", scope="post")(served_metric("ssim", lambda x: x >= 0.90, "SSIM"))
check("QD08", scope="post")(served_metric("de00_mean", lambda x: x <= 2.0, "mean dE00"))
check("QD09", scope="post")(served_metric("shift_px", lambda x: x <= 2, "shift"))
check("QD11", scope="post")(served_metric("ocr_accuracy", lambda x: x >= 0.98, "OCR accuracy"))


@check("QD10", scope="post")
def order(post):
    return per_net(post, lambda n, v: ok(v.get("order_ok"), "") if "order_ok" in v else None)


@check("QD12", scope="post")
def alt_arrived(post):
    return per_net(post, lambda n, v: ok(v.get("alt") == post.nets.get(n, {}).get("alt"), "") if "alt" in v else None)


@check("QD13", scope="post")
def sweeps(post):
    d = delivery(post)
    if not d or not d.get("sweeps"):
        return na("runs after publishing (+1 h, +24 h, +7 d sweeps)")
    bad = [k for k, v in d["sweeps"].items() if v is False]
    return ok(not bad, ", ".join(bad))


@check("QD15", scope="post")
def alert_retry(post):
    d = delivery(post)
    if not d or "alert_minutes" not in d:
        return na("runs after publishing")
    return ok(d["alert_minutes"] <= 15 and d.get("retries", 0) <= 1, "")


@check("QD27", scope="post")
def metrics_stored(post):
    d = delivery(post)
    return na("runs after publishing") if not d else ok(all("ssim" in v for v in d.get("networks", {}).values()), "")


def og(post):
    return post.extra.get("og")


def og_rows(post, fn):
    o = og(post)
    if not o:
        return na("runs on the www.svpay.app Open Graph audit (pass --og og.json)")
    rows = []
    for page in o.get("pages", []):
        r = fn(page)
        if r is not None:
            r.where = page.get("url", "page")
            rows.append(r)
    return rows or na("no pages")


check("QD16", scope="post")(lambda post: og_rows(post, lambda p: ok(all(p.get(k) for k in ("og:image", "og:image:width", "og:image:height", "og:image:alt")), "")))
check("QD17", scope="post")(lambda post: og_rows(post, lambda p: ok(p.get("image_status") == 200 and p.get("image_type") in ("image/png", "image/jpeg") and str(p.get("og:image", "")).startswith("https://"), "")))
check("QD18", scope="post")(lambda post: og_rows(post, lambda p: ok(p.get("image_size") == [1200, 630] == [int(p.get("og:image:width", 0)), int(p.get("og:image:height", 0))], "")))
check("QD19", scope="post")(lambda post: og_rows(post, lambda p: ok(p.get("safe_zone_ok"), "") if "safe_zone_ok" in p else None))
check("QD20", scope="post")(lambda post: og_rows(post, lambda p: ok(bool(p.get("og:image:alt")) and len(p.get("og:image:alt", "")) <= 420 and p.get("og:image:alt") != p.get("og:title"), "")))
check("QD21", scope="post")(lambda post: og_rows(post, lambda p: ok(p.get("aurora_ok"), "") if "aurora_ok" in p else None))
check("QD22", scope="post")(lambda post: og_rows(post, lambda p: ok(bool(re.search(r"[0-9a-f]{8}", p.get("og:image", ""))), "")))
check("QD24", scope="post")(lambda post: (lambda o: na("runs on the Open Graph audit") if not o else ok(len({p.get("og:image") for p in o["pages"]}) == len(o["pages"]), ""))(og(post)))
check("QD25", scope="post")(lambda post: og_rows(post, lambda p: ok(p.get("metadata_clean"), "") if "metadata_clean" in p else None))
check("QD26", scope="post")(lambda post: og_rows(post, lambda p: ok(p.get("crawler_ok"), "") if "crawler_ok" in p else None))


@check("QF21", scope="post")
def host_headers(post):
    urls = [u for v in post.nets.values() for u in v.get("media_urls", [])]
    if not urls:
        return na("no public image URLs in the package (media_urls)")
    res = post.extra.get("link_results") or {}
    bad = [u for u in urls if u in res and res[u] not in (True, 200)]
    unknown = [u for u in urls if u not in res]
    return ok(not bad, ", ".join(bad)) if not unknown else na(f"{len(unknown)} image URLs not checked (pass them in --link-results)")


@check("QF22", scope="post")
def fetchable(post):
    urls = [u for v in post.nets.values() for u in v.get("media_urls", [])]
    if not urls:
        return na("no public image URLs in the package (media_urls)")
    return ok(all(u.startswith("https://") for u in urls), "")


@check("QF23", scope="post")
def url_lifetime(post):
    urls = [u for v in post.nets.values() for u in v.get("media_urls", [])]
    if not urls:
        return na("no public image URLs in the package (media_urls)")
    signed = [u for u in urls if re.search(r"(?i)[?&](expires|x-amz-expires|sig|signature)=", u)]
    return ok(not signed, f"{len(signed)} signed URLs")


@check("QF24", scope="post")
def metricool_copy(post):
    d = delivery(post)
    if not d or "metricool_ssim" not in d:
        return na("runs after scheduling (Metricool's hosted copy)")
    return ok(all(x >= 0.99 for x in d["metricool_ssim"]), "")


@check("QF26", scope="post")
def li_pdf_letterbox(post):
    d = delivery(post)
    li = post.nets.get("linkedin", {})
    if not (li.get("pdf") or li.get("pdf_title")):
        return na("no LinkedIn document")
    if not d or "linkedin_pdf_letterboxed" not in d:
        return na("runs after the first PDF post (open the live post and record linkedin_pdf_letterboxed)")
    return ok(not d["linkedin_pdf_letterboxed"], "")


@check("QM11", scope="post")
def rehosted_clean(post):
    d = delivery(post)
    if not d or "rehosted_metadata_clean" not in d:
        return na("runs after scheduling")
    return ok(d["rehosted_metadata_clean"], "")
