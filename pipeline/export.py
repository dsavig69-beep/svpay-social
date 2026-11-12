#!/usr/bin/env python3
"""Export a post's network files from its 2160x2700 masters (ruling 3, Nov 11 research round).

    python3 pipeline/export.py posts/<folder> [--png out] [--template slides.html] [--gen-ai] [--update-post]

For each slide-N it writes, next to post.json:
  slide-N.jpg       the 2160x2700 master, JPEG quality 92, 4:4:4, no metadata
  slide-N-1440.jpg  1440x1800, JPEG quality 92, 4:4:4, area downscale made once from the PNG master
and routes them: Instagram, Facebook and Threads get the 1440 file; LinkedIn and X get the master when it is under
5 MB, else the 1440 file. GBP keeps its own variant (slide-1-gbp.jpg) when there is one.

--png out   reads the PNG masters out1.png .. outN.png (render.js output) so each JPEG is the only lossy step. Without
            it the existing slide-N.jpg is the master (then the 1440 file is a second lossy encode; QF10 says so).
--update-post rewrites post.json's media lists to the routed files.

It also writes files.json (one row per network file: network, file, w, h, bytes, format, subsampling, ICC, sha256;
QF27) and provenance.json (template, git commit, Chromium, Playwright, three.js, font hashes, input hashes, encode steps,
gen_ai_used; QM09). Both stay next to the slides.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MASTER = (2160, 2700)
SMALL = (1440, 1800)
LI_X_LIMIT = 5_000_000
META = ("instagram", "facebook", "threads")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def save_jpeg(im, path):
    im.convert("RGB").save(path, "JPEG", quality=92, subsampling=0, optimize=True)   # no exif, no xmp, no icc bytes


def area_downscale(im, size):
    import cv2
    a = cv2.resize(np.asarray(im.convert("RGB")), size, interpolation=cv2.INTER_AREA)
    return Image.fromarray(a)


def version(pkg):
    for base in (os.path.join(REPO, "node_modules"), "/opt/npm-tools/node_modules"):
        p = os.path.join(base, pkg, "package.json")
        if os.path.exists(p):
            return json.load(open(p))["version"]
    return None


def chromium(folder):
    for p in sorted(glob.glob(os.path.join(folder, "passes", "*.manifest.json"))):
        return json.load(open(p)).get("capture", {}).get("chromium")
    lock = os.path.join(REPO, "checks", "image", "render.lock.json")
    return json.load(open(lock)).get("chromium") if os.path.exists(lock) else None


def row(folder, net, f):
    p = os.path.join(folder, f)
    with Image.open(p) as im:
        from PIL import JpegImagePlugin
        sub = JpegImagePlugin.get_sampling(im) if im.format == "JPEG" else None
        return {"network": net, "file": f, "w": im.size[0], "h": im.size[1], "bytes": os.path.getsize(p), "format": im.format,
                "subsampling": {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}.get(sub), "icc": "sRGB (untagged)" if not im.info.get("icc_profile") else "embedded",
                "sha256": sha(p)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--png", help="prefix of the PNG masters from render.js (out -> out1.png ..)")
    ap.add_argument("--template", default="slides.html")
    ap.add_argument("--gen-ai", action="store_true", help="a generative-AI asset is in the post (V-I20 then needs the caption disclosure)")
    ap.add_argument("--update-post", action="store_true")
    a = ap.parse_args()
    folder = a.folder.rstrip("/")
    post_p = os.path.join(folder, "post.json")
    post = json.load(open(post_p, encoding="utf-8")) if os.path.exists(post_p) else None
    n = len(post["slides"]) if post else len(glob.glob(os.path.join(folder, "slide-[0-9].jpg")) + glob.glob(os.path.join(folder, "slide-[0-9][0-9].jpg")))
    files, inputs = [], []
    for i in range(1, n + 1):
        master = os.path.join(folder, f"slide-{i}.jpg")
        chain = []
        if a.png:
            src = os.path.join(os.path.dirname(a.png) or ".", f"{os.path.basename(a.png)}{i}.png")
            im = Image.open(src)
            inputs.append({"file": os.path.relpath(src, REPO), "sha256": sha(src), "icc": "embedded" if im.info.get("icc_profile") else "sRGB (untagged)"})
            if im.size != MASTER:
                raise SystemExit(f"{src} is {im.size}, not {MASTER}")
            save_jpeg(im, master)
            chain = ["png master", "jpeg q92 4:4:4"]
        else:
            im = Image.open(master)
            if im.size != MASTER:
                raise SystemExit(f"{master} is {im.size}, not {MASTER}")
            chain = ["jpeg master"]
        small = os.path.join(folder, f"slide-{i}-1440.jpg")
        save_jpeg(area_downscale(im, SMALL), small)
        files.append({"file": os.path.basename(master), "encode_chain": chain})
        files.append({"file": os.path.basename(small), "encode_chain": chain[:1] + ["area downscale 1440x1800", "jpeg q92 4:4:4"] if a.png else ["jpeg master", "area downscale 1440x1800", "jpeg q92 4:4:4"]})
    # single-image variants a network uses (slide-1-single.jpg): their own master and 1440 file
    variants = sorted({os.path.basename(m) for v in ((post or {}).get("networks", {}) or {}).values() for m in v.get("media", [])
                       if re.fullmatch(r"slide-\d+-(?!gbp|1440)[a-z0-9-]+\.jpg", os.path.basename(m))})
    for v in variants:
        im = Image.open(os.path.join(folder, v))
        if im.size != MASTER:
            continue
        small = v.replace(".jpg", "-1440.jpg")
        save_jpeg(area_downscale(im, SMALL), os.path.join(folder, small))
        files.append({"file": v, "encode_chain": ["jpeg master"]})
        files.append({"file": small, "encode_chain": ["jpeg master", "area downscale 1440x1800", "jpeg q92 4:4:4"]})
    # routing
    route = {}
    for net in ("instagram", "facebook", "threads", "linkedin", "x"):
        if post and net not in post.get("networks", {}):
            continue
        cur = [os.path.basename(m).replace("-1440.jpg", ".jpg") for m in ((post or {}).get("networks", {}).get(net, {}).get("media") or [])]
        cur = [c for c in cur if os.path.exists(os.path.join(folder, c))] or [f"slide-{i}.jpg" for i in range(1, n + 1)]
        out = []
        for master in cur:
            small = master.replace(".jpg", "-1440.jpg")
            if not os.path.exists(os.path.join(folder, small)):
                out.append(master)
            elif net in META:
                out.append(small)
            else:
                out.append(master if os.path.getsize(os.path.join(folder, master)) < LI_X_LIMIT else small)
        route[net] = out
    gbp = os.path.join(folder, "slide-1-gbp.jpg")
    if os.path.exists(gbp) and (not post or "gbp" in post.get("networks", {})):
        route["gbp"] = ["slide-1-gbp.jpg"]
    rows = [row(folder, net, f) for net, fs in route.items() for f in fs]
    json.dump({"_why": "Per-network files of this post (pipeline/export.py; QF27)", "files": rows}, open(os.path.join(folder, "files.json"), "w"), indent=1)
    try:
        commit = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        commit = None
    fonts_lock = os.path.join(REPO, "checks", "image", "fonts.lock.json")
    fonts = {k: v for k, v in json.load(open(fonts_lock)).items() if not k.startswith("_")} if os.path.exists(fonts_lock) else {}
    side = {"_why": "Provenance of this post's files (pipeline/export.py; QM09). Private facts never go here.", "template": a.template, "git_commit": commit,
            "chromium": chromium(folder), "playwright": version("playwright"), "three": version("three"), "fonts": fonts, "inputs": inputs,
            "files": files, "gen_ai_used": bool(a.gen_ai)}
    json.dump(side, open(os.path.join(folder, "provenance.json"), "w"), indent=1)
    if a.update_post and post:
        rel = os.path.relpath(folder, REPO)
        for net, fs in route.items():
            post["networks"][net]["media"] = [f"{rel}/{f}" for f in fs]
        json.dump(post, open(post_p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    for net, fs in route.items():
        print(f"{net:9} {', '.join(fs)}")
    print(f"wrote files.json ({len(rows)} rows) and provenance.json in {folder}")


if __name__ == "__main__":
    main()
