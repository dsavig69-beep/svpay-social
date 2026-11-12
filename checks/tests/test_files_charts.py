"""File specs, pre-sizing, metadata, network subsets (checks/image/files.py) and declared charts (charts.py), on
synthetic files and packages."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import check_post  # noqa: E402
import image  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="svpay-files-")
N = [0]


def img(w, h, fmt="JPEG", q=92, sub=0, exif=None, mode="RGB", noise=True, name=None):
    N[0] += 1
    p = os.path.join(TMP, name or f"f{N[0]}.{'jpg' if fmt == 'JPEG' else 'png'}")
    rng = np.random.default_rng(N[0])
    a = (rng.integers(0, 255, (h, w, 3)) if noise else np.full((h, w, 3), 240)).astype(np.uint8)
    im = Image.fromarray(a).convert(mode)
    if mode == "RGBA":
        im.putalpha(200)
    kw = {"quality": q, "subsampling": sub} if fmt == "JPEG" else {}
    if exif is not None:
        kw["exif"] = exif
    im.save(p, fmt, **kw)
    return p


def pkg(nets, slides=None, **top):
    d = {"date": "2026-11-12", "kind": "news", "format": "news", "facts": [], "math": [], "networks": nets, "slides": slides or []}
    d.update(top)
    return d


def run(p, cid):
    rep = check_post.Report()
    image.run(rep, p, base_dir=REPO, extra={}, only={cid})
    rows = [r for r in rep.results if r["id"] == cid]
    if any(r["status"] == "fail" for r in rows):
        return "fail"
    if any(r["status"] == "pass" for r in rows):
        return "pass"
    return "na"


def gps_exif():
    e = Image.Exif()
    e[0x010F] = "PhoneMaker"
    e[0x8825] = {1: "N", 2: (44.0, 47.0, 0.0)}
    return e.tobytes()


M1440 = lambda **k: img(1440, 1800, **k)
M2160 = lambda **k: img(2160, 2700, **k)

ROWS = [
    ("QF01", pkg({"instagram": {"media": [M1440()]}}), pkg({"instagram": {"media": [M2160()]}})),
    ("QF02", pkg({"instagram": {"media": [M1440()]}}), pkg({"instagram": {"media": [img(1440, 1800, fmt="PNG")]}})),
    ("QF03", pkg({"instagram": {"media": [M1440()]}}), pkg({"instagram": {"media": [img(1350, 1800)]}})),
    ("QF04", pkg({"linkedin": {"media": [img(1440, 1800, noise=False)]}}), pkg({"linkedin": {"media": [img(2400, 3000, fmt="PNG")]}})),
    ("QF05", pkg({"gbp": {"media": [img(1200, 900)]}}), pkg({"gbp": {"media": [img(200, 150, noise=False, q=40)]}})),
    ("QF06", pkg({"linkedin": {"media": [M1440()]}}), pkg({"linkedin": {"media": [img(6100, 6000, noise=False, q=30)]}})),
    ("QF07", pkg({"linkedin": {"media": [M1440()]}}), pkg({"linkedin": {"media": [img(1200, 300, noise=False)]}})),
    ("QF08", pkg({"x": {"media": [M1440()]}}), pkg({"x": {"media": [img(1440, 1800, fmt="WEBP", name="w.webp")]}})),
    ("QF09", pkg({"x": {"media": [M1440()]}}), pkg({"x": {"media": [img(1440, 1800, sub=2)]}})),
    ("QF11", pkg({"x": {"media": [M1440()]}}), pkg({"x": {"media": [img(1440, 1800, mode="L")]}})),
    ("QF12", pkg({"x": {"media": [img(1440, 1800, fmt="PNG")]}}), pkg({"x": {"media": [img(1440, 1800, fmt="PNG", mode="RGBA")]}})),
    ("QF29", pkg({"x": {"media": [M2160(noise=False)]}}), pkg({"x": {"media": [img(1080, 1350)]}})),
    ("QF30", pkg({"x": {"media": [M1440()]}}), pkg({"x": {"media": [img(1440, 1800, noise=False)]}})),
    ("QM01", pkg({"x": {"media": [M1440()]}}), pkg({"x": {"media": [img(1440, 1800, exif=gps_exif())]}})),
    ("QM02", pkg({"x": {"media": [M1440()]}}), pkg({"x": {"media": [img(1440, 1800, exif=gps_exif())]}})),
    ("QM06", pkg({"x": {"media": [M1440()]}}), pkg({"x": {"media": [img(1440, 1800, exif=gps_exif())]}})),
    ("V-A08", pkg({"x": {"media": [M1440()]}}), pkg({"x": {"media": [img(1440, 1800, q=50)]}})),
    ("F-GB01", pkg({"gbp": {"media": [img(1200, 900)]}}), pkg({"gbp": {"media": [M1440()]}})),
    ("F-NI15", pkg({"facebook": {"media": [M1440()]}}), pkg({"facebook": {"media": [M1440() for _ in range(7)]}})),
    ("F-X01", pkg({"x": {"media": [M1440()]}}), pkg({"x": {"media": [M1440(), M1440(), M1440()]}})),
    ("F-TH03", pkg({"threads": {"media": [M1440(), M1440()]}}), pkg({"threads": {"media": [M1440() for _ in range(6)]}})),
    ("F-LD01", pkg({"linkedin": {"pdf_title": "T", "pdf_pages": 10, "media": [M1440(), M1440()]}}), pkg({"linkedin": {"pdf_title": "T", "pdf_pages": 6, "media": [M1440(), M1440()]}})),
]


class Files(unittest.TestCase):
    pass


def _mk(cid, good, bad):
    def t(self):
        self.assertEqual(run(good, cid), "pass", f"{cid} passing case")
        self.assertEqual(run(bad, cid), "fail", f"{cid} failing case")
    return t


for _i, (_c, _g, _b) in enumerate(ROWS):
    setattr(Files, f"test_{_i:02d}_{_c.replace('-', '_')}", _mk(_c, _g, _b))


def chart(**k):
    c = {"slide": 1, "type": "column", "title": "Paid to lease vs to buy", "units": "$", "plot_box": {"x": 180, "y": 700, "w": 1800, "h": 1400},
         "marks": [{"value": 2256, "px": 1200, "thickness": 230, "label": "$2,256", "label_px": 110, "label_gap": 12},
                   {"value": 400, "px": 212.8, "thickness": 230, "label": "$400", "label_px": 110, "label_gap": 12}],
         "colors": [[255, 47, 143], [10, 18, 48]], "series": [{"name": "lease"}, {"name": "buy"}], "category_labels": ["lease", "buy new"],
         "axis": {"zero": True, "log": False, "dual": False, "break": False}}
    c.update(k)
    return c


CROWS = [
    ("MC-A01", [chart()], [chart(type="radar")]),
    ("MC-A02", [chart()], [chart(), chart(type="line")]),
    ("MC-A04", [chart(type="pie_2")], [chart(type="pie_2", marks=[{"value": 1, "label": "a"}, {"value": 2, "label": "b"}, {"value": 3, "label": "c"}])]),
    ("MC-A06", [chart()], [chart(plot_box={"x": 0, "y": 0, "w": 600, "h": 600})]),
    ("MC-A07", [chart()], [chart(marks=[{"value": 2256, "px": 1200, "thickness": 40, "label": "$2,256"}, {"value": 400, "px": 212.8, "thickness": 40, "label": "$400"}])]),
    ("MC-A08", [chart()], [chart(category_labels=["paid to lease the card machine for four years", "to buy"])]),
    ("MC-B01", [chart()], [chart(marks=[{"value": 2256, "px": 1200, "thickness": 230, "label": "$2,256"}, {"value": 400, "px": 600, "thickness": 230, "label": "$400"}])]),
    ("MC-B02", [chart()], [chart(axis={"zero": True, "break": True})]),
    ("MC-B06", [chart()], [chart(axis={"dual": True})]),
    ("MC-B07", [chart()], [chart(axis={"log": True})]),
    ("MC-B09", [chart()], [chart(marks=[{"value": 2256, "px": 500, "thickness": 230, "label": "$2,256"}, {"value": 400, "px": 88.7, "thickness": 230, "label": "$400"}])]),
    ("MC-C05", [chart()], [chart(logo_labels=True)]),
    ("MC-C06", [chart()], [chart(marks=[{"value": 1, "px": 1, "thickness": 230, "label": "$1", "label_gap": 60}])]),
    ("MC-G01", [chart()], [chart(colors=[[123, 200, 17], [35, 80, 255]])]),
    ("MC-J07", [chart()], [chart(marks=[{"value": 2256, "px": 1200, "thickness": 230, "label": "$2,256", "label_px": 110}, {"value": 400, "px": 212.8, "thickness": 120, "label": "$400", "label_px": 60}])]),
    ("F-IG26", [chart()], [chart(marks=[{"value": 1, "px": 1, "thickness": 230, "label": "$1", "label_px": 61}])]),
    ("QA02", [chart()], [chart(colors=[[205, 90, 40], [110, 135, 40]])]),
    ("QA03", [chart()], [chart(colors=[[255, 47, 143], [255, 60, 140]])]),
]


class Charts(unittest.TestCase):
    pass


def _mkc(cid, good, bad):
    def t(self):
        self.assertEqual(run(pkg({}, charts=good), cid), "pass", f"{cid} passing case")
        self.assertEqual(run(pkg({}, charts=bad), cid), "fail", f"{cid} failing case")
    return t


for _i, (_c, _g, _b) in enumerate(CROWS):
    setattr(Charts, f"test_{_i:02d}_{_c.replace('-', '_')}", _mkc(_c, _g, _b))

TESTED = sorted({r[0] for r in ROWS} | {r[0] for r in CROWS})


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
