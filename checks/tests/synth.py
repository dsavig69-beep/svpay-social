"""Synthetic posts for the image-check unit tests: a 2160x2700 slide drawn with Pillow in the brand fonts, with the
same render passes the pipeline writes (bg, mask, unclipped, manifest; id, idj, cover, normal, shadow, scene.json).

make_post(dirname, **opts) builds a clean post that the image gates pass. Each option switches on one defect, so a
test flips one option and expects the matching check to fail.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FONTS = {
    ("Inter Tight", 800): "node_modules/@fontsource/inter-tight/files/inter-tight-latin-800-normal.woff",
    ("Inter Tight", 700): "node_modules/@fontsource/inter-tight/files/inter-tight-latin-700-normal.woff",
    ("Inter", 500): "node_modules/@fontsource/inter/files/inter-latin-500-normal.woff",
    ("Inter", 600): "node_modules/@fontsource/inter/files/inter-latin-600-normal.woff",
}
W, H = 2160, 2700


def ttf(family, weight):
    import sys
    sys.path.insert(0, os.path.join(REPO, "checks"))
    from image.core import font_file
    return font_file(FONTS[(family, weight)], REPO)


def font(family, weight, size):
    return ImageFont.truetype(ttf(family, weight), int(size))


def background(kind="aurora"):
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    base = np.stack([246 + 0 * x, 247 + 0 * x, 252 + 0 * x], -1)
    if kind == "aurora":
        blob = np.exp(-(((x - 1500) / 700) ** 2 + ((y - 1200) / 500) ** 2))
        base += blob[..., None] * np.array([-30, -40, -5])
        blob2 = np.exp(-(((x - 500) / 600) ** 2 + ((y - 2000) / 500) ** 2))
        base += blob2[..., None] * np.array([-10, -25, -2])
        rng = np.random.default_rng(1)
        base += rng.normal(0, 0.8, base.shape)
    elif kind == "dark":
        base = np.stack([20 + 0 * x, 24 + 0 * x, 48 + 0 * x], -1)
    return np.clip(base, 0, 255).astype(np.uint8)


def char_boxes(fnt, text, x, y):
    out, cx = [], x
    for ch in text:
        adv = fnt.getlength(ch)
        out.append([ch, round(cx, 1), y, round(adv, 1), fnt.size * 1.2])
        cx += adv
    return out


def text_el(i, text, role, x, y, size, family="Inter", weight=500, color=(10, 18, 48), wrap=None, lines=None, letter=0.0, transform=None, opacity=1.0):
    f = font(family, weight, size)
    lines = lines or [text]
    run_text = text
    recs = []
    lh = size * 1.25
    for k, ln in enumerate(lines):
        ly = y + k * lh
        w = f.getlength(ln)
        recs.append({"text": ln, "x": x, "y": ly, "w": w, "h": lh, "chars": char_boxes(f, ln, x, ly)})
    box = {"x": x, "y": y, "w": max(r["w"] for r in recs), "h": lh * len(lines)}
    run = {"mid": str(i), "text": run_text, "family": f'"{family}", sans-serif', "weight": weight, "style": "normal", "size": size, "letter_spacing": letter * size,
           "line_height": lh, "color": "rgb(%d, %d, %d)" % color, "fill": "rgb(%d, %d, %d)" % color, "gradient_text": None, "opacity": opacity, "blend": "normal",
           "text_shadow": "none", "stroke": "0px", "transform": transform or "none", "features": "normal", "variant_numeric": "normal", "text_transform": "none",
           "decoration": "none", "hyphens": "manual"}
    return {"id": i, "mids": [str(i)], "role": role, "text": text, "lines": recs, "box": box, "container": dict(box), "clip": {"x": 0, "y": 0, "w": W, "h": H},
            "align": "left", "white_space": "normal", "size": size, "family": run["family"], "weight": weight, "color": run["color"], "runs": [run], "html_layer": True,
            "platform_fonts": [{"familyName": family, "isCustomFont": True, "glyphCount": len(text)}], "_rgb": color}


def draw_text(img, els, mask=None, clip_rect=None):
    d = ImageDraw.Draw(img)
    dm = ImageDraw.Draw(mask) if mask is not None else None
    for e in els:
        f = font(e["family"].split(",")[0].strip('" '), e["weight"], e["size"])
        for ln in e["lines"]:
            d.text((ln["x"], ln["y"]), ln["text"], font=f, fill=e["_rgb"])
            if dm is not None:
                dm.text((ln["x"], ln["y"]), ln["text"], font=f, fill=0)


def glyph_polys(ch, units=100):
    """Outline and holes of a glyph in the 3D font, at size 100 (font units scaled), y up."""
    from fontTools.ttLib import TTFont
    from fontTools.pens.basePen import BasePen
    from shapely.geometry import Polygon
    f = TTFont(ttf("Inter Tight", 800))
    gs, cmap = f.getGlyphSet(), f.getBestCmap()
    k = units / f["head"].unitsPerEm

    class P(BasePen):
        def __init__(s):
            super().__init__(gs)
            s.cs, s.cur = [], []

        def _moveTo(s, p): s.cur = [p]
        def _lineTo(s, p): s.cur.append(p)
        def _curveToOne(s, a, b, c):
            p0 = s.cur[-1]
            for i in range(1, 9):
                t = i / 8
                s.cur.append(tuple((1 - t) ** 3 * q0 + 3 * (1 - t) ** 2 * t * q1 + 3 * (1 - t) * t * t * q2 + t ** 3 * q3 for q0, q1, q2, q3 in zip(p0, a, b, c)))
        def _qCurveToOne(s, a, b):
            p0 = s.cur[-1]
            for i in range(1, 9):
                t = i / 8
                s.cur.append(tuple((1 - t) ** 2 * q0 + 2 * (1 - t) * t * q1 + t * t * q2 for q0, q1, q2 in zip(p0, a, b)))
        def _closePath(s): s.cs.append(s.cur); s.cur = []
        _endPath = _closePath

    pen = P()
    gs[cmap[ord(ch)]].draw(pen)
    polys = [[(x * k, y * k) for x, y in c] for c in pen.cs if len(c) > 2]
    P_ = [Polygon(c) for c in polys]
    outers = [i for i, p in enumerate(P_) if sum(1 for q in P_ if q is not p and q.contains(p)) % 2 == 0]
    shapes = []
    for i in outers:
        holes = [polys[j] for j, q in enumerate(P_) if j not in outers and P_[i].contains(q)]
        shapes.append((polys[i], holes))
    return shapes, f["hmtx"][cmap[ord(ch)]][0] * k


def make_post(d, *, n_slides=2, defects=(), date="2026-11-12", hero="60%", extra_slide_text=None):
    """Write a synthetic post under d and return (pkg, post dir). defects: names from DEFECTS below."""
    os.makedirs(os.path.join(d, "passes"), exist_ok=True)
    D = set(defects)
    slides = []
    for si in range(1, n_slides + 1):
        bg = background("dark" if "dark_bg" in D else "aurora")
        img = Image.fromarray(bg.copy())
        els = []
        i = 0
        hl_color = (190, 195, 215) if "low_contrast" in D and si == 1 else (10, 18, 48)
        els.append(text_el(i, "Shops paid about $2,250", "headline", 144, 300, 150, "Inter Tight", 800, hl_color)); i += 1
        body = "About $47 a month for 4 years." if "split_number" not in D else "About $47 a month for 4 years, then $2.90 more."
        lines = None
        if "split_number" in D and si == 1:
            lines = ["About $47 a month for 4 years, then $2.", "90 more."]
        els.append(text_el(i, body, "body", 144, 1900, 96, "Inter", 500, lines=lines)); i += 1
        src_size = 24 if "tiny_text" in D and si == 1 else 64
        els.append(text_el(i, "Source: FTC complaint, Nov 2, 2026", "source", 144, 2200, src_size, "Inter", 500)); i += 1
        els.append(text_el(i, f"{si} / {n_slides}", "counter", 1860, 120, 48, "Inter Tight", 700)); i += 1
        els.append(text_el(i, "www.svpay.app", "footer", 1560, 2520, 52, "Inter Tight", 700)); i += 1
        if extra_slide_text:
            els.append(text_el(i, extra_slide_text, "body", 144, 2050, 80, "Inter", 500)); i += 1
        if "placeholder" in D and si == 1:
            els.append(text_el(i, "Headline here {{amount}}", "body", 144, 1700, 80, "Inter", 500)); i += 1
        # 3D hero (slide 1 only)
        id_img = cover = shadow = normal = None
        scene = None
        if si == 1:
            id_img, cover, shadow, normal, scene, hero_rgb = hero3d(hero, D)
            # composite the hero onto the background
            a = np.asarray(img).copy()
            obj = id_img[..., 2] > 0
            a[obj] = hero_rgb[obj]
            sh = (255 - shadow[..., 0]).astype(np.float32) / 255 * 0.2
            a = (a * (1 - sh[..., None])).astype(np.uint8)
            img = Image.fromarray(a)
        if "glass_over_glyph" in D and si == 1:
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(ov).rounded_rectangle((1100, 1050, 2000, 1500), radius=40, fill=(252, 252, 255, 250))
            img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
        bg_pass = img.copy()
        mask = Image.new("L", (W, H), 255)
        draw_text(img, els, mask)
        unclipped = mask.copy()
        if "clipped" in D and si == 1:
            # the headline's last letters are hidden by an overflow box: the final and the mask lose them
            a = np.asarray(img).copy()
            m = np.asarray(mask).copy()
            box = els[0]["box"]
            x0 = int(box["x"] + box["w"] - 60)
            a[int(box["y"]):int(box["y"] + box["h"]), x0:] = np.asarray(bg_pass)[int(box["y"]):int(box["y"] + box["h"]), x0:]
            m[int(box["y"]):int(box["y"] + box["h"]), x0:] = 255
            img, mask = Image.fromarray(a), Image.fromarray(m)
        stem = f"slide-{si}"
        f = os.path.join(d, stem + ".jpg")
        img.save(f, quality=92, subsampling=0)
        bg_pass.save(os.path.join(d, "passes", stem + ".bg.png"))
        mask.convert("1").save(os.path.join(d, "passes", stem + ".mask.png"))
        unclipped.convert("1").save(os.path.join(d, "passes", stem + ".unclipped.png"))
        man = {"size": [W, H], "slide": stem, "section": f"s{si}", "file": "synthetic", "failed_requests": [], "text": [{k: v for k, v in e.items() if k != "_rgb"} for e in els],
               "panels": ([{"x": 1100, "y": 1050, "w": 900, "h": 450, "radius": 80, "backdrop": "blur(24px)", "background": "rgba(255,255,255,0.98)", "border": "2px rgb(255,255,255)",
                            "box_shadow": "none", "cls": "glass"}] if "glass_over_glyph" in D and si == 1 else []),
               "images": [{"src": "svpay-logo.png", "cls": "logo", "complete": True, "natural": [400, 160], "x": 151, "y": 2454, "w": 342, "h": 136.8}]
               + ([{"src": "hero_1.png", "cls": "hero", "complete": True, "natural": [W, H], "x": 0, "y": 0, "w": W, "h": H}] if si == 1 else []),
               "svgs": [], "hero": "hero_1.png" if si == 1 else None,
               "fonts": {"status": "unloaded" if "fonts_not_loaded" in D else "loaded", "faces": [], "files": [
                   {"family": fam, "weight": str(wt), "style": "normal", "src": FONTS[(fam, wt)].replace("node_modules", "node_modules")} for fam, wt in FONTS]},
               "images_complete": True, "capture": {"scale": 2, "viewport": [1080, 1400], "width": W, "height": H, "chromium": "153.0.8010.12", "fonts_ready_before_capture": True}}
        json.dump(man, open(os.path.join(d, "passes", stem + ".manifest.json"), "w"))
        if si == 1:
            for name, arr in (("id", id_img), ("idj", id_img if "zfight" not in D else jitter(id_img)), ("cover", cover), ("shadow", shadow), ("normal", normal)):
                Image.fromarray(arr).save(os.path.join(d, "passes", f"hero_1.{name}.png"))
            json.dump(scene, open(os.path.join(d, "passes", "hero_1.scene.json"), "w"))
        slides.append({"file": f, "role": "hook" if si == 1 else ("cta" if si == n_slides else "proof"), "text": " ".join(e["text"] for e in els if e["role"] not in ("counter",)),
                       "has_stat": True, "source_line": "Source: FTC complaint, Nov 2, 2026"})
    media = [s["file"] for s in slides]
    pkg = {"date": date, "slug": "synthetic", "kind": "news", "format": "news",
           "networks": {"instagram": {"text": "Synthetic.", "media": media, "alt": [f"Slide {i + 1}: shops paid $2,250." for i in range(len(media))]},
                        "x": {"text": "Synthetic.", "media": media[:1], "alt": ["Shops paid $2,250."]}},
           "facts": [{"text": "lease total", "numbers": ["$2,250", "$47", "60%", "4 years", "$2.90"], "source_url": "https://www.ftc.gov/x", "source_date": "2026-11-02"}],
           "math": [], "slides": slides}
    return pkg, d


def jitter(idm):
    a = idm.copy()
    ys, xs = np.nonzero(a[..., 2] > 0)
    cy, cx = int(ys.mean()), int(xs.mean())
    a[cy - 20:cy + 20, cx - 20:cx + 20, 0] = 2            # a patch inside one glyph turns into another object: z-fighting
    a[cy - 20:cy + 20, cx - 20:cx + 20, 2] = 2
    return a


def hero3d(text, D):
    """A flat stand-in for the 3D hero: glyph faces over offset walls, with every 3D pass."""
    from shapely.geometry import Polygon
    from shapely import affinity
    idm = np.zeros((H, W, 4), np.uint8)
    idm[..., 3] = 255
    cover = np.zeros((H, W, 4), np.uint8)
    cover[..., 3] = 255
    normal = np.zeros((H, W, 4), np.uint8)
    normal[..., 3] = 255
    rgb = np.zeros((H, W, 3), np.uint8)
    shadow = np.full((H, W, 4), 255, np.uint8)
    chars = list(text) if "question_mark" not in D else list(text) + ["?"]
    scale = 7.0                  # font units (size 100) to px
    x = 300.0
    base_y = 1600.0
    parts, objects = [], []
    k = 0
    for oi, ch in enumerate(chars, 1):
        shapes, adv = glyph_polys(ch)
        role_txt = ch
        pts_part = []
        for outline, holes in shapes:
            k += 1
            poly = Polygon(outline, holes).buffer(0)
            P = affinity.translate(affinity.scale(poly, scale, -scale, origin=(0, 0)), x, base_y)
            wall = affinity.translate(P, 14, 10)
            for geom, g in ((wall, 2), (P, 1)):
                m = raster(geom)
                if g == 2:
                    m &= ~raster(P)
                idm[m, 0] = k
                idm[m, 1] = g
                idm[m, 2] = oi
                rgb[m] = (150, 150, 185) if g == 2 else (110, 70, 230)
                normal[m, :3] = (128, 128, 255) if g == 1 else (220, 128, 180)
            full = raster(P) | raster(wall)
            if "sliver" in D and ch == chars[0] and k == 1:
                # a wall poking through the front face: the self-intersection signature
                ys, xs = np.nonzero(raster(P))
                cy, cx = int(np.median(ys)), int(np.median(xs))
                idm[cy - 1:cy + 1, cx - 60:cx + 60, 1] = 2
                rgb[cy - 1:cy + 1, cx - 60:cx + 60] = (245, 245, 255)
            j = k - 1
            cover[full, j >> 3] |= (1 << (j & 7))
            b = P.union(wall).bounds
            parts.append({"part": k, "object": oi, "char": ch, "curve_types": ["LineCurve"], "outline": [[round(a, 3), round(c, 3)] for a, c in outline],
                          "holes": [[[round(a, 3), round(c, 3)] for a, c in h] for h in holes], "expanded": [[round(a, 3), round(c, 3)] for a, c in outline],
                          "expanded_holes": [[[round(a, 3), round(c, 3)] for a, c in h] for h in holes], "bevel_reach": 0.2,
                          "px_box": [int(b[0]), int(b[1]), int(b[2]), int(b[3])],
                          "world_box": [[(b[0] - 1080) / 100, (H - b[3]) / 100 - 10, -0.5], [(b[2] - 1080) / 100, (H - b[1]) / 100 - 10, 0.5]], "min_world_y": 0.3})
            pts_part.append(k)
        objects.append({"index": oi, "name": "", "geometry": "ExtrudeGeometry", "user": {"normals": "creased"}, "parts": pts_part, "parent": "group-1", "parent_is_scene": False,
                        "material": [{"type": "MeshPhysicalMaterial", "transmission": 0, "transparent": False, "opacity": 1, "side": 0, "tone_mapped": True,
                                      "map_anisotropy": 16, "map_min_filter": 1008, "normal_map": False, "has_tangents": False}],
                        "extrude": {"depth": 50, "bevelEnabled": True, "bevelThickness": 6, "bevelSize": 5, "bevelOffset": 0, "bevelSegments": 8, "curveSegments": 32, "steps": 1},
                        "text": role_txt, "role": "glyph", "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                        "world_box": parts[-1]["world_box"]})
        x += adv * scale + 30
    # roles: the number's characters are numerals
    num = "".join(o["text"] for o in objects if o["text"] in "0123456789$%,.")
    for o in objects:
        o["role"] = "numeral" if o["text"] in "0123456789$%,." and any(c.isdigit() for c in num) else "glyph" if o["text"] else "other"
    # soft floor shadow under the glyphs
    y, xg = np.mgrid[0:H, 0:W]
    sh = np.exp(-(((xg - 1000) / 700.0) ** 2 + ((y - 1660) / 50.0) ** 2)) * 0.6
    if "shadow_line" in D:
        line = (np.abs(y - (1250 - (xg * 0.02))) < 1.0) & (xg % 14 < 9)
        sh = np.maximum(sh, line * 0.08)
    v = (255 * (1 - sh)).astype(np.uint8)
    shadow[..., 0] = shadow[..., 1] = shadow[..., 2] = v
    scene = {"font_url": "node_modules/@fontsource/inter-tight/files/inter-tight-latin-800-normal.woff", "three_revision": "160",
             "gpu": "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)",
             "renderer": {"tone_mapping": "NeutralToneMapping", "exposure": 1, "output_color_space": "srgb", "color_management": True, "shadow_type": "PCFShadowMap",
                          "shadows": True, "pixel_ratio": 1, "drawing_buffer": [W, H], "antialias": True, "preserve_drawing_buffer": True, "max_samples": 4,
                          "max_anisotropy": 16, "context_lost": False},
             "camera": {"type": "PerspectiveCamera", "fov": 26, "near": 1, "far": 300, "aspect": 0.8, "position": [0, 6, 40], "rotation": [-0.149, 0, 0], "distance": 40.4, "log_depth": False},
             "environment": {"present": True, "intensity": 1}, "lights": [{"type": "DirectionalLight", "intensity": 2.2, "color": "ffffff", "position": [8, 22, 16], "cast_shadow": True,
                                                                           "target": [0, 0, 0], "shadow": {"map_size": [4096, 4096], "radius": 4, "blur_samples": 16, "bias": -0.0003, "normal_bias": 0.02,
                                                                                                           "camera": {"left": -90, "right": 90, "top": 90, "bottom": -90, "near": 1, "far": 300},
                                                                                                           "visible_floor": {"samples": 100, "outside_95pct": 0, "worst_fraction": 0.4}}}],
             "floors": [{"position": [0, 0, 0], "opacity": 0.2, "size": [200, 200]}], "reflections": 0, "objects": objects, "parts": parts,
             "loading": {"fonts": "loaded", "images_complete": True}, "part_pairs": [], "coplanar_pairs": [], "render_seconds": 12.0, "console": [], "hero": "hero_1.png"}
    return idm, cover, shadow, normal, scene, rgb


def raster(geom):
    im = Image.new("1", (W, H), 0)
    dr = ImageDraw.Draw(im)
    for g in getattr(geom, "geoms", [geom]):
        if g.is_empty:
            continue
        dr.polygon(list(g.exterior.coords), fill=1)
        for r in g.interiors:
            dr.polygon(list(r.coords), fill=0)
    return np.array(im, bool)
