"""Coverage of the script tier: every Script-tier image criterion has a check, every check runs on a synthetic clean
post and on a synthetic post with every defect switched on without a crash, and each outcome is pinned in
expected_synth.json so a change in any check's behavior shows up.

    python3 -m unittest checks.tests.test_coverage
    SVPAY_UPDATE_GOLDEN=1 python3 -m unittest checks.tests.test_coverage    # re-pin after an intended change
"""
import json
import os
import re
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
from image import stubs  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
CHECKS = os.path.dirname(HERE)
GOLDEN = os.path.join(HERE, "expected_synth.json")
IMAGE_ID = re.compile(r"^(V-|F-|T-|R-|MC-|Q[A-Z]\d)")
ALL_DEFECTS = ("low_contrast", "tiny_text", "split_number", "clipped", "placeholder", "fonts_not_loaded", "shadow_line", "sliver", "question_mark",
               "zfight", "glass_over_glyph")


def criteria():
    return {c["id"]: c for c in json.load(open(os.path.join(CHECKS, "criteria.json"), encoding="utf-8"))}


class Coverage(unittest.TestCase):
    def test_every_script_image_criterion_has_a_check(self):
        image._load()
        src = open(os.path.join(CHECKS, "check_post.py"), encoding="utf-8").read()
        missing = [cid for cid, c in criteria().items()
                   if IMAGE_ID.match(cid) and c["tier"] == "Script" and cid not in image.REGISTRY and f'"{cid}"' not in src]
        self.assertEqual(missing, [])

    def test_every_check_is_a_criterion(self):
        image._load()
        crit = criteria()
        self.assertEqual([c for c in image.REGISTRY if c not in crit], [])

    def test_stubs_never_fail(self):
        image._load()
        for cid, why in stubs.STUBS.items():
            for fn, scope in image.REGISTRY[cid]:
                out = fn(None) if scope == "post" else fn(None, None)
                self.assertIsNone(out.ok, cid)
                self.assertTrue(why and out.detail, cid)


TMP = None
RESULTS = {}


def setUpModule():
    global TMP
    TMP = tempfile.mkdtemp(prefix="svpay-cover-")
    image._load()
    for name, defects in (("clean", ()), ("defects", ALL_DEFECTS)):
        pkg, _ = synth.make_post(os.path.join(TMP, name), defects=defects)
        rep = check_post.Report()
        image.run(rep, pkg, base_dir=REPO, extra={"report_dir": os.path.join(TMP, name, "report")})
        out = {}
        for r in rep.results:
            out.setdefault(r["id"], {})[r["where"]] = r["status"]
        RESULTS[name] = {"out": out, "errors": [(r["id"], r["where"], r["detail"]) for r in rep.results if "check error" in str(r["detail"])]}


def tearDownModule():
    if TMP:
        shutil.rmtree(TMP, ignore_errors=True)


class Synthetic(unittest.TestCase):
    def test_no_check_crashes(self):
        for name in RESULTS:
            self.assertEqual(RESULTS[name]["errors"], [], name)

    def test_every_check_reports(self):
        image._load()
        for name in RESULTS:
            silent = [cid for cid in image.REGISTRY if cid not in RESULTS[name]["out"]]
            self.assertEqual(silent, [], name)

    def test_outcomes_match_the_pinned_ones(self):
        now = {name: RESULTS[name]["out"] for name in RESULTS}
        if os.environ.get("SVPAY_UPDATE_GOLDEN") or not os.path.exists(GOLDEN):
            json.dump(now, open(GOLDEN, "w", encoding="utf-8"), indent=0, sort_keys=True)
        want = json.load(open(GOLDEN, encoding="utf-8"))
        diff = [(name, cid, want[name].get(cid), now[name].get(cid)) for name in want for cid in sorted(set(want[name]) | set(now[name]))
                if want[name].get(cid) != now[name].get(cid)]
        self.assertEqual(diff, [])

    def test_defects_fail_more_gates_than_clean(self):
        crit = criteria()
        def gates_failed(name):
            return {cid for cid, w in RESULTS[name]["out"].items() if crit[cid].get("gate") and any(s in ("fail", "needs_passes") for s in w.values())}
        self.assertEqual(gates_failed("clean"), set())
        self.assertGreaterEqual(len(gates_failed("defects")), 10)


if __name__ == "__main__":
    unittest.main()
