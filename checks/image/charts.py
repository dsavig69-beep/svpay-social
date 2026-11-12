"""Data visuals (MC-A, MC-B, MC-C, MC-G, MC-J07, F-IG26, F-X07). A chart is declared by the job in post.json:

  "charts": [{"slide": 4, "type": "column", "title": "...", "units": "$",
              "marks": [{"value": 2256, "px": 640, "thickness": 230, "label": "$2,256", "label_px": 48, "label_gap": 12,
                         "label_inside": false, "label_contrast": 7.1}],
              "series": [{"name": "...", "points": 2}], "colors": [[255,47,143], [35,80,255]],
              "axis": {"zero": true, "log": false, "dual": false, "break": false, "max": 2500, "y_max": null},
              "plot_box": {"x": 1180, "y": 730, "w": 840, "h": 1360}, "category_labels": ["paid to lease", "to buy new"],
              "tick_labels": [], "legend": null, "projected": [], "events": []}]

Checks with no declared chart are not scored (the 3D-free flat chart is drawn in HTML, so the job knows these values).
"""
from __future__ import annotations

import re

import numpy as np

from . import check
from .core import de00, lstar, na, ok, rel_lum, to_lab, wcag

ALLOWED = {"big_number", "horizontal_bar", "column", "dot", "lollipop", "line", "waterfall", "small_table", "stacked_bar_2", "receipt", "pie_2", "donut_2"}


def charts(post):
    return post.pkg.get("charts", [])


def each(post, fn):
    cs = charts(post)
    if not cs:
        return na("no chart declared in post.json")
    rows = []
    for c in cs:
        r = fn(c)
        if r is not None:
            r.where = f"slide {c.get('slide', '?')} chart"
            rows.append(r)
    return rows or na("does not apply to these charts")


@check("MC-A01", scope="post")
def allow_list(post):
    return each(post, lambda c: ok(c.get("type") in ALLOWED, c.get("type")))


@check("MC-A02", scope="post")
def one_chart(post):
    cs = charts(post)
    if not cs:
        return na("no chart declared in post.json")
    from collections import Counter
    n = Counter(c.get("slide") for c in cs)
    bad = [s for s, k in n.items() if k > 1]
    return ok(not bad, f"slides with more than one chart: {bad}")


@check("MC-A03", scope="post")
def line_limits(post):
    return each(post, lambda c: ok(len(c.get("series", [])) <= 3 and all(2 <= s.get("points", 2) <= 24 for s in c.get("series", [])), "") if c.get("type") == "line" else None)


@check("MC-A04", scope="post")
def pie_two(post):
    return each(post, lambda c: ok(len(c.get("marks", [])) == 2 and all(m.get("label") for m in c["marks"]), "") if c.get("type", "").startswith(("pie", "donut")) else None)


@check("MC-A05", scope="post")
def small_tables(post):
    return each(post, lambda c: ok(c.get("rows", 0) <= 4 and c.get("cols", 0) <= 3 and c.get("decimal_aligned", True), f"{c.get('rows')}x{c.get('cols')}") if c.get("type") == "small_table" else None)


@check("MC-A06", scope="post")
def plot_area(post):
    def f(c):
        b = c.get("plot_box")
        if not b:
            return ok(False, "no plot_box declared")
        share = b["w"] * b["h"] / (2160 * 2700)
        lim = 0.45 if c.get("linkedin_page") else 0.40
        return ok(share >= lim, f"plot area {share:.0%}")
    return each(post, f)


@check("MC-A07", scope="post")
def mark_sizes(post):
    def f(c):
        t = c.get("type")
        th = [m.get("thickness", 0) for m in c.get("marks", [])]
        if t in ("column", "horizontal_bar", "waterfall", "stacked_bar_2"):
            return ok(min(th or [0]) >= 80, f"thinnest bar {min(th or [0])}px")
        if t == "line":
            return ok(c.get("stroke", 0) >= 10, f"stroke {c.get('stroke')}px")
        if t in ("dot", "lollipop"):
            return ok(min(th or [0]) >= 28, f"smallest dot {min(th or [0])}px")
        return None
    return each(post, f)


@check("MC-A08", scope="post")
def category_labels(post):
    def f(c):
        labs = c.get("category_labels", [])
        if not labs:
            return None
        if c.get("rotated_labels"):
            return ok(False, "rotated labels")
        long_ = any(len(l) > 10 for l in labs)
        return ok(all(len(l) <= 20 for l in labs) and not (long_ and c.get("type") == "column"), f"longest label {max(len(l) for l in labs)} chars")
    return each(post, f)


@check("MC-A09", scope="post")
def labels_at_390(post):
    def f(c):
        if c.get("labels_read_at_390") is None:
            return na("labels not OCR-checked at 390px (charts.labels_read_at_390)")
        return ok(c["labels_read_at_390"], "")
    return each(post, f)


def lie_factor(c):
    ms = [m for m in c.get("marks", []) if m.get("value") and m.get("px")]
    out = []
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            a, b = ms[i], ms[j]
            out.append((a["px"] / b["px"]) / (a["value"] / b["value"]))
    return out


@check("MC-B01", scope="post")
def proportional(post):
    def f(c):
        if c.get("type") not in ("column", "horizontal_bar", "waterfall", "stacked_bar_2"):
            return None
        lf = lie_factor(c)
        if not lf:
            return ok(False, "marks lack value or px")
        bad = [round(x, 3) for x in lf if not 0.95 <= x <= 1.05]
        return ok(not bad, f"lie factors {bad}" if bad else "proportional")
    return each(post, f)


@check("MC-B02", scope="post")
def no_breaks(post):
    return each(post, lambda c: ok(not c.get("axis", {}).get("break"), "axis break") if c.get("type") in ("column", "horizontal_bar") else None)


@check("MC-B03", scope="post")
def line_baseline(post):
    def f(c):
        if c.get("type") != "line" or c.get("axis", {}).get("zero", True):
            return None
        return ok(c["axis"].get("lowest_gridline_labeled") and not c["axis"].get("heavy_baseline"), "")
    return each(post, f)


@check("MC-B04", scope="post")
def same_scale(post):
    cs = [c for c in charts(post) if c.get("measure") and c.get("axis", {}).get("px_per_unit")]
    if len(cs) < 2:
        return na("no measure shown on two slides")
    by = {}
    for c in cs:
        by.setdefault(c["measure"], []).append(c["axis"]["px_per_unit"])
    bad = [k for k, v in by.items() if len(v) > 1 and (max(v) - min(v)) / max(v) > 0.01]
    return ok(not bad, ", ".join(bad))


@check("MC-B05", scope="post")
def area_marks(post):
    def f(c):
        if c.get("encoding") != "area":
            return None
        ms = [m for m in c.get("marks", []) if m.get("value") and m.get("area_px")]
        bad = [(a["area_px"] / b["area_px"]) / (a["value"] / b["value"]) for a in ms for b in ms if a is not b]
        return ok(all(0.95 <= x <= 1.05 for x in bad), "")
    return each(post, f)


@check("MC-B06", scope="post")
def no_dual(post):
    return each(post, lambda c: ok(not c.get("axis", {}).get("dual"), "dual y-axes" if c.get("axis", {}).get("dual") else ""))


@check("MC-B07", scope="post")
def no_log(post):
    return each(post, lambda c: ok(not c.get("axis", {}).get("log"), ""))


@check("MC-B08", scope="post")
def time_axis(post):
    return each(post, lambda c: ok(c.get("time_spacing_error_px", 0) <= 2 and c.get("gaps_drawn", True), "") if c.get("time_axis") else None)


@check("MC-B09", scope="post")
def range_fill(post):
    def f(c):
        ms = [m for m in c.get("marks", []) if m.get("px")]
        if c.get("type") in ("column", "horizontal_bar") and ms and c.get("plot_box"):
            L = c["plot_box"]["h"] if c["type"] == "column" else c["plot_box"]["w"]
            return ok(max(m["px"] for m in ms) >= 0.6 * L, f"longest bar {max(m['px'] for m in ms) / L:.0%} of the plot")
        if c.get("type") == "line" and c.get("axis", {}).get("y_max") and c.get("data_max"):
            return ok(c["axis"]["y_max"] <= 1.25 * c["data_max"], "")
        return None
    return each(post, f)


@check("MC-B10", scope="post")
def tick_format(post):
    def f(c):
        ticks = c.get("tick_labels", [])
        if not ticks:
            return None
        has_dollar = any("$" in m.get("label", "") for m in c.get("marks", []))
        return ok(all(("$" in t) == has_dollar for t in ticks), "")
    return each(post, f)


@check("MC-B11", scope="post")
def projected(post):
    def f(c):
        pr = c.get("projected", [])
        if not pr:
            return None
        return ok(all(c["marks"][i].get("dashed") and "Projected" in c["marks"][i].get("label", "") for i in pr), "")
    return each(post, f)


@check("MC-C01", scope="post")
def value_labels_match(post):
    def f(c):
        sl = next((s for s in post.slides if s.idx == c.get("slide")), None)
        if not sl:
            return None
        labels = {m.get("label") for m in c.get("marks", [])}
        from .words import numbers
        quoted = [t for t, a, b in numbers(sl.text) if any(norm_eq(t, m.get("value")) for m in c.get("marks", []))]
        bad = [q for q in quoted if q not in labels and not any(q in (l or "") for l in labels)]
        return ok(not bad, ", ".join(bad))
    return each(post, f)


def norm_eq(tok, v):
    from .words import norm_value
    x, _ = norm_value(tok)
    return x is not None and v is not None and abs(x - v) < 1e-6


@check("MC-C03", scope="post")
def units(post):
    return each(post, lambda c: ok(bool(c.get("units")) or all(re.search(r"[$%]|\b(months?|years?)\b", m.get("label", "")) for m in c.get("marks", [])), ""))


@check("MC-C04", scope="post")
def event_annotations(post):
    def f(c):
        ev = c.get("events", [])
        if not ev:
            return None
        return ok(all(abs(e["x_px"] - e["expected_px"]) <= 2 for e in ev), "")
    return each(post, f)


@check("MC-C05", scope="post")
def no_logo_labels(post):
    return each(post, lambda c: ok(not c.get("logo_labels"), ""))


@check("MC-C06", scope="post")
def label_distance(post):
    def f(c):
        ms = c.get("marks", [])
        if not ms:
            return None
        far = [m.get("label") for m in ms if m.get("label_gap", 0) > 24]
        low = [m.get("label") for m in ms if m.get("label_inside") and m.get("label_contrast", 99) < 4.5]
        return ok(not far and not low, f"far: {far} low contrast: {low}")
    return each(post, f)


@check("MC-C07", scope="post")
def legend(post):
    return each(post, lambda c: ok(c["legend"].get("above") and c["legend"].get("order_matches"), "") if c.get("legend") else None)


@check("F-IG26", scope="post")
def value_label_size(post):
    return each(post, lambda c: ok(min(m.get("label_px", 0) for m in c.get("marks", [{}])) >= 94, f"smallest value label {min(m.get('label_px', 0) for m in c.get('marks', [{}]))}px") if c.get("marks") else None)


@check("F-X07", scope="post")
def x_chart_slides(post):
    xs = {s.idx for s in post.media("x")}
    cs = [c for c in charts(post) if c.get("slide") in xs]
    if len(post.media("x")) < 2 or not cs:
        return na("no chart in a multi-image X post")
    return ok(all(len(c.get("marks", [])) <= 3 and min(m.get("label_px", 0) for m in c["marks"]) >= 150 for c in cs), "")


@check("MC-G01", scope="post")
def data_palette(post):
    pal = [[0x23, 0x50, 0xFF], [0xFF, 0x2F, 0x8F], [0x0A, 0x12, 0x30], [0x9A, 0xA3, 0xB8], [0xE6, 0x9F, 0x00], [0x00, 0x9E, 0x73], [0x56, 0xB4, 0xE9], [0xCC, 0x79, 0xA7], [0xD5, 0x5E, 0x00], [0x00, 0x72, 0xB2]]
    pl = to_lab(np.array(pal, float)[None])[0]
    def f(c):
        cols = c.get("colors", [])
        if not cols:
            return None
        cl = to_lab(np.array(cols, float)[None])[0]
        bad = [i for i, x in enumerate(cl) if min(float(de00(x[None, None], p[None, None])) for p in pl) > 5]
        return ok(not bad, f"off-palette series {bad}")
    return each(post, f)


@check("MC-G04", scope="post")
def weak_colors(post):
    def f(c):
        cols = c.get("colors", [])
        if not cols:
            return None
        bg = c.get("background", [246, 247, 252])
        bad = [x for x in cols if wcag(float(rel_lum(np.array(x, float))), float(rel_lum(np.array(bg, float)))) < 3 and c.get("type") in ("line", "dot", "lollipop")]
        return ok(not bad, f"{len(bad)} thin-mark colors under 3:1")
    return each(post, f)


@check("MC-G05", scope="post")
def scales_monotonic(post):
    def f(c):
        if c.get("scale") not in ("sequential", "diverging"):
            return None
        L = lstar(np.array(c["colors"], float))
        if c["scale"] == "sequential":
            return ok(bool(np.all(np.diff(L) > 0) or np.all(np.diff(L) < 0)), "")
        return ok(c.get("center_meaningful", False), "")
    return each(post, f)


@check("MC-G07", scope="post")
def entity_colors(post):
    seen = {}
    bad = []
    for c in charts(post):
        for s, col in zip(c.get("series", []), c.get("colors", [])):
            n = s.get("name")
            if n in seen and seen[n] != col:
                bad.append(n)
            seen.setdefault(n, col)
    return ok(not bad, ", ".join(bad)) if seen else na("no named series")


@check("MC-J07", scope="post")
def equal_treatment(post):
    def f(c):
        ms = c.get("marks", [])
        if len(ms) < 2:
            return None
        th = {m.get("thickness") for m in ms}
        ls = {m.get("label_px") for m in ms}
        return ok(len(th) <= 1 and len(ls) <= 1, f"thickness {sorted(th, key=str)}, label sizes {sorted(ls, key=str)}")
    return each(post, f)
