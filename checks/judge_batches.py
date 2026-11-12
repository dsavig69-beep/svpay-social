#!/usr/bin/env python3
"""Print the judge-tier and panel-tier checks in batches of 15, for the judge and the panel to rule on.

Usage:
    python3 checks/judge_batches.py judge [--skip-visuals | --only-visuals]   # 288 single-judge checks, 15 per batch
    python3 checks/judge_batches.py panel                                     # the 42 panel checks

--only-visuals keeps the image checks (V-, and the Nov 11 image research groups F-, T-, R-, MC-, QA..QS), which the
visual judge rules on with the slides open; --skip-visuals keeps the rest for the text judge.

Each line: ID | the check | pass threshold | weight | GATE. The judge answers every line with a reason first, then
"pass", "fail" or "na" (does not apply to this post), and quotes the failing words. It never edits the post.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
crit = json.load(open(os.path.join(HERE, "criteria.json"), encoding="utf-8"))
tier = "Panel" if len(sys.argv) > 1 and sys.argv[1] == "panel" else "Judge"
rows = [c for c in crit if c["tier"] == tier and c["category"] != "system"]
IMAGE = re.compile(r"^(V-|F-|T-|R-|MC-|Q[A-Z]\d)")
if "--skip-visuals" in sys.argv:
    rows = [c for c in rows if not IMAGE.match(c["id"])]
if "--only-visuals" in sys.argv:
    rows = [c for c in rows if IMAGE.match(c["id"])]
size = 15
for b in range(0, len(rows), size):
    batch = rows[b:b + size]
    print(f"## {tier} batch {b // size + 1} of {(len(rows) + size - 1) // size} ({batch[0]['sub_name']} ...)")
    for c in batch:
        print(f"{c['id']} | {c['test']} | {c['pass']} | w{c['weight']}{' | GATE' if c['gate'] else ''}")
    print()
print(f"# {len(rows)} {tier.lower()} checks")
