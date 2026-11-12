#!/usr/bin/env python3
"""Shrink the render passes for the repo: text masks to 1-bit PNG, the text-free background to a JPEG (quality 90,
4:4:4). The checker reads either form. Usage: python3 pipeline/pack_passes.py posts/<folder>/passes"""
import os
import sys

from PIL import Image

d = sys.argv[1]
for f in sorted(os.listdir(d)):
    p = os.path.join(d, f)
    if f.endswith((".mask.png", ".unclipped.png")):
        with Image.open(p) as im:
            if im.mode != "1":
                im.convert("L").point(lambda v: 255 if v >= 128 else 0).convert("1").save(p, optimize=True)
    elif f.endswith(".bg.png"):
        with Image.open(p) as im:
            im.convert("RGB").save(p[:-4] + ".jpg", quality=90, subsampling=0, optimize=True)
        os.remove(p)
print("packed", d)
