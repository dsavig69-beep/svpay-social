"""V-series checks first measured in the Nov 11 round, and the remaining research checks (checks/image/vseries.py,
extras.py), on one synthetic post. Each case edits the post's manifest or package, runs one check, and restores it:
the good edit must pass and the bad edit must fail.
"""
import copy
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import check_post  # noqa: E402
import image  # noqa: E402
import synth  # noqa: E402
from image import extras  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="svpay-vseries-")
D = os.path.join(TMP, "post")
PKG = None
MAN = {}


def setUpModule():
    global PKG
    PKG, _ = synth.make_post(D)
    for s in (1, 2):
        MAN[s] = json.load(open(os.path.join(D, "passes", f"slide-{s}.manifest.json")))


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def el(text, role="body", x=144, y=1500, size=80, family="Inter", weight=500, color=(10, 18, 48), lines=None, **run):
    e = synth.text_el(900 + len(text), text, role, x, y, size, family, weight if (family, weight) in synth.FONTS else 500, color, lines=lines)
    e.pop("_rgb")
    e["weight"] = weight
    e["runs"][0].update(run, weight=weight)
    return e


def logo(src, x, y, w, h, natural=None):
    return {"src": src, "cls": "logo", "complete": True, "natural": natural or [w, h], "x": x, "y": y, "w": w, "h": h}


def run(cid, edit=None, extra=None, history=None):
    pkg = copy.deepcopy(PKG)
    mans = copy.deepcopy(MAN)
    if edit:
        edit(pkg, mans)
    for s, m in mans.items():
        json.dump(m, open(os.path.join(D, "passes", f"slide-{s}.manifest.json"), "w"))
    try:
        rep = check_post.Report()
        image.run(rep, pkg, base_dir=REPO, history=history, extra=extra or {}, only={cid})
    finally:
        for s, m in MAN.items():
            json.dump(m, open(os.path.join(D, "passes", f"slide-{s}.manifest.json"), "w"))
    st = [r["status"] for r in rep.results if r["id"] == cid]
    errs = [r["detail"] for r in rep.results if "check error" in str(r["detail"])]
    assert not errs, errs
    return "fail" if "fail" in st or "needs_passes" in st else "pass" if "pass" in st else "na"


def add(s, *els):
    return lambda p, m: m[s]["text"].extend(copy.deepcopy(list(els)))


def chain(*fs):
    def f(p, m):
        for g in fs:
            g(p, m)
    return f


def set_el(s, idx, **kv):
    def f(p, m):
        e = m[s]["text"][idx]
        for k, v in kv.items():
            if k.startswith("run_"):
                e["runs"][0][k[4:]] = v
            elif k == "dy":
                e["box"]["y"] += v
                for l in e["lines"]:
                    l["y"] += v
            elif k == "dx":
                e["box"]["x"] += v
                for l in e["lines"]:
                    l["x"] += v
            else:
                e[k] = v
    return f


def charts(*cs):
    return lambda p, m: p.__setitem__("charts", list(cs))


def nets(**kv):
    return lambda p, m: p["networks"].update(kv)


def media(i):
    return os.path.join(D, f"slide-{i + 1}.jpg")


BAR = {"slide": 1, "type": "column", "marks": [{"value": 2250, "px": 1125, "label": "$2,250", "label_px": 80}, {"value": 400, "px": 200, "label": "$400", "label_px": 80}],
       "colors": [[255, 47, 143], [10, 18, 48]], "axis": {"zero": True}}

TOAST = logo("toast-logo.png", 1300, 2300, 400, 100)
FOOTER_UP = set_el(1, 4, dy=-200)
FOOTER_UP2 = set_el(2, 4, dy=-200)

CASES = [
    # (id, good edit, bad edit)
    ("V-B01", None, set_el(1, 0, dx=-110)),
    ("V-B02", chain(FOOTER_UP, FOOTER_UP2, set_el(1, 3, dy=20), set_el(2, 3, dy=20)), chain(FOOTER_UP, FOOTER_UP2, set_el(1, 3, dy=20), set_el(2, 3, dy=20), set_el(1, 2, dx=-90))),
    ("V-B03", chain(FOOTER_UP, lambda p, m: m[1]["images"][0].update(y=2300)), None),
    ("V-B09", None, add(1, el("Cut by the edge", x=1900, size=90))),
    ("V-C05", None, set_el(1, 1, size=120)),
    ("V-C06", None, add(1, el("More words here", size=70, y=1400), el("And more", size=110, y=1200))),
    ("V-C07", None, set_el(1, 1, run_family="Roboto")),
    ("V-C08", None, add(1, el("Light", weight=300, y=1400), el("Book", weight=400, y=1200))),
    ("V-C09", None, add(2, el("This body line runs well past the forty character limit", y=1400))),
    ("V-C10", add(1, el("Line one of body two", lines=["Line one of", "body two"], y=1300, size=80, line_height=112)),
     add(1, el("Line one of body two", lines=["Line one of", "body two"], y=1300, size=80, line_height=84))),
    ("V-C11", add(1, el("Shops paid more than they knew", "headline", lines=["Shops paid more than", "they knew"], y=600, size=150, family="Inter Tight", weight=800)),
     add(1, el("Shops paid more than they knew", "headline", lines=["Shops paid more than they", "knew"], y=600, size=150, family="Inter Tight", weight=800))),
    ("V-C13", add(2, el("$2,250", y=1200, size=120, variant_numeric="tabular-nums"), el("$400", y=1400, size=120, variant_numeric="tabular-nums")),
     add(2, el("$2,250", y=1200, size=120), el("$400", y=1400, size=120))),
    ("V-C14", None, add(2, el("Paid $2250 in all", y=1400))),
    ("V-C15", add(2, el("Read the fine print", y=1400, text_transform="uppercase")), add(2, el("Read the fine print before signing", y=1400, text_transform="uppercase"))),
    ("V-C16", add(2, el("one two three", lines=["one", "two", "three"], y=1200)), add(2, dict(el("one two three", lines=["one", "two", "three"], y=1200), align="center"))),
    ("V-C19", None, add(2, el("Teh shop paid", y=1400))),
    ("V-D14", add(2, el("Blue", y=1400, color=(35, 80, 255))), add(2, el("Blue", y=1400, color=(60, 100, 235)))),
    ("V-D15", add(2, el("$2,250 more", y=1400, color=(220, 30, 30))), add(2, el("$2,250", y=1400, color=(220, 30, 30)))),
    ("V-E03", None, chain(set_el(2, 0, dx=60), set_el(2, 1, dx=60), set_el(2, 2, dx=60))),
    ("V-E04", None, chain(set_el(2, 0, dx=60), set_el(2, 1, dx=60), set_el(2, 2, dx=60))),
    ("V-E11", None, set_el(2, 3, dy=40)),
    ("V-E17", None, lambda p, m: m[1].update(hero=None)),
    ("V-E20", lambda p, m: [m[s]["panels"].append({"x": 100, "y": 100, "w": 200, "h": 200, "radius": 40, "backdrop": "none", "background": "white", "border": "none", "box_shadow": "none", "cls": "card"}) for s in (1, 2)], lambda p, m: [m[s]["panels"].append({"x": 100, "y": 100, "w": 200, "h": 200, "radius": r, "backdrop": "none", "background": "white", "border": "none", "box_shadow": "none", "cls": "card"}) for s, r in ((1, 10), (2, 20), (2, 30))]),
    ("V-F11", add(2, el("www.svpay.app", "cta", y=1400, size=110)), None),
    ("V-G05", add(2, el("Interchange (the card bank's cut) went up", y=1400)), add(2, el("Interchange went up again", y=1400))),
    ("V-G08", None, add(2, el("SVPay costs $9 a month", y=1400))),
    ("V-H06", charts(BAR), charts(dict(BAR, axis={"zero": False}))),
    ("V-H07", charts(BAR), charts(dict(BAR, colors=[[1, 2, 3], [4, 5, 6], [7, 8, 9]]))),
    ("V-H10", charts(BAR), charts(dict(BAR, marks=[dict(BAR["marks"][0], label_px=40), BAR["marks"][1]]))),
    ("V-H13", charts(BAR), charts(dict(BAR, marks=[BAR["marks"][0], dict(BAR["marks"][1], px=300)]))),
    ("V-H16", charts(BAR), charts(dict(BAR, marks=[BAR["marks"][0], dict(BAR["marks"][1], label="18%")]))),
    ("V-D16", charts(dict(BAR, colors=[[35, 80, 255], [230, 159, 0]])), charts(dict(BAR, colors=[[205, 90, 40], [110, 135, 40]]))),
    ("V-K03", add(1), lambda p, m: m[1]["images"][0].update(w=400)),
    ("V-K09", lambda p, m: m[1]["images"].append(logo("toast-logo.png", 1300, 2300, 200, 50)), lambda p, m: m[1]["images"].append(logo("toast-logo.png", 1300, 2200, 600, 150))),
    ("V-K12", chain(lambda p, m: m[1]["images"].append(TOAST), add(1, el("SVPay is not affiliated with Toast.", "source", y=2420, size=64))),
     lambda p, m: m[1]["images"].append(TOAST)),
    ("V-K04", lambda p, m: m[1]["images"].append(TOAST), chain(lambda p, m: m[1]["images"].append(TOAST), add(1, el("Close", x=1300, y=2410, size=60)))),
    ("V-K08", lambda p, m: m[1]["images"].extend([logo("visa-logo.png", 900, 2300, 200, 80), logo("mastercard-logo.png", 1300, 2300, 200, 80)]),
     lambda p, m: m[1]["images"].extend([logo("visa-logo.png", 900, 2300, 200, 80), logo("mastercard-logo.png", 1300, 2300, 300, 120)])),
    ("V-K11", add(2, el("Paid with Stripe", y=1400)), add(2, el("Paid with Stripe™", y=1400))),
    ("V-L05", lambda p, m: m[1]["panels"].append({"x": 100, "y": 100, "w": 500, "h": 500, "radius": 40, "backdrop": "blur(24px)", "background": "x", "border": "x", "box_shadow": "none", "cls": "glass"}),
     lambda p, m: m[1]["panels"].extend([{"x": 100 + k * 50, "y": 100, "w": 500, "h": 500, "radius": 40, "backdrop": "blur(24px)", "background": "x", "border": "x", "box_shadow": "none", "cls": "glass"} for k in range(3)])),
    ("V-N01", nets(linkedin={"text": "x", "media": [media(0)]}), nets(linkedin={"text": "x", "media": [], "link": "https://svpay.app"})),
    ("V-N02", None, nets(instagram={"text": "x", "media": [media(0)]})),
    ("V-N09", None, nets(x={"text": "x", "media": [media(1)]})),
    ("MC-F01", add(1, el("Example", "label", x=323, y=1660, size=50)), add(1, el("Example", "label", x=144, y=150, size=50))),
    ("MC-M03", add(2, dict(el("Fees at Toast went up", y=1400), runs=[dict(el("Fees at ")["runs"][0], text="Fees at  went up"), dict(el("Toast")["runs"][0], text="Toast")])),
     add(2, dict(el("Fees at Toast went up", y=1400), runs=[dict(el("Fees at ")["runs"][0], text="Fees at  went up"), dict(el("Toast")["runs"][0], text="Toast", color="rgb(255, 102, 0)")]))),
    ("MC-Q06", charts({"slide": 1, "type": "stars", "rating": 4.3, "fill": 0.86}), charts({"slide": 1, "type": "stars", "rating": 4.3, "fill": 0.9})),
    ("F-X08", nets(x={"text": "x", "media": [media(0)], "thread": [{"media": [media(0)]}, {"media": [media(1)]}]}),
     nets(x={"text": "x", "media": [media(0)], "thread": [{"media": [media(1)]}, {"media": [media(0)]}]})),
    ("F-NI02", lambda p, m: m[2]["images"].append({"src": "svpay-app-screenshot.png", "cls": "screenshot own", "natural": [1000, 800], "x": 300, "y": 600, "w": 1000, "h": 800}),
     lambda p, m: m[2]["images"].append({"src": "news-site.png", "cls": "screenshot", "natural": [1000, 800], "x": 300, "y": 600, "w": 1000, "h": 800})),
    ("F-NI03", lambda p, m: m[2]["images"].append({"src": "app.png", "cls": "device frame", "natural": [1170, 2532], "x": 500, "y": 200, "w": 1000, "h": 2165}),
     lambda p, m: m[2]["images"].append({"src": "app.png", "cls": "screen", "natural": [1170, 2532], "x": 500, "y": 200, "w": 1000, "h": 2165})),
    ("T-L10", chain(lambda p, m: m[2]["panels"].append({"x": 100, "y": 1100, "w": 1900, "h": 700, "radius": 40, "backdrop": "none", "background": "x", "border": "x", "box_shadow": "none", "cls": "card"}),
                    add(2, el("First paragraph", y=1150, size=60, line_height=84), el("Second paragraph", y=1300, size=60, line_height=84))),
     chain(lambda p, m: m[2]["panels"].append({"x": 100, "y": 1100, "w": 1900, "h": 700, "radius": 40, "backdrop": "none", "background": "x", "border": "x", "box_shadow": "none", "cls": "card"}),
           add(2, el("First paragraph", y=1150, size=60, line_height=84), el("Second paragraph", y=1230, size=60, line_height=84)))),
    ("T-K13", chain(add(2, el("$2,250", "headline", y=600, size=200), el("paid in all", "label", y=870, size=60)),
                    add(1, el("$400", "headline", y=600, size=200), el("to buy", "label", y=870, size=60))),
     chain(add(2, el("$2,250", "headline", y=600, size=200), el("paid in all", "label", y=900, size=60)),
           add(1, el("$400", "headline", y=600, size=200), el("to buy", "label", y=870, size=60)))),
    ("F-IG24", lambda p, m: (p.update(format="list"), [s.update(role="item", template="t1") for s in p["slides"]],
                             [m[k]["text"].append(el("1", "label", x=144, y=150, size=90)) for k in (1, 2)]),
     lambda p, m: (p.update(format="list"), [s.update(role="item", template="t1") for s in p["slides"]],
                   m[1]["text"].append(el("1", "label", x=144, y=150, size=90)), m[2]["text"].append(el("2", "label", x=244, y=150, size=90)))),
]


class Manifest(unittest.TestCase):
    pass


def _mk(cid, good, bad):
    def t(self):   # a missing edit means the clean post is that case
        self.assertEqual(run(cid, good), "pass", f"{cid} good case")
        self.assertEqual(run(cid, bad), "fail", f"{cid} bad case")
    return t


for _i, (_c, _g, _b) in enumerate(CASES):
    setattr(Manifest, f"test_{_i:02d}_{_c.replace('-', '_')}", _mk(_c, _g, _b))


# ------------------------------------------------------------------ files, PDF, scene, people, logs
def jpeg_with_app11(src, dst):
    data = open(src, "rb").read()
    seg = b"JP" + b"\x00\x01" + b"jumbc2pa" + b"\x00" * 16
    open(dst, "wb").write(data[:2] + b"\xff\xeb" + (len(seg) + 2).to_bytes(2, "big") + seg + data[2:])


class Files(unittest.TestCase):
    def test_c2pa(self):
        bad = os.path.join(D, "slide-1-c2pa.jpg")
        jpeg_with_app11(os.path.join(REPO, PKG["slides"][0]["file"]) if not os.path.isabs(PKG["slides"][0]["file"]) else PKG["slides"][0]["file"], bad)
        for cid in ("QM08", "V-A16"):
            self.assertEqual(run(cid), "pass", cid)
            self.assertEqual(run(cid, nets(x={"text": "x", "media": [bad]})), "fail", cid)

    def test_export_checksums(self):
        f = PKG["slides"][0]["file"]
        rows = [{"file": os.path.basename(f), "sha256": hashlib.sha256(open(f, "rb").read()).hexdigest()}]
        p = os.path.join(D, "files.json")
        json.dump({"files": rows}, open(p, "w"))
        self.assertEqual(run("V-A19"), "pass")
        rows[0]["sha256"] = "0" * 64
        json.dump({"files": rows}, open(p, "w"))
        self.assertEqual(run("V-A19"), "fail")
        os.remove(p)

    def test_pdf(self):
        pdf = os.path.join(D, "deck.pdf")
        ims = [Image.open(s["file"]).convert("RGB") for s in PKG["slides"]]
        ims[0].save(pdf, save_all=True, append_images=ims[1:], resolution=72)
        good = nets(linkedin={"text": "Shops paid $2,250.", "media": [], "pdf": pdf})
        for cid in ("V-A09", "V-A11", "V-A12", "F-LD06"):
            self.assertEqual(run(cid, good), "pass", cid)
        pdf2 = os.path.join(D, "deck2.pdf")
        ims[0].save(pdf2, save_all=True, append_images=[Image.new("RGB", (1000, 1000), "white")], resolution=72)
        bad = nets(linkedin={"text": "x", "media": [], "pdf": pdf2})
        self.assertEqual(run("V-A11", bad), "fail")
        self.assertEqual(run("F-LD06", bad), "fail")
        self.assertEqual(run("V-A09", nets(linkedin={"text": "x", "media": [], "pdf": os.path.join(D, "missing.pdf")})), "fail")

    def test_x_cut(self):
        sq = os.path.join(D, "slide-1-x.jpg")
        Image.open(PKG["slides"][0]["file"]).crop((0, 270, 2160, 2430)).save(sq, quality=92)
        self.assertEqual(run("V-B04", nets(x={"text": "x", "media": [sq]})), "pass")
        self.assertEqual(run("V-B04", set_el(1, 0, dy=-200)), "fail")

    def test_avatar(self):
        for pad, want in ((0.3, "pass"), (0.02, "fail")):
            a = Image.new("RGB", (400, 400), "white")
            r = int(200 * (1 - pad))
            from PIL import ImageDraw
            ImageDraw.Draw(a).rectangle((200 - r * 0.7, 200 - r * 0.7, 200 + r * 0.7, 200 + r * 0.7), fill=(35, 80, 255))
            p = os.path.join(D, f"avatar-{want}.png")
            a.save(p)
            self.assertEqual(run("V-B07", lambda pk, m: pk.update(avatar=p)), want)

    def test_report_written(self):
        out = os.path.join(TMP, "report")
        self.assertEqual(run("V-M07", extra={"report_dir": out}), "pass")
        self.assertTrue(os.path.exists(os.path.join(out, "contrast-size.json")))

    def test_caption_numbers(self):
        pdf = os.path.join(D, "deck.pdf")
        if not os.path.exists(pdf):
            Image.open(PKG["slides"][0]["file"]).convert("RGB").save(pdf, resolution=72)
        self.assertEqual(run("V-M04", nets(linkedin={"text": "No numbers here.", "media": [], "pdf": pdf})), "fail")


class Scene(unittest.TestCase):
    def scene_edit(self, cid, path, val):
        p = os.path.join(D, "passes", "hero_1.scene.json")
        orig = open(p).read()
        sc = json.loads(orig)
        cur = sc
        keys = path.split(".")
        for k in keys[:-1]:
            cur = cur[int(k)] if k.isdigit() else cur[k]
        cur[int(keys[-1]) if keys[-1].isdigit() else keys[-1]] = val
        json.dump(sc, open(p, "w"))
        try:
            return run(cid)
        finally:
            open(p, "w").write(orig)

    def test_focal_length(self):
        self.assertEqual(run("V-I12"), "pass")
        self.assertEqual(self.scene_edit("V-I12", "camera.fov", 90), "fail")

    def test_micro_variation(self):
        p = os.path.join(D, "passes", "hero_1.scene.json")
        orig = open(p).read()
        sc = json.loads(orig)
        for o in sc["objects"]:
            o["material"][0]["normal_map"] = True
        json.dump(sc, open(p, "w"))
        try:
            self.assertEqual(run("V-I03"), "pass")
        finally:
            open(p, "w").write(orig)
        self.assertEqual(run("V-I03"), "fail")

    def test_post_effects(self):
        self.assertEqual(run("V-I15"), "pass")
        self.assertEqual(self.scene_edit("V-I15", "renderer.post_passes", ["bloom", "grain", "SMAA"]), "fail")


class People(unittest.TestCase):
    """A photo with a face (scikit-image's astronaut) pasted into slide 2."""

    @classmethod
    def setUpClass(cls):
        from skimage import data
        f = PKG["slides"][1]["file"]
        cls.orig = open(f, "rb").read()
        im = Image.open(f).convert("RGB")
        face = Image.fromarray(data.astronaut()).resize((900, 900))
        im.paste(face, (600, 700))
        im.save(f, quality=92, subsampling=0)
        cls.photo = {"src": "shop-owner.jpg", "cls": "photo", "complete": True, "natural": [900, 900], "x": 600, "y": 700, "w": 900, "h": 900}

    @classmethod
    def tearDownClass(cls):
        open(PKG["slides"][1]["file"], "wb").write(cls.orig)

    def with_photo(self, *more):
        return chain(lambda p, m: m[2]["images"].append(self.photo), *more)

    def test_release_ledger(self):
        rel = {"image": "shop-owner.jpg", "id": "R-1", "uses": ["paid", "organic"], "networks": ["instagram", "x"], "expires": "2027-12-31", "people": 1}
        good = self.with_photo(lambda p, m: p.update(releases=[rel]))
        bad = self.with_photo()
        for cid in ("MC-O01", "MC-O02", "MC-O05"):
            self.assertEqual(run(cid, good), "pass", cid)
            self.assertEqual(run(cid, bad), "fail", cid)
        self.assertEqual(run("MC-O05", self.with_photo(lambda p, m: p.update(releases=[dict(rel, expires="2026-01-01")]))), "fail")

    def test_face_size(self):
        self.assertEqual(run("V-J06", self.with_photo()), "pass")

    def test_photo_sharpness(self):
        from skimage import data
        f = PKG["slides"][1]["file"]
        im = Image.open(f).convert("RGB")
        im.paste(Image.fromarray(data.chelsea()), (600, 1650))          # native size: an upscaled photo is soft
        im.save(f, quality=92, subsampling=0)
        cat = dict(self.photo, src="cat.jpg", y=1650, w=451, h=300)
        self.assertEqual(run("V-J04", lambda p, m: m[2]["images"].append(cat)), "pass")
        blurry = dict(self.photo, x=1600, y=520, w=300, h=150, src="blurry.jpg")
        self.assertEqual(run("V-J04", lambda p, m: m[2]["images"].append(blurry)), "fail")


class Logs(unittest.TestCase):
    def test_shop_permission(self):
        perm = os.path.join(D, "permission.pdf")
        open(perm, "w").write("signed")
        self.assertEqual(run("MC-O06", lambda p, m: p.update(real_shops=[{"ref": "shop-1", "permission_file": perm}])), "pass")
        self.assertEqual(run("MC-O06", lambda p, m: p.update(real_shops=[{"ref": "shop-1"}])), "fail")

    def test_claim_rechecks(self):
        log = [{"stage": "render", "at": "2026-11-11T09:00:00", "match": True, "hash": "a"}, {"stage": "pre_publish", "at": "2026-11-12T08:00:00", "match": True, "hash": "b"}]
        self.assertEqual(run("MC-S12", lambda p, m: p.update(claim_checks=log, publish_at="2026-11-12T12:00:00")), "pass")
        self.assertEqual(run("MC-S12", lambda p, m: p.update(claim_checks=log[:1])), "fail")

    def test_free_offer(self):
        txt = add(2, el("Free for 3 months with an annual plan", y=1400))
        hist = [{"date": f"2026-{m:02d}-05", "text": "Free for 3 months with an annual plan"} for m in range(1, 10)]
        self.assertEqual(run("MC-K06", txt, history=hist[:3]), "pass")
        self.assertEqual(run("MC-K06", txt, history=hist), "fail")

    def test_grids(self):
        tok = os.path.join(TMP, "tokens.json")
        json.dump({"grid": {"columns": 12, "margin": 144, "gutter": 48}, "baseline": 4}, open(tok, "w"))
        old = extras.TOKENS
        extras.TOKENS = tok
        try:
            self.assertEqual(run("QB14"), "pass")
            self.assertEqual(run("QB14", set_el(2, 0, dx=30)), "fail")
        finally:
            extras.TOKENS = old


if __name__ == "__main__":
    unittest.main()
