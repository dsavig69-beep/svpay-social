"""Image gates on synthetic slides: a clean post passes every image gate; one defect at a time fails its gate.

    python3 -m unittest discover -s checks/tests
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import check_post  # noqa: E402
import image  # noqa: E402
import synth  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="svpay-synth-")
_BUILT = {}


def build(defects=(), strip_passes=False, edit_scene=None):
    key = (tuple(sorted(defects)), strip_passes, json.dumps(edit_scene, sort_keys=True) if edit_scene else None)
    if key not in _BUILT:
        d = os.path.join(TMP, "p%d" % len(_BUILT))
        pkg, _ = synth.make_post(d, defects=defects)
        if strip_passes:
            shutil.rmtree(os.path.join(d, "passes"))
        if edit_scene:
            p = os.path.join(d, "passes", "hero_1.scene.json")
            sc = json.load(open(p))
            for path, val in edit_scene.items():
                cur = sc
                keys = path.split(".")
                for k in keys[:-1]:
                    cur = cur[int(k)] if k.isdigit() else cur[k]
                cur[int(keys[-1]) if keys[-1].isdigit() else keys[-1]] = val
            json.dump(sc, open(p, "w"))
        _BUILT[key] = pkg
    return _BUILT[key]


def statuses(pkg, ids):
    rep = check_post.Report()
    image.run(rep, pkg, base_dir=REPO, extra={}, only=set(ids))
    out = {}
    for r in rep.results:
        out.setdefault(r["id"], []).append((r["status"], r["where"], r["detail"]))
    return out


def worst(rows):
    order = {"fail": 0, "needs_passes": 1, "pass": 2, "na": 3}
    return min((r[0] for r in rows), key=lambda s: order[s]) if rows else None


DEFECT_GATES = [
    ("low_contrast", "V-D01"),
    ("tiny_text", "T-S05"),
    ("split_number", "T-N06"),
    ("clipped", "T-O05"),
    ("placeholder", "T-O09"),
    ("fonts_not_loaded", "R-P06"),
    ("shadow_line", "R-S02"),
    ("sliver", "T-D04"),
    ("sliver", "R-A08"),
    ("question_mark", "R-D11"),
    ("zfight", "R-Z03"),
    ("glass_over_glyph", "R-G02"),
]

CLEAN_GATES = sorted({g for _, g in DEFECT_GATES} | {"T-O01", "T-O03", "T-O11", "T-O10", "T-N08", "R-D01", "R-D03", "R-Z04", "R-Z06", "R-G01", "R-X01",
                                                     "QR06", "R-S01", "MC-S01", "SC25", "SC07", "F-IG20", "F-SW05", "F-TN15", "QF02", "QF03", "QF04",
                                                     "QF08", "QF30", "QM02"})


class CleanPost(unittest.TestCase):
    def test_clean_post_passes_the_image_gates(self):
        res = statuses(build(), CLEAN_GATES)
        bad = {k: [r for r in v if r[0] in ("fail", "needs_passes")] for k, v in res.items()}
        bad = {k: v for k, v in bad.items() if v}
        self.assertEqual(bad, {}, bad)


class Defects(unittest.TestCase):
    pass


def _mk(defect, gate):
    def t(self):
        res = statuses(build((defect,)), [gate])
        self.assertEqual(worst(res.get(gate, [])), "fail", res.get(gate))
    return t


for _d, _g in DEFECT_GATES:
    setattr(Defects, f"test_{_d}_fails_{_g.replace('-', '_')}", _mk(_d, _g))


class NeedsPasses(unittest.TestCase):
    def test_gate_without_passes_is_needs_passes_or_estimate_fail(self):
        res = statuses(build(strip_passes=True), ["T-S05", "T-O05", "T-D04", "R-A08", "R-S02"])
        for gid in ("T-D04", "R-A08"):
            self.assertEqual(worst(res[gid]), "needs_passes", (gid, res[gid]))
        for gid in ("T-O05",):
            self.assertIn(worst(res[gid]), ("needs_passes", "fail"), (gid, res[gid]))

    def test_an_estimate_never_clears_a_gate(self):
        res = statuses(build(strip_passes=True), ["T-O09"])
        self.assertTrue(all(r[0] in ("needs_passes", "fail", "na") for r in res["T-O09"]), res["T-O09"])

    def test_passes_from_another_render_count_as_missing(self):
        """A re-render that does not reproduce the slide (Nov 4: 3D knobs never recorded) must not be measured."""
        d = os.path.join(TMP, "mismatch")
        pkg, _ = synth.make_post(d)
        from PIL import Image
        bg = os.path.join(d, "passes", "slide-1.bg.png")
        Image.open(bg).transform((synth.W, synth.H), Image.AFFINE, (1, 0, 120, 0, 1, 0)).save(bg)
        res = statuses(pkg, ["T-D04", "R-A08"])
        for gid in ("T-D04", "R-A08"):
            self.assertEqual(worst([r for r in res[gid] if r[1] == "slide 1"]), "needs_passes", (gid, res[gid]))

    def test_non_gate_without_passes_is_not_scored(self):
        res = statuses(build(strip_passes=True), ["T-K03", "T-L02"])
        self.assertTrue(all(r[0] == "na" for v in res.values() for r in v), res)


SCENE_EDITS = [
    ({"renderer.tone_mapping": "ACESFilmicToneMapping"}, "R-X03"),
    ({"renderer.shadow_type": "VSMShadowMap"}, "R-S09"),
    ({"renderer.shadow_type": "BasicShadowMap"}, "R-S08"),
    ({"lights.0.shadow.radius": 48}, "R-T02"),
    ({"lights.0.shadow.visible_floor.outside_95pct": 40}, "R-S01"),
    ({"camera.near": 0.1}, "R-Z01"),
    ({"renderer.drawing_buffer": [1080, 1350]}, "V-I23"),
    ({"renderer.antialias": False}, "R-A07"),
    ({"renderer.color_management": False}, "R-X07"),
    ({"environment.present": False}, "R-T06"),
    ({"renderer.context_lost": True}, "QR06"),
    ({"coplanar_pairs": [{"a": 1, "b": 2, "shared_planes": 3}]}, "R-Z02"),
    ({"part_pairs": [{"a": 1, "b": 2, "same_object": False, "chars": ["6", "0"], "min_dist": 0.0, "glyph_h": 2.5, "inside": 7}]}, "R-Z06"),
    ({"objects.0.material.0.side": 2}, "R-N07"),
    ({"objects.0.material.0.transmission": 0.5, "objects.0.material.0.transparent": True}, "R-G03"),
    ({"objects.0.extrude.bevelSegments": 2}, "R-D06"),
    ({"parts.0.min_world_y": -0.5}, "R-Z07"),
    ({"render_seconds": 45}, "QR22"),
]


class SceneAsserts(unittest.TestCase):
    pass


def _mk_scene(edit, cid):
    def t(self):
        clean = worst(statuses(build(), [cid]).get(cid, []))
        bad = worst(statuses(build(edit_scene=edit), [cid]).get(cid, []))
        self.assertNotEqual(clean, "fail", f"{cid} fails the clean scene")
        self.assertEqual(bad, "fail", f"{cid} passes {edit}")
    return t


for _e, _c in SCENE_EDITS:
    setattr(SceneAsserts, f"test_{_c.replace('-', '_')}_" + "_".join(k.split(".")[-1] for k in _e), _mk_scene(_e, _c))


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
