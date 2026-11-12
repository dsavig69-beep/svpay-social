#!/usr/bin/env python3
"""Merge the reviewers' answers into the files score.py reads.

    python3 checks/merge.py judge judge-text.json judge-visual.json --out judge.json
    python3 checks/merge.py panel panel-1.json panel-2.json panel-3.json --out panel.json
    python3 checks/merge.py panel review-panel.json --out panel.json      # the light process: one reviewer

Each judge file is a list: [{"id": "W-A20", "where": "linkedin", "verdict": "pass"|"fail"|"na", "reason": "...", "quote": "..."}].

Each panel file is one reviewer's answer:
{"reader": "shop owner", "reads_as_person": true, "original": true,
 "refs": [{"ref": "2026-11-04 Toast carousel", "first_order": "win"|"tie"|"loss", "second_order": "win"|"tie"|"loss"}],
 "checks": [{"id": "SG-H01", "verdict": "pass"|"fail"|"na", "reason": "..."}]}

A reference counts as won or tied for a reviewer only when the post wins or ties in BOTH orders (this cancels the
pull of whichever post is read first). The panel's count is the median of the reviewers. A check's credit is the
share of reviewers who passed it (0, 0.33, 0.67 or 1; with the light process's one reviewer, 0 or 1).
"""
import argparse
import json
import statistics

ap = argparse.ArgumentParser()
ap.add_argument("kind", choices=["judge", "panel"])
ap.add_argument("files", nargs="+")
ap.add_argument("--out", required=True)
a = ap.parse_args()
docs = [json.load(open(f, encoding="utf-8")) for f in a.files]

if a.kind == "judge":
    out = [row for d in docs for row in d]
    bad = [r for r in out if r.get("verdict") not in ("pass", "fail", "na") or not r.get("id")]
    if bad:
        raise SystemExit(f"{len(bad)} judge rows have no id or a verdict other than pass/fail/na")
    print(f"{len(out)} judge verdicts: {sum(r['verdict'] == 'fail' for r in out)} fail, {sum(r['verdict'] == 'na' for r in out)} na")
else:
    if len(docs) not in (1, 3):
        raise SystemExit("the panel needs 3 reviewers (or 1 for the light process)")
    n = len(docs)
    ok = {"win", "tie"}
    refs = [sum(1 for r in d.get("refs", []) if r.get("first_order") in ok and r.get("second_order") in ok) for d in docs]
    per = {}
    for d in docs:
        for c in d.get("checks", []):
            if c.get("verdict") == "na":
                continue
            per.setdefault(c["id"], []).append((c.get("verdict") == "pass", c.get("reason", "")))
    out = {
        "judges": n,
        "readers": [d.get("reader", "") for d in docs],
        "not_ai_votes": sum(bool(d.get("reads_as_person")) for d in docs),
        "original_votes": sum(bool(d.get("original")) for d in docs),
        "refs_compared": max(len(d.get("refs", [])) for d in docs),
        "refs_won_or_tied": int(statistics.median(refs)),
        "per_check": [{"id": cid, "credit": round(sum(p for p, _ in v) / n, 2),
                       "reason": "; ".join(r for p, r in v if not p)[:300]} for cid, v in sorted(per.items())],
    }
    print(f"panel: {out['not_ai_votes']}/{n} read as a person, {out['original_votes']}/{n} original, "
          f"{out['refs_won_or_tied']}/{out['refs_compared']} references won or tied")
json.dump(out, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
