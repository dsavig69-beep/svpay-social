"""The light process (founder, Nov 12, 2026): one reviewer; facts, legal claims and script gates block; taste scores."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = os.path.dirname(HERE)
sys.path.insert(0, CHECKS)
import score  # noqa: E402

ALL = list(score.CRITERIA)


def judged(fails=()):
    return [{"id": cid, "where": "post", "verdict": "fail" if cid in fails else "pass", "reason": "r", "quote": "q"}
            for cid in ALL]


ONE_PERSON = {"judges": 1, "not_ai_votes": 1, "original_votes": 1, "per_check": []}
ONE_AI = {"judges": 1, "not_ai_votes": 0, "original_votes": 1, "per_check": []}


class Light(unittest.TestCase):
    def test_clean_post_publishes(self):
        out = score.decide({"results": []}, judged(), ONE_PERSON, light=True)
        self.assertEqual(out["decision"], "PUBLISH", out["why"])

    def test_taste_gate_scores_but_never_blocks(self):
        out = score.decide({"results": []}, judged({"V-L07"}), ONE_PERSON, light=True)
        self.assertNotEqual(out["decision"], "REWRITE", out["why"])
        self.assertNotIn("V-L07", out["gates_failed"])
        self.assertEqual(score.decide({"results": []}, judged({"V-L07"}), ONE_PERSON)["decision"], "REWRITE")

    def test_ai_read_goes_to_the_founder(self):
        out = score.decide({"results": []}, judged(), ONE_AI, light=True)
        self.assertEqual(out["decision"], "HOLD")
        self.assertIn("SG-H05", out["why"])

    def test_facts_and_legal_still_block(self):
        fact_gates = [c for c in ("W-H05", "SC16", "W-G10", "CO11") if c in score.CRITERIA]
        self.assertTrue(fact_gates)
        for cid in fact_gates:
            out = score.decide({"results": []}, judged({cid}), ONE_PERSON, light=True)
            self.assertEqual(out["decision"], "REWRITE", cid)

    def test_script_gate_blocks(self):
        gate = next(c["id"] for c in score.CRITERIA.values() if c["gate"] and c["tier"] == "Script")
        script = {"results": [{"id": gate, "status": "fail", "where": "linkedin", "detail": "x"}]}
        self.assertEqual(score.decide(script, judged(), ONE_PERSON, light=True)["decision"], "REWRITE")

    def test_merge_takes_one_reviewer(self):
        d = tempfile.mkdtemp()
        f = os.path.join(d, "r.json")
        json.dump({"reader": "one", "reads_as_person": True, "original": True,
                   "checks": [{"id": "SG-H05", "verdict": "pass", "reason": ""}]}, open(f, "w"))
        out = os.path.join(d, "panel.json")
        subprocess.run([sys.executable, os.path.join(CHECKS, "merge.py"), "panel", f, "--out", out], check=True,
                       capture_output=True)
        p = json.load(open(out))
        self.assertEqual((p["judges"], p["not_ai_votes"], p["per_check"][0]["credit"]), (1, 1, 1.0))


if __name__ == "__main__":
    unittest.main()
