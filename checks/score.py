#!/usr/bin/env python3
"""Combine the script report, the judge's verdicts and the panel's verdicts into the publish decision.

Usage:
    python3 checks/score.py script_report.json judge.json panel.json [--out decision.json] [--light]

--light is the daily process the founder chose on Nov 12, 2026 (ALGORITHM.md, "The light process"): one fresh reviewer
instead of two judges and a panel of three. Facts, legal claims, copy rules and every script gate still block. The taste
gates in TASTE only lower the score, a reviewer who would flag the post as AI-written sends it to the founder (HOLD),
reference comparisons are not run, and only a post below LIGHT_REWRITE_BELOW or a blocking failure is rewritten.

judge.json: [{"id": "W-A20", "where": "linkedin", "verdict": "pass"|"fail"|"na", "reason": "...", "quote": "..."}, ...]
    One entry per judge-tier criterion that applies (see criteria.json, tier "Judge"). The reason comes first, then the
    verdict; failing words are quoted.
panel.json: {"judges": 3, "families": ["..."], "not_ai_votes": 0-3, "original_votes": 0-3,
             "refs_compared": 5, "refs_won_or_tied": 0-5, "per_check": [{"id": "SG-H05", "credit": 0|0.33|0.67|1}]}

Rules (ALGORITHM.md): any gate fails -> REWRITE; every category must clear its minimum; Q is a weighted geometric
mean; Q >= 0.85 with a "not AI" panel (2 of 3) and 3 of 5 reference wins -> PUBLISH; 0.75-0.85, one minimum missed by
0.05 or less, or a 2-1 split on a gate question -> HOLD; below 0.75 -> REWRITE.
"""
import argparse
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CRITERIA = {c["id"]: c for c in json.load(open(os.path.join(HERE, "criteria.json"), encoding="utf-8"))}
WEIGHTS = {"writing": 0.30, "audience_value": 0.25, "visuals": 0.20, "platform_fit": 0.15, "brand": 0.10}
MINIMUMS = {"writing": 0.85, "audience_value": 0.75, "visuals": 0.80, "platform_fit": 0.75, "brand": 0.80, "compliance": 0.95}
PUBLISH_AT = 0.85
HOLD_AT = 0.75
# The light process (founder, Nov 12, 2026): these taste gates score but never block. SG-H05 (would a daily ChatGPT
# user flag it as AI-written?) goes to the founder instead: HOLD, never a rewrite loop.
TASTE = {"V-L07", "V-I07"}
FOUNDER_DECIDES = {"SG-H05"}
LIGHT_REWRITE_BELOW = 0.70


def verdicts(script, judge, panel):
    """One verdict per criterion: a criterion that fails anywhere (any network, any slide) fails once."""
    v = {}

    def put(cid, credit, src, detail=""):
        if cid not in CRITERIA:
            return
        if cid in v:
            v[cid]["credit"] = min(v[cid]["credit"], credit)
            if credit < 1:
                v[cid]["why"].append(f"{src}: {detail}"[:160])
        else:
            v[cid] = {"credit": credit, "why": [f"{src}: {detail}"[:160]] if credit < 1 else []}

    for r in script.get("results", []):
        if r["status"] == "na":
            continue
        put(r["id"], 1.0 if r["status"] == "pass" else 0.0, f"script[{r['where']}]", r.get("detail", ""))
    for j in judge:
        if j.get("verdict") == "na":
            continue
        put(j["id"], 1.0 if j.get("verdict") == "pass" else 0.0, f"judge[{j.get('where', 'post')}]",
            (j.get("reason", "") + " " + j.get("quote", "")).strip())
    for p in panel.get("per_check", []):
        put(p["id"], float(p.get("credit", 0)), "panel", p.get("reason", ""))
    return v


def decide(script, judge, panel, light=False):
    v = verdicts(script, judge, panel)
    if light:
        return decide_light(v, panel)
    # A panel gate question carried 2 of 3 is a split, not a failure: it HOLDs (ALGORITHM.md). Only a panel gate
    # carried by fewer than 2 of 3, or any script or judge gate, fails.
    panel_ids = {p["id"] for p in panel.get("per_check", [])}
    split_gates = sorted(cid for cid, x in v.items() if CRITERIA[cid].get("gate") and cid in panel_ids
                         and 0.66 <= x["credit"] < 1 and all(w.startswith("panel") for w in x["why"]))
    gates = sorted(cid for cid, x in v.items() if CRITERIA[cid].get("gate") and x["credit"] < 1 and cid not in split_gates)
    # SG-H05: the panel's "not AI" verdict is a gate (at least 2 of 3), decided by the votes.
    not_ai = panel.get("not_ai_votes")
    if not_ai is not None:
        gates = [g for g in gates if g != "SG-H05"]
        split_gates = [g for g in split_gates if g != "SG-H05"]
        if not_ai < 2:
            gates.append("SG-H05")
    cats = {}
    for cat in list(WEIGHTS) + ["compliance"]:
        items = [(cid, x) for cid, x in v.items() if CRITERIA[cid].get("category") == cat and not CRITERIA[cid].get("gate")]
        tw = sum(int(CRITERIA[cid]["weight"]) for cid, _ in items)
        if tw == 0:
            cats[cat] = None
            continue
        cats[cat] = round(sum(int(CRITERIA[cid]["weight"]) * x["credit"] for cid, x in items) / tw, 3)
    missing = [c for c in WEIGHTS if cats.get(c) is None]
    q = None
    if not missing:
        q = round(math.exp(sum(WEIGHTS[c] * math.log(max(cats[c], 1e-6)) for c in WEIGHTS)), 3)
    below = {c: cats[c] for c in MINIMUMS if cats.get(c) is not None and cats[c] < MINIMUMS[c]}
    near = {c: s for c, s in below.items() if MINIMUMS[c] - s <= 0.05}
    refs = panel.get("refs_won_or_tied")
    split = not_ai == 2 or panel.get("original_votes") == 2 or bool(split_gates)

    if gates:
        decision = "REWRITE"
        why = "gate failed: " + ", ".join(gates)
    elif missing:
        decision = "HOLD"
        why = "not every category was scored: " + ", ".join(missing)
    elif below and set(below) != set(near):
        decision = "REWRITE"
        why = "below a category minimum: " + ", ".join(f"{c} {s}" for c, s in below.items())
    elif q < HOLD_AT:
        decision = "REWRITE"
        why = f"Q {q} below {HOLD_AT}"
    elif near or q < PUBLISH_AT or split or (refs is not None and refs < 3):
        decision = "HOLD"
        parts = []
        if near:
            parts.append("near a minimum: " + ", ".join(f"{c} {s}" for c, s in near.items()))
        if q < PUBLISH_AT:
            parts.append(f"Q {q} below {PUBLISH_AT}")
        if split:
            parts.append("2-1 panel split" + (f" on {', '.join(split_gates)}" if split_gates else ""))
        if refs is not None and refs < 3:
            parts.append(f"won or tied {refs} of 5 reference posts")
        why = "; ".join(parts)
    else:
        decision = "PUBLISH"
        why = f"all gates pass, every minimum met, Q {q}, panel {not_ai}/3 not AI, {refs}/5 references"
    worst = sorted(((cid, x) for cid, x in v.items() if x["credit"] < 1),
                   key=lambda t: (not CRITERIA[t[0]].get("gate"), -int(CRITERIA[t[0]]["weight"])))[:30]
    return {"decision": decision, "why": why, "Q": q, "categories": cats, "minimums": MINIMUMS,
            "gates_failed": gates, "criteria_scored": len(v),
            "fix_list": [{"id": cid, "gate": CRITERIA[cid].get("gate"), "weight": CRITERIA[cid]["weight"],
                          "test": CRITERIA[cid]["test"], "why": x["why"][:3]} for cid, x in worst]}


def decide_light(v, panel):
    soft = TASTE | FOUNDER_DECIDES
    gate = lambda cid: CRITERIA[cid].get("gate") and cid not in soft
    gates = sorted(cid for cid, x in v.items() if gate(cid) and x["credit"] < 1)
    not_ai = panel.get("not_ai_votes")
    judges = panel.get("judges") or 1
    ai_flag = (not_ai is not None and not_ai * 2 < judges) or v.get("SG-H05", {}).get("credit", 1) < 0.5
    cats = {}
    for cat in list(WEIGHTS) + ["compliance"]:
        items = [(cid, x) for cid, x in v.items() if CRITERIA[cid].get("category") == cat and not gate(cid)]
        tw = sum(int(CRITERIA[cid]["weight"]) for cid, _ in items)
        cats[cat] = round(sum(int(CRITERIA[cid]["weight"]) * x["credit"] for cid, x in items) / tw, 3) if tw else None
    missing = [c for c in WEIGHTS if cats.get(c) is None]
    q = None if missing else round(math.exp(sum(WEIGHTS[c] * math.log(max(cats[c], 1e-6)) for c in WEIGHTS)), 3)
    below = {c: cats[c] for c in MINIMUMS if cats.get(c) is not None and cats[c] < MINIMUMS[c]}
    if gates:
        decision, why = "REWRITE", "gate failed: " + ", ".join(gates)
    elif "compliance" in below:
        decision, why = "REWRITE", f"compliance {below['compliance']} below {MINIMUMS['compliance']}"
    elif missing:
        decision, why = "HOLD", "not every category was scored: " + ", ".join(missing)
    elif q < LIGHT_REWRITE_BELOW:
        decision, why = "REWRITE", f"Q {q} below {LIGHT_REWRITE_BELOW}"
    elif below or q < PUBLISH_AT or ai_flag:
        decision = "HOLD"
        parts = []
        if below:
            parts.append("below a minimum: " + ", ".join(f"{c} {s}" for c, s in below.items()))
        if q < PUBLISH_AT:
            parts.append(f"Q {q} below {PUBLISH_AT}")
        if ai_flag:
            parts.append("the reviewer would flag it as AI-written (SG-H05)")
        why = "for the founder: " + "; ".join(parts)
    else:
        decision, why = "PUBLISH", f"no blocking gate, every minimum met, Q {q}, the reviewer read it as a person"
    worst = sorted(((cid, x) for cid, x in v.items() if x["credit"] < 1),
                   key=lambda t: (not gate(t[0]), -int(CRITERIA[t[0]]["weight"])))[:30]
    return {"decision": decision, "why": why, "Q": q, "categories": cats, "minimums": MINIMUMS, "process": "light",
            "gates_failed": gates, "criteria_scored": len(v),
            "fix_list": [{"id": cid, "gate": bool(gate(cid)), "weight": CRITERIA[cid]["weight"],
                          "test": CRITERIA[cid]["test"], "why": x["why"][:3]} for cid, x in worst]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("judge")
    ap.add_argument("panel")
    ap.add_argument("--out")
    ap.add_argument("--light", action="store_true")
    a = ap.parse_args()
    script = json.load(open(a.script, encoding="utf-8"))
    judge = json.load(open(a.judge, encoding="utf-8")) if os.path.exists(a.judge) else []
    panel = json.load(open(a.panel, encoding="utf-8")) if os.path.exists(a.panel) else {}
    out = decide(script, judge, panel, light=a.light)
    txt = json.dumps(out, indent=1, ensure_ascii=False)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt)
    print(f"{out['decision']}: {out['why']}")
    print("categories: " + ", ".join(f"{k} {v}" for k, v in out["categories"].items()) + f" | Q {out['Q']}")
    for f in out["fix_list"][:15]:
        print(f"  {'GATE ' if f['gate'] else ''}{f['id']} (w{f['weight']}) {f['test'][:70]}")


if __name__ == "__main__":
    main()
