"""The image checks of the script tier (IMAGE-CHECKS-2, Nov 11, 2026 research round).

check_post.py calls run(rep, pkg, ...). Each check is a function registered with @check(id, scope=...):
  scope "slide"  runs once per slide in pkg["slides"] (and is told the slide)
  scope "image"  runs once per image file any network sends (slides plus single-image variants)
  scope "post"   runs once per post
A check returns an Outcome (core.ok / core.na / core.estimate / Outcome(core.NEEDS, ...)). The runner applies ruling 6:
an estimate made without the render passes may fail a check but can never clear a gate; a gate that only has an
estimate is reported "needs_passes" and counts as failing until the passes exist (pipeline --passes).
"""
from __future__ import annotations

import json
import os
import traceback

from . import core
from .core import NEEDS, Outcome

REGISTRY: dict[str, list] = {}
CRITERIA = {c["id"]: c for c in json.load(open(os.path.join(os.path.dirname(core.HERE), "criteria.json"), encoding="utf-8"))}


def check(cid, scope="slide"):
    def deco(fn):
        REGISTRY.setdefault(cid, []).append((fn, scope))
        return fn
    return deco


def _load():
    from . import files, words, textpx, threed, layout, brand, charts, delivery, vseries, extras, stubs  # noqa: F401  (registers checks)


def run(rep, pkg, base_dir=None, history=None, extra=None, only=None):
    """Run every registered image check on the package and add the results to rep (check_post.Report)."""
    _load()
    post = core.Post(pkg, base_dir=base_dir, history=history, extra=extra)
    items = []
    for cid, fns in sorted(REGISTRY.items()):
        if only and cid not in only:
            continue
        crit = CRITERIA.get(cid)
        if crit is None:
            continue
        for fn, scope in fns:
            items.append((cid, bool(crit.get("gate")), fn, scope))
    # one image at a time, then its caches are dropped: full-size passes for a whole deck do not fit next to the
    # other builders on an 8 GB machine
    deck = {id(s) for s in post.slides}
    for sl in post.all_images():
        for cid, gate, fn, scope in items:
            if scope == "image" or (scope == "slide" and id(sl) in deck):
                _one(rep, cid, gate, fn, post, sl, sl.where)
        sl.release()
    for cid, gate, fn, scope in items:
        if scope == "post":
            _one(rep, cid, gate, fn, post, None, "post")
            post.release()
    return post


def _one(rep, cid, gate, fn, post, sl, where):
    try:
        out = fn(post) if sl is None else fn(post, sl)
    except Exception as e:  # a crashing check is reported, never silently passed
        out = Outcome(False if gate else None, f"check error: {type(e).__name__}: {e}"[:200])
        if os.environ.get("SVPAY_CHECK_DEBUG"):
            traceback.print_exc()
    if out is None:
        return
    for o in (out if isinstance(out, list) else [out]):
        status, detail = o.ok, o.detail
        if status == NEEDS and not gate:
            status = None                     # a non-gate without its passes is not scored
        elif o.estimated and gate and status is True:
            status, detail = NEEDS, "needs passes: " + detail
        rep.add(cid, status, detail, o.where or where, o.value)


def registered_ids():
    _load()
    return sorted(REGISTRY)
