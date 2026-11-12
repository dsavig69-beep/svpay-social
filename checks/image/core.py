"""Shared measurement for the image checks: the slide context, the render passes, delivery simulations, color math,
OCR and the rule that an estimate never clears a gate.

Passes (written by pipeline/render.js and pipeline/run3d.js with --passes) live next to the slides in
posts/<folder>/passes/:
  <slide>.manifest.json, <slide>.bg.png, <slide>.mask.png, <slide>.unclipped.png   (HTML layer, render.js)
  <hero>.id.png, .idj.png, .cover.png, .normal.png, .shadow.png, .scene.json       (3D layer, run3d.js)
The manifest names the hero a slide uses, which links a slide to its 3D passes.
"""
from __future__ import annotations

import functools
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
MASTER_W, MASTER_H = 2160, 2700
NEEDS = "needs_passes"


# ------------------------------------------------------------------ results
class Outcome:
    """What a check returns: ok True/False/None (na) or NEEDS, with a detail and a value."""
    __slots__ = ("ok", "detail", "value", "where", "estimated")

    def __init__(self, ok, detail="", value=None, where=None, estimated=False):
        self.ok, self.detail, self.value, self.where, self.estimated = ok, detail, value, where, estimated


def na(why):
    return Outcome(None, why)


def ok(cond, detail="", value=None):
    return Outcome(bool(cond), detail, value)


def estimate(cond, detail, value=None):
    """A result measured without the render passes. It may fail a check; for a gate it can never pass (ruling 6)."""
    return Outcome(bool(cond), "estimate (no render passes): " + detail, value, estimated=True)


# ------------------------------------------------------------------ color math
def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rel_lum(rgb):
    """WCAG relative luminance of 8-bit sRGB (array [..., 3])."""
    lin = srgb_to_linear(rgb)
    return lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722


def wcag(y1, y2):
    hi, lo = max(y1, y2), min(y1, y2)
    return (hi + 0.05) / (lo + 0.05)


def apca_y(rgb):
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    return 0.2126729 * c[..., 0] ** 2.4 + 0.7151522 * c[..., 1] ** 2.4 + 0.0721750 * c[..., 2] ** 2.4


def apca(txt_y, bg_y):
    """APCA-W3 0.1.9 lightness contrast Lc (positive: dark text on a light background). Black on white is about 106."""
    blk_thrs, blk_clmp = 0.022, 1.414
    t = txt_y if txt_y > blk_thrs else txt_y + (blk_thrs - txt_y) ** blk_clmp
    b = bg_y if bg_y > blk_thrs else bg_y + (blk_thrs - bg_y) ** blk_clmp
    if abs(b - t) < 0.0005:
        return 0.0
    if b > t:
        s = (b ** 0.56 - t ** 0.57) * 1.14
        return 0.0 if s < 0.1 else (s - 0.027) * 100
    s = (b ** 0.65 - t ** 0.62) * 1.14
    return 0.0 if s > -0.1 else (s + 0.027) * 100


def to_lab(rgb):
    from skimage.color import rgb2lab
    return rgb2lab(np.asarray(rgb, dtype=np.float64) / 255.0)


def de00(lab1, lab2):
    from skimage.color import deltaE_ciede2000
    return np.squeeze(deltaE_ciede2000(lab1, lab2))


def lstar(rgb):
    y = rel_lum(rgb)
    return np.where(y > 0.008856, 116 * np.cbrt(y) - 16, 903.3 * y)


# Machado 2009 severity 1.0 matrices (linear RGB)
CVD = {
    "protan": np.array([[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]]),
    "deutan": np.array([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]]),
    "tritan": np.array([[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]]),
}
# Vienot 1999 (protan, deutan) for the second model (QA04)
VIENOT = {
    "protan": np.array([[0.11238, 0.88762, 0.0], [0.11238, 0.88762, 0.0], [0.00401, -0.00401, 1.0]]),
    "deutan": np.array([[0.29275, 0.70725, 0.0], [0.29275, 0.70725, 0.0], [-0.02234, 0.02234, 1.0]]),
}


def simulate_cvd(rgb, kind, model=CVD):
    lin = srgb_to_linear(rgb)
    out = np.clip(lin @ model[kind].T, 0, 1)
    s = np.where(out <= 0.0031308, out * 12.92, 1.055 * out ** (1 / 2.4) - 0.055)
    return np.clip(s * 255, 0, 255)


# ------------------------------------------------------------------ images
def load_rgb(path):
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def jpeg_roundtrip(arr, quality=75, subsampling=2):
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=quality, subsampling=subsampling)
    buf.seek(0)
    return np.asarray(Image.open(buf).convert("RGB"))


def resize_area(arr, w, h):
    if cv2 is not None:
        return cv2.resize(arr, (w, h), interpolation=cv2.INTER_AREA)
    return np.asarray(Image.fromarray(arr).resize((w, h), Image.BOX))


def gray(arr):
    return (arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114).astype(np.float32)


@functools.lru_cache(maxsize=64)
def _ocr_cached(key, psm, whitelist):
    arr = _OCR_STORE.pop(key)
    return _ocr(arr, psm, whitelist)


_OCR_STORE = {}


def tesseract_ok():
    return shutil.which("tesseract") is not None


def _ocr(arr, psm=11, whitelist=None):
    import pytesseract
    cfg = f"--psm {psm}"
    if whitelist:
        cfg += f" -c tessedit_char_whitelist={whitelist}"
    d = pytesseract.image_to_data(Image.fromarray(arr), config=cfg, output_type=pytesseract.Output.DICT)
    words = []
    for i, t in enumerate(d["text"]):
        if t.strip():
            words.append({"text": t.strip(), "conf": float(d["conf"][i]), "x": d["left"][i], "y": d["top"][i], "w": d["width"][i], "h": d["height"][i]})
    return words


def ocr(arr, psm=11, whitelist=None, key=None):
    """Tesseract words with boxes. Returns None when tesseract is not installed (checks then say 'needs OCR')."""
    if not tesseract_ok():
        return None
    if key is None:
        return _ocr(arr, psm, whitelist)
    _OCR_STORE[key] = arr
    return _ocr_cached(key, psm, whitelist)


def ocr_text(arr, psm=6, whitelist=None):
    words = ocr(arr, psm, whitelist)
    return None if words is None else " ".join(w["text"] for w in words)


def norm_words(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-").replace("×", "x").replace("÷", "/")
    return [w for w in re.sub(r"[^A-Za-z0-9$%.,'/-]+", " ", s.lower()).split() if w.strip(".,'-")]


def word_match_rate(expected, got):
    e = [w.strip(".,'\"") for w in norm_words(expected)]
    g = [w.strip(".,'\"") for w in norm_words(got)]
    if not e:
        return 1.0
    pool = list(g)
    hit = 0
    for w in e:
        if w in pool:
            pool.remove(w)
            hit += 1
    return hit / len(e)


def cer(expected, got):
    a = re.sub(r"\s+", " ", expected.strip().lower())
    b = re.sub(r"\s+", " ", got.strip().lower())
    if not a:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1] / len(a)


# ------------------------------------------------------------------ fonts
FONT_CACHE = os.path.join(tempfile.gettempdir(), "svpay-checks-fonts")


def font_file(src, base_dir):
    """A TrueType path Pillow can open for a manifest font src (woff2 -> the woff sibling -> ttf)."""
    if not src:
        return None
    p = os.path.normpath(os.path.join(base_dir, src))
    if p.endswith(".woff2") and os.path.exists(p[:-1]):
        p = p[:-1]
    if not os.path.exists(p):
        return None
    if p.endswith((".ttf", ".otf")):
        return p
    os.makedirs(FONT_CACHE, exist_ok=True)
    out = os.path.join(FONT_CACHE, os.path.basename(p).rsplit(".", 1)[0] + ".ttf")
    if not os.path.exists(out):
        from fontTools.ttLib import TTFont
        f = TTFont(p)
        f.flavor = None
        f.save(out)
    return out


# ------------------------------------------------------------------ slides and passes
class Slide:
    def __init__(self, post, idx, spec, file):
        self.post, self.idx, self.spec, self.file = post, idx, spec, file
        self.stem = os.path.splitext(os.path.basename(file))[0]
        self.where = self.stem.replace("slide-", "slide ") if self.stem.startswith("slide-") else self.stem
        self.dir = os.path.dirname(file)
        self.pdir = os.path.join(self.dir, "passes")
        self.role = spec.get("role")
        self.text = spec.get("text", "")
        self._c = {}

    def release(self):
        """Drop the full-size images; keep the small parsed files (manifest, scene)."""
        keep = {k: v for k, v in self._c.items() if k in ("man", "scene")}
        self._c = keep

    # ---- files
    def _p(self, name):
        return os.path.join(self.pdir, f"{self.stem}.{name}")

    @property
    def manifest(self):
        if "man" not in self._c:
            p = self._p("manifest.json")
            self._c["man"] = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
        return self._c["man"]

    @property
    def hero_stem(self):
        m = self.manifest
        if not m or not m.get("hero"):
            return None
        return os.path.splitext(os.path.basename(m["hero"]))[0]

    def p3(self, name):
        h = self.hero_stem
        return os.path.join(self.pdir, f"{h}.{name}") if h else None

    @property
    def scene(self):
        if "scene" not in self._c:
            p = self.p3("scene.json")
            self._c["scene"] = json.load(open(p, encoding="utf-8")) if p and os.path.exists(p) else None
        return self._c["scene"]

    def has(self, *names):
        for n in names:
            if n in ("bg", "mask", "unclipped"):
                if not os.path.exists(self._p(n + ".png")) and not (n == "bg" and os.path.exists(self._p("bg.jpg"))):
                    return False
            elif n == "manifest":
                if self.manifest is None:
                    return False
            elif n in ("id", "idj", "cover", "normal", "shadow"):
                p = self.p3(n + ".png")
                if not p or not os.path.exists(p):
                    return False
            elif n == "scene":
                if self.scene is None:
                    return False
        return True

    @property
    def is3d(self):
        """True when the slide carries a 3D hero (a hero render with objects in it)."""
        sc = self.scene
        if sc is not None:
            return bool(sc.get("objects"))
        m = self.manifest
        if m is not None:
            return bool(m.get("hero")) and "hero" in os.path.basename(m.get("hero") or "")
        return None

    @property
    def final(self):
        if "final" not in self._c:
            self._c["final"] = load_rgb(self.file)
        return self._c["final"]

    @property
    def size(self):
        return self.final.shape[1], self.final.shape[0]

    @property
    def scale(self):
        return self.final.shape[1] / MASTER_W

    def img(self, name):
        key = "img:" + name
        if key not in self._c:
            p = self._p(name + ".png") if name in ("bg", "mask", "unclipped") else self.p3(name + ".png")
            if name == "bg" and not os.path.exists(p):
                p = self._p("bg.jpg")
            self._c[key] = load_rgb(p) if p and os.path.exists(p) else None
        return self._c[key]

    def raw(self, name):
        key = "raw:" + name
        if key not in self._c:
            p = self.p3(name + ".png")
            self._c[key] = np.asarray(Image.open(p)) if p and os.path.exists(p) else None
        return self._c[key]

    # ---- masks
    @property
    def text_mask(self):
        """Glyph mask (bool). From the mask pass when present, else an estimate from the final image."""
        if "tm" not in self._c:
            if self.has("mask"):
                m = self.img("mask")
                self._c["tm"] = (gray(m) < 128)
            else:
                self._c["tm"] = None
        return self._c["tm"]

    def element_mask(self, el, src="mask"):
        m = self.text_mask if src == "mask" else (gray(self.img("unclipped")) < 128)
        out = np.zeros(m.shape, bool)
        for ln in el["lines"]:
            x0, y0 = int(max(0, ln["x"] - 4)), int(max(0, ln["y"] - 4))
            x1, y1 = int(min(m.shape[1], ln["x"] + ln["w"] + 4)), int(min(m.shape[0], ln["y"] + ln["h"] + 4))
            out[y0:y1, x0:x1] = m[y0:y1, x0:x1]
        return out

    @property
    def elements(self):
        m = self.manifest
        return m["text"] if m else []

    # ---- 3D
    @property
    def id_map(self):
        if "idm" not in self._c:
            a = self.raw("id")
            self._c["idm"] = a
        return self._c["idm"]

    def parts(self):
        sc = self.scene
        return sc.get("parts", []) if sc else []

    def objects(self):
        sc = self.scene
        return sc.get("objects", []) if sc else []

    def cover(self, part):
        """Full silhouette (bool) of one 3D part, ignoring the other parts."""
        k = part - 1
        name = "cover" if k < 32 else f"cover{k // 32 + 1}"
        j = k % 32
        a = self.raw(name)
        if a is None:
            return None
        if j < 24:
            return (a[..., j >> 3] & (1 << (j & 7))) > 0
        b = self.raw(name + "a")
        return None if b is None else (b[..., 0] & (1 << (j & 7))) > 0

    def visible_part(self, part, group=None):
        a = self.id_map
        if a is None:
            return None
        m = a[..., 0] == part
        if group is not None:
            m &= a[..., 1] == group
        return m

    # ---- delivery simulations (QF17, T-J03): 1080 wide area downscale, JPEG q75 4:2:0; twice for SIM2
    @property
    def sim1(self):
        if "sim1" not in self._c:
            h = round(self.final.shape[0] * 1080 / self.final.shape[1])
            self._c["sim1"] = jpeg_roundtrip(resize_area(self.final, 1080, h), 75, 2)
        return self._c["sim1"]

    @property
    def sim2(self):
        if "sim2" not in self._c:
            self._c["sim2"] = jpeg_roundtrip(jpeg_roundtrip(self.sim1, 80, 2), 75, 2)
        return self._c["sim2"]

    def scaled(self, w):
        key = f"sc{w}"
        if key not in self._c:
            h = round(self.final.shape[0] * w / self.final.shape[1])
            self._c[key] = resize_area(self.final, w, h)
        return self._c[key]

    def ocr_words(self, which="final", psm=11):
        key = f"ocr:{which}:{psm}"
        if key not in self._c:
            arr = {"final": self.final, "sim1": self.sim1}.get(which)
            if arr is None:
                arr = self.scaled(int(which))
            self._c[key] = ocr(arr, psm)
        return self._c[key]

    def ocr_all_text(self):
        w = self.ocr_words("final")
        return None if w is None else " ".join(x["text"] for x in w)

    def intended_text(self):
        """The slide's words: the manifest's text when the passes exist, else the package's slide text."""
        if self.manifest:
            return "\n".join(e["text"] for e in self.elements)
        return self.text


class Post:
    """The package plus its slides (from pkg['slides'] and each network's media)."""

    def __init__(self, pkg, base_dir=None, history=None, extra=None):
        self.pkg = pkg
        self.base = base_dir or REPO
        self.history = history or []
        self.extra = extra or {}
        self.nets = pkg.get("networks", {})
        self.date = pkg.get("date", "")
        self.slides = []
        seen = {}
        for i, s in enumerate(pkg.get("slides", []), 1):
            f = self.path(s.get("file", ""))
            if f and os.path.exists(f):
                sl = Slide(self, i, s, f)
                self.slides.append(sl)
                seen[os.path.abspath(f)] = sl
        # single-image variants a network uses (slide-1-single.jpg, slide-1-gbp.jpg) that are not in pkg['slides']
        self.variants = {}
        for net, v in self.nets.items():
            for m in v.get("media", []):
                f = self.path(m)
                if not f or not os.path.exists(f):
                    continue
                a = os.path.abspath(f)
                if a not in seen:
                    first = pkg.get("slides", [{}])[0] if pkg.get("slides") else {}
                    sl = Slide(self, 0, dict(first, role="single"), f)
                    seen[a] = sl
                    self.variants[a] = sl
        self.by_file = seen

    def path(self, p):
        if not p:
            return None
        return p if os.path.isabs(p) else os.path.join(self.base, p)

    def media(self, net):
        return [self.by_file.get(os.path.abspath(self.path(m))) for m in self.nets.get(net, {}).get("media", []) if self.path(m) and os.path.exists(self.path(m))]

    def all_images(self):
        return list(self.by_file.values())

    def release(self):
        for s in self.by_file.values():
            s.release()

    @property
    def deck_text(self):
        return "\n".join(s.text for s in self.slides)


def png_bytes_info(path):
    """Format, mode, size, JPEG quality estimate and subsampling, ICC and metadata presence for an image file."""
    info = {"bytes": os.path.getsize(path)}
    with open(path, "rb") as f:
        head = f.read(16)
    info["magic"] = "JPEG" if head[:3] == b"\xff\xd8\xff" else "PNG" if head[:8] == b"\x89PNG\r\n\x1a\n" else "other"
    with Image.open(path) as im:
        info.update(format=im.format, mode=im.mode, size=im.size, icc=bool(im.info.get("icc_profile")), exif=bool(im.info.get("exif")) or bool(im.getexif()),
                    xmp=bool(im.info.get("xmp")) or b"http://ns.adobe.com/xap" in open(path, "rb").read(65536), dpi=im.info.get("dpi"))
        if im.format == "JPEG":
            try:
                from PIL import JpegImagePlugin
                info["subsampling"] = JpegImagePlugin.get_sampling(im)  # 0 = 4:4:4, 1 = 4:2:2, 2 = 4:2:0
                q = im.quantization
                if q:
                    t = q[0]
                    s = sum(t) / len(t)
                    # standard luminance table average is about 57.6 at q50
                    scale = s * 100 / 57.625
                    info["quality"] = int(round((200 - scale) / 2 if scale <= 100 else 5000 / scale))
            except Exception:
                pass
        if im.mode in ("RGBA", "LA") or "transparency" in im.info:
            a = np.asarray(im.convert("RGBA"))[..., 3]
            info["alpha_min"] = int(a.min())
    info["app_segments"] = jpeg_segments(path) if info["magic"] == "JPEG" else []
    return info


def jpeg_segments(path):
    segs = []
    with open(path, "rb") as f:
        data = f.read()
    i = 2
    while i < len(data) - 4:
        if data[i] != 0xFF:
            break
        m = data[i + 1]
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        ln = int.from_bytes(data[i + 2:i + 4], "big")
        if 0xE0 <= m <= 0xEF:
            segs.append("APP%d" % (m - 0xE0) + (":" + data[i + 4:i + 10].split(b"\0")[0].decode("latin1", "ignore") if ln > 6 else ""))
        elif m == 0xFE:
            segs.append("COM")
        if m == 0xDA:
            break
        i += 2 + ln
    return segs


def phash(arr, size=8):
    """64-bit DCT perceptual hash."""
    g = gray(arr)
    g = cv2.resize(g, (32, 32), interpolation=cv2.INTER_AREA)
    d = cv2.dct(g.astype(np.float32))[:size, :size]
    med = np.median(d.flatten()[1:])
    return (d > med).flatten()


def hamming(a, b):
    return int(np.count_nonzero(a != b))


def euler(mask):
    """Components minus holes of a binary mask (8-connected foreground, 4-connected background)."""
    from skimage.measure import label
    m = np.pad(mask.astype(np.uint8), 1)
    comps = label(m, connectivity=2).max()
    bg = label(1 - m, connectivity=1)
    holes = bg.max() - 1
    return comps - holes, comps, holes
