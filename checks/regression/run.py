#!/usr/bin/env python3
"""The checker's regression set: the founder-approved Nov 4 deck, the rejected Nov 11 renders and the rejected
Nov 11 tax-tips post. Run it under the heavy lock (it renders nothing, but it runs every check on every slide):

    flock /home/claude/work/heavy.lock python3 checks/regression/run.py [--only nov4,r1]

expected.json says what each case must show:
  "no_gates"         no gate in this list of groups may fail (the approved deck: every gate passes on it)
  "fail_on"          {slide: [gates]}: each rejected slide must fail at least one of its gates, the ones that name
                     the reason it was rejected (V-I07 for the Riverside renders)
  "gates_fail"       gates that must all fail (the tax-tips post's 11)

It writes checks/regression/results.json (all_pass, summary, three_revision, one row per case), which R-P02 and
R-P04 read on every post.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CHECK = os.path.join(REPO, "checks", "check_post.py")
CRIT = {c["id"]: c for c in json.load(open(os.path.join(REPO, "checks", "criteria.json"), encoding="utf-8"))}


def gate_rows(report, statuses=("fail", "needs_passes")):
    rows = []
    for r in report.get("results", []):
        c = CRIT.get(r["id"])
        if c and c.get("gate") and r["status"] in statuses:
            rows.append((r["id"], r["where"], r.get("detail", "")))
    return rows


def where_matches(where, slide):
    return where == slide or where.endswith(": " + slide) or where.replace("slide ", "slide-") == slide


def run_case(name, case):
    out = os.path.join(tempfile.gettempdir(), f"svpay-regression-{name}.json")
    cmd = [sys.executable, CHECK, os.path.join(REPO, case["package"]), "--offline", "--out", out]
    subprocess.run(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3600)
    rep = json.load(open(out, encoding="utf-8"))
    gates = gate_rows(rep)
    measured = gate_rows(rep, ("fail",))      # a rejected slide is caught only by a measured failure, never by "needs passes"
    problems = []
    if "no_gates" in case:
        prefixes = tuple(case["no_gates"])
        bad = sorted({f"{g} [{w}]" for g, w, _ in gates if g.startswith(prefixes) and g not in case.get("allowed", [])})
        if bad:
            problems.append("gates fail on the approved deck: " + ", ".join(bad))
    for slide, want in case.get("fail_on", {}).items():
        hit = sorted({g for g, w, _ in measured if g in want and where_matches(w, slide)})
        if not hit:
            problems.append(f"{slide} fails none of {', '.join(want)}")
    if "gates_fail" in case:
        failed = {g for g, _, _ in gates}
        miss = [g for g in case["gates_fail"] if g not in failed]
        if miss:
            problems.append("expected gates did not fail: " + ", ".join(miss))
    caught = {slide: sorted({g for g, w, _ in measured if g in want and where_matches(w, slide)}) for slide, want in case.get("fail_on", {}).items()}
    return {"case": name, "package": case["package"], "pass": not problems, "problems": problems, "caught": caught,
            "gates_failed": sorted({g for g, _, _ in gates})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated case names")
    a = ap.parse_args()
    exp = json.load(open(os.path.join(HERE, "expected.json"), encoding="utf-8"))
    names = [n for n in exp["cases"] if not a.only or n in a.only.split(",")]
    rows = []
    for n in names:
        r = run_case(n, exp["cases"][n])
        rows.append(r)
        print(f"{'PASS' if r['pass'] else 'FAIL'} {n}: " + ("; ".join(r["problems"]) or ", ".join(f"{s}: {'/'.join(g)}" for s, g in r["caught"].items()) or "ok"))
    rev = None
    for p in ("node_modules/three/package.json",):
        fp = os.path.join(REPO, p)
        if os.path.exists(fp):
            rev = json.load(open(fp))["version"].split(".")[1]
    allp = all(r["pass"] for r in rows) and len(names) == len(exp["cases"])
    res = {"all_pass": allp, "summary": f"{sum(r['pass'] for r in rows)} of {len(exp['cases'])} regression cases pass" + ("" if len(names) == len(exp["cases"]) else " (partial run)"),
           "three_revision": rev, "cases": rows}
    if len(names) == len(exp["cases"]):
        json.dump(res, open(os.path.join(HERE, "results.json"), "w", encoding="utf-8"), indent=1)
    print(res["summary"])
    sys.exit(0 if allp else 1)


if __name__ == "__main__":
    main()
