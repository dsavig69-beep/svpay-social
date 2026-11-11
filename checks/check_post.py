#!/usr/bin/env python3
"""SVPay post checker: the script tier of ALGORITHM.md.

Runs every script check that can be decided from the post package (the text for each network, the slides, the facts
log, the post history and the files) and writes a JSON report. Checks that need judgement are left to the judge and
the panel (see ALGORITHM.md); this script never edits a post.

Usage:
    python3 checks/check_post.py post.json [--history history.json] [--private-terms terms.txt]
                                           [--offline] [--out report.json]

Exit code: 0 when no gate fails, 1 when any gate fails, 2 when the package itself is malformed.
The IDs match checks/criteria.json (887 criteria, from the Nov 11, 2026 research).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import statistics
import sys
import urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
CRITERIA = {c["id"]: c for c in json.load(open(os.path.join(HERE, "criteria.json"), encoding="utf-8"))}

NETWORKS = ["linkedin", "facebook", "instagram", "x", "threads", "gbp"]
LIMITS = {"linkedin": 3000, "x": 280, "threads": 500, "gbp": 1500, "instagram": 2200, "facebook": 63206}
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
STATES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|"
          "Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|"
          "Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|"
          "Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|"
          "West Virginia|Wisconsin|Wyoming")
LOCAL = "Shakopee|Minneapolis|St\\. Paul|Saint Paul|Twin Cities|Eagan|Apple Valley|Savage|Prior Lake|Jordan|Belle Plaine|New Prague|Burnsville|Bloomington|Chaska"
BANNED_WORDS = ["blockchain", "on-chain", "onchain", "crypto", "token", "deposit", "withdrawal", "kyb"]
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF]")
URL_RE = re.compile(r"https?://[^\s)\]]+")


# ----------------------------------------------------------------------------------------------- helpers
def words(t: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9$%][A-Za-z0-9$%,.'\-]*", t)


def plain_words(t: str) -> list[str]:
    return [w.lower().strip(".,'\"-") for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", t)]


def strip_urls(t: str) -> str:
    return URL_RE.sub(" ", t)


def sentences(t: str) -> list[str]:
    t = strip_urls(t)
    t = re.sub(r"\n+", "\n", t)
    parts = re.split(r"(?<=[.!?])\s+|\n", t)
    out = [p.strip() for p in parts if re.search(r"[A-Za-z0-9]", p or "")]
    return out


def paragraphs(t: str) -> list[str]:
    # Social posts use line breaks as paragraph breaks: every non-empty line is a paragraph.
    return [p.strip() for p in strip_urls(t).split("\n") if re.search(r"[A-Za-z0-9]", p)]


def syllables(word: str) -> int:
    w = word.lower().strip(".,!?;:'\"()$%")
    if not w:
        return 0
    if w.isdigit():
        return max(1, len(w) // 2)
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    n = len(re.findall(r"[aeiouy]{1,2}", w))
    return max(1, n)


def readability(t: str) -> tuple[float, float]:
    ss = sentences(t)
    ws = [w for s in ss for w in re.findall(r"[A-Za-z0-9$%][A-Za-z0-9'\-]*", s)]
    if not ss or not ws:
        return 0.0, 100.0
    syl = sum(syllables(w) for w in ws)
    wps = len(ws) / len(ss)
    spw = syl / len(ws)
    fk = 0.39 * wps + 11.8 * spw - 15.59
    fre = 206.835 - 1.015 * wps - 84.6 * spw
    return round(fk, 1), round(fre, 1)


def count_any(t: str, phrases: list[str], word_boundary: bool = True) -> list[str]:
    hits = []
    low = t.lower()
    for p in phrases:
        pat = r"\b" + re.escape(p.lower()) + r"\b" if word_boundary else re.escape(p.lower())
        n = len(re.findall(pat, low))
        hits += [p] * n
    return hits


def shingles(t: str, n: int = 3) -> set[tuple[str, ...]]:
    ws = plain_words(t)
    return {tuple(ws[i:i + n]) for i in range(max(0, len(ws) - n + 1))}


def overlap(a: str, b: str, n: int = 3) -> float:
    sa, sb = shingles(a, n), shingles(b, n)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def cosine(a: str, b: str) -> float:
    ca, cb = Counter(plain_words(a)), Counter(plain_words(b))
    common = set(ca) & set(cb)
    num = sum(ca[w] * cb[w] for w in common)
    den = math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values()))
    return num / den if den else 0.0


def first_line(t: str) -> str:
    for line in t.split("\n"):
        if line.strip():
            return line.strip()
    return ""


def hashtags(t: str) -> list[str]:
    return re.findall(r"(?<!\w)#[A-Za-z][A-Za-z0-9_]*", t)


def numbers_in(t: str) -> list[str]:
    return [n.rstrip(".,") for n in re.findall(r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:[kKmMbB]|million|billion)?|\d[\d,]*(?:\.\d+)?\s?%", t)]


def norm_num(n: str) -> str:
    """A figure as a plain value, so "$1.62 billion", "$1.62B" and "1620000000" match, and "2.62" matches "$2.62"."""
    t = re.sub(r"[\s,$]", "", str(n).lower())
    pct = t.endswith("%")
    t = t.rstrip("%")
    mult = 1.0
    for suf, m in (("billion", 1e9), ("million", 1e6), ("b", 1e9), ("m", 1e6), ("k", 1e3)):
        if t.endswith(suf):
            t, mult = t[: -len(suf)], m
            break
    try:
        v = float(t) * mult
    except ValueError:
        return t
    return ("%g" % v) + ("%" if pct else "")


# ----------------------------------------------------------------------------------------------- the report
class Report:
    def __init__(self):
        self.results: list[dict] = []

    def add(self, cid: str, ok: bool | None, detail: str = "", where: str = "post", value=None):
        c = CRITERIA.get(cid, {})
        status = "na" if ok is None else ("pass" if ok else "fail")
        self.results.append({"id": cid, "where": where, "status": status, "gate": bool(c.get("gate")),
                             "weight": c.get("weight", 1), "category": c.get("category", "unmapped"),
                             "test": c.get("test", ""), "detail": detail, "value": value})

    def gates_failed(self):
        return [r for r in self.results if r["gate"] and r["status"] == "fail"]


# ----------------------------------------------------------------------------------------------- word lists
A_LISTS = {
    "W-A01": (["delve", "delves", "delving", "delved"], 0),
    "W-A02": (["underscore", "underscores", "underscoring", "underscored"], 0),
    "W-A03": (["tapestry"], 0),
    "W-A04": (["camaraderie", "palpable", "intricate", "amidst", "unease"], 0),
    "W-A05": (["landscape", "paradigm", "synergy", "ecosystem", "realm"], 0),
    "W-A06": (["leverage", "leverages", "leveraging", "utilize", "utilizes", "utilizing", "robust", "streamline",
               "streamlines", "harness", "harnesses", "certainly"], 0),
    "W-A07": (["quietly", "deeply", "fundamentally", "arguably"], 1),
    "W-A08": (["it's worth noting", "it is worth noting", "importantly", "interestingly", "notably"], 0),
    "W-A09": (["serves as", "stands as"], 0),
    "W-A10": (["showcase", "showcases", "pivotal", "testament", "vibrant", "crucial", "foster", "fosters", "enhance",
               "enhances", "navigate", "navigating"], 1),
    "W-A12": (["game-changer", "game changer", "game-changing", "revolutionary", "unprecedented", "seamless",
               "seamlessly", "cutting-edge", "all-in-one"], 0),
    "W-A13": (["reshape", "reshapes", "reshaping", "transform everything", "redefine", "redefines", "upend", "upends"], 0),
    "W-A14": (["in today's fast-paced", "ever-evolving", "in today's digital world", "in the digital age"], 0),
    "W-A17": (["solution", "solutions", "empower", "empowers", "unlock", "unlocks", "drive value", "elevate", "elevates"], 0),
    "W-A18": (["journey", "beacon", "symphony"], 0),
    "W-A21": (["may potentially", "could possibly", "nearly always", "somewhat"], 1),
    "W-A22": (["truly", "incredibly", "absolutely", "literally"], 1),
}
B_LISTS = {
    "W-B01": ["here's the thing", "here is the thing"],
    "W-B02": ["here's the kicker", "the kicker?"],
    "W-B04": ["here's something most people don't know", "something most people don't know", "most people don't know",
              "what nobody tells you", "nobody talks about", "no one talks about"],
    "W-B05": ["unpopular opinion", "unpopular take", "hot take", "controversial take", "controversial opinion"],
    "W-B06": ["let that sink in"],
    "W-B07": ["read that again"],
    "W-B08": ["let's break this down", "let's break it down", "let's unpack", "unpack this", "let's dive in", "dive in",
              "deep dive"],
    "W-B09": ["imagine a world where", "picture this"],
    "W-B10": ["think of it as", "think of it like"],
    "W-B11": ["in conclusion", "to sum up", "bottom line", "tl;dr", "tldr"],
    "W-B15": ["what do you think?"],
    "W-B16": ["it's important to remember", "it's important to note", "it's important to understand",
              "it is important to remember", "it is important to note"],
    "W-B17": ["despite its challenges", "despite these challenges", "despite the challenges"],
    "W-B18": ["is here to stay", "the future of"],
    "W-B19": ["plot twist:", "spoiler:"],
    "W-B20": ["the truth is", "the reality is", "the fact is"],
    "W-B21": ["most people think", "everyone says", "everybody says"],
    "W-B22": ["experts say", "studies show", "many believe", "research shows", "experts agree", "analysts say"],
    "W-B23": ["excited to share", "thrilled to share", "humbled to share", "excited to announce", "thrilled to announce"],
    "W-B25": ["this is huge", "this changes everything"],
    "W-B26": ["ever wondered", "ever struggled"],
    "W-B28": ["full stop.", "period."],
    "W-C25": ["let me explain", "worth being precise about", "let me be clear"],
    "W-H13": ["you won't believe", "insane", "shocking truth", "mind-blowing", "jaw-dropping"],
    "W-K09": ["before it's too late", "don't miss out", "act now", "last chance"],
    "LI-13": ["dm me", "message me", "send me a dm"],
    "X-04": ["giveaway", "airdrop", "dm me", "100x"],
}
HOOK_CLICHES = ["stop scrolling", "nobody talks about", "no one talks about", "unpopular opinion", "hot take",
                "i was today years old", "this might be controversial", "here's something most people don't know",
                "most people don't know", "here's the thing", "let that sink in", "you won't believe"]
ENGAGEMENT_BAIT = [r"\bcomment\s+['\"]?(yes|no|below|[A-Z]{2,})['\"]?", r"\blike if\b", r"\btag (someone|a friend|a)\b",
                   r"\brepost (this|to)\b", r"\bshare (this|if)\b", r"\bsmash (the|that)\b", r"\bdrop an?\s+\S+\s+(below|in the comments)\b",
                   r"\breact with\b", r"\bvote (below|in the comments)\b", r"\bagree\?\s*$", r"\bthoughts\?\s*$"]
MED_METAPHORS = ["treat the cause", "the symptom", "diagnosis", "diagnose", "prescription", "prescribe", "triage", "vital signs"]
BRITISH = {"colour": "color", "favour": "favor", "behaviour": "behavior", "organise": "organize", "organisation": "organization",
           "centre": "center", "cheque": "check", "analyse": "analyze", "realise": "realize", "recognise": "recognize",
           "labour": "labor", "honour": "honor", "neighbour": "neighbor", "programme": "program", "catalogue": "catalog",
           "travelled": "traveled", "cancelled": "canceled", "modelling": "modeling", "licence": "license", "defence": "defense"}
ACRONYMS = ["MDR", "BIN", "MCC", "ACH", "PCI", "POS", "CFPB", "FTC", "NRA", "MPC", "RTP"]


# ----------------------------------------------------------------------------------------------- text checks
def check_text(rep: Report, net: str, text: str, kind: str, history: list[dict], first_comment: str = ""):
    where = net
    low = text.lower()
    body = strip_urls(text)
    lowbody = body.lower()
    ws = words(body)
    nwords = max(1, len(ws))
    fl = first_line(body)
    is_news = kind == "news"

    # 1A AI vocabulary
    cluster = set()
    for cid, (lst, limit) in A_LISTS.items():
        hits = count_any(lowbody, lst)
        if cid in ("W-A01", "W-A02", "W-A03", "W-A04", "W-A05", "W-A06", "W-A07", "W-A08", "W-A09", "W-A10"):
            cluster |= {h.lower() for h in hits}
        rep.add(cid, len(hits) <= limit, ", ".join(hits), where, len(hits))
    rep.add("W-A11", len(cluster) < 2, ", ".join(sorted(cluster)), where, len(cluster))
    nomin = [w for w in plain_words(body) if len(w) > 5 and re.search(r"(tion|ment|ness|ity|ance)s?$", w)]
    rep.add("W-A15", len(nomin) * 100 / nwords <= 4, ", ".join(nomin[:8]), where, round(len(nomin) * 100 / nwords, 1))

    # 1B stock phrases
    for cid, lst in B_LISTS.items():
        if cid == "X-04" and net != "x":
            continue
        if cid == "LI-13" and net != "linkedin":
            continue
        hits = count_any(lowbody, lst, word_boundary=False)
        rep.add(cid, not hits, ", ".join(hits), where, len(hits))
    setup = [s for s in sentences(body) if re.fullmatch(r"here's (why|how|what)[.:!]?", s.strip().lower())]
    rep.add("W-B03", not setup, "; ".join(setup), where)
    agree = re.findall(r"(?im)^\s*(agree\?|thoughts\?)\s*$", body)
    rep.add("W-B13", not agree, "; ".join(agree), where)
    rep.add("W-B24", not re.search(r"\b(BREAKING|Big news)\b", body), "", where)

    # 1C structure
    negpar = []
    for pat in [r"\b(it'?s|this is|that'?s|it is|this isn'?t|that isn'?t)\s+not\s+(just\s+|only\s+|about\s+)?[^.;:!?\n]{1,40},\s*(it'?s|but|it is)\b",
                r",\s+not\s+(just\s+|only\s+|merely\s+)?(the\s+|a\s+|your\s+)?[a-z]+[.!]",
                r"\bnot\s+(just|only|merely)\s+(about\s+)?(the\s+|a\s+)?[a-z]+,",
                r"\b(isn'?t|is not|aren'?t|are not)\s+(the\s+)?(problem|point|issue|question)[.;,]"]:
        negpar += [m.group(0) for m in re.finditer(pat, body, re.I)]
    rep.add("W-C01", not negpar, " | ".join(negpar[:4]), where, len(negpar))
    notnot = re.findall(r"\bNot [^.]{1,40}\.\s+Not [^.]{1,40}\.\s+(Just|Only)\b", body)
    rep.add("W-C02", not notnot, "", where)
    probl = re.findall(r"\b(isn'?t|is not) the problem\.|\bthe problem isn'?t\b|\bthe (real )?problem is\b", body, re.I)
    rep.add("W-C03", not probl, "", where)
    qa = re.findall(r"(?:^|[.!\n]\s*)((?:The|And the|So the)\s+\w+\?)\s+\S", body)
    rep.add("W-C06", not qa, "; ".join(qa), where)
    ss = sentences(body)
    paras = paragraphs(body)
    last_para = paras[-1] if paras else ""
    questions = [s for s in ss if s.endswith("?") and s not in last_para]
    rep.add("W-C07", len(questions) <= 1, " | ".join(questions[:3]), where, len(questions))
    reveals = re.findall(r"(?i)\b(here's my take|my take|the catch|the twist|the kicker|the result|the answer|the lesson|here's why|here's how)\s*:", body)
    rep.add("W-C08", len(reveals) <= 1, ", ".join(reveals), where, len(reveals))
    firsts = [re.sub(r"[^a-z']", "", s.split()[0].lower()) if s.split() else "" for s in ss]
    anaphora = any(firsts[i] and firsts[i] == firsts[i + 1] == firsts[i + 2] for i in range(len(firsts) - 2))
    rep.add("W-C11", not anaphora, "", where)
    trailing = re.findall(r",\s+(highlighting|underscoring|reflecting|showcasing|signaling|signalling|emphasizing|demonstrating|illustrating)\b", body, re.I)
    rep.add("W-C12", not trailing, ", ".join(trailing), where)
    participial = re.findall(r",\s+[a-z]+ing\b", body)
    rep.add("W-C13", len(participial) * 100 / nwords <= 1, ", ".join(participial[:5]), where, round(len(participial) * 100 / nwords, 2))
    devices = len(negpar) + len(notnot) + len(qa) + len(questions) + len(reveals)
    rep.add("W-C28", devices * 100 / nwords <= 1.0, "", where, round(devices * 100 / nwords, 2))

    # 1D rhythm and layout
    para_sent = [len(sentences(p)) for p in paras]
    one = [n == 1 for n in para_sent]
    share = sum(one) / len(one) if one else 0
    rep.add("W-D01", share <= 0.5, f"{sum(one)} of {len(one)} paragraphs are one sentence", where, round(share, 2))
    rep.add("W-D02", sum(1 for n in para_sent if n >= 2) >= 2 or net in ("x", "threads"), "", where)
    run = best = 0
    for o in one:
        run = run + 1 if o else 0
        best = max(best, run)
    rep.add("W-D03", best <= 3 or net == "x", f"longest run {best}", where, best)
    lens = [len(s.split()) for s in ss]
    if len(lens) >= 3:
        sd = statistics.pstdev(lens)
        mean = statistics.mean(lens)
        rep.add("W-D04", sd >= 5, "", where, round(sd, 1))
        rep.add("W-D05", (sd / mean if mean else 0) >= 0.45, "", where, round(sd / mean if mean else 0, 2))
        rep.add("W-D06", max(lens) >= 18 and min(lens) <= 6, "", where)
        rep.add("W-D07", max(lens) <= 30, "", where, max(lens))
        rep.add("W-D08", 9 <= mean <= 18, "", where, round(mean, 1))
        short_lines = sum(1 for p in paras if len(p.split()) < 6) / max(1, len(paras))
        rep.add("W-D09", short_lines <= 0.30, "", where, round(short_lines, 2))
        frag_run = 0
        frag_best = 0
        for p in paras:
            frag_run = frag_run + 1 if len(p.split()) < 6 else 0
            frag_best = max(frag_best, frag_run)
        rep.add("W-D10", frag_best < 3, "", where, frag_best)
        opens = [f for f in firsts if f]
        rep.add("W-D21", (len(set(opens)) / len(opens) if opens else 1) >= 0.6, "", where)
    em = body.count("—")
    rep.add("W-D11", em <= 1 and "—" not in fl, "", where, em)
    rep.add("W-D12", " -- " not in body, "", where)
    deco = re.findall(r"(?m)^\s*(→|✅|\U0001F449|➡|✔|‣|▶)", body)
    rep.add("W-D13", not deco, "", where)
    em_n = len(EMOJI_RE.findall(body))
    em_cap = {"linkedin": 1, "instagram": 3, "threads": 3, "facebook": 3, "gbp": 2, "x": 1}[net]
    rep.add("W-D14", em_n <= em_cap and not (net == "linkedin" and EMOJI_RE.search(fl)), "", where, em_n)
    rep.add("W-D15", not re.search(r"[\U0001D400-\U0001D7FF]|\*\*", body), "", where)
    rep.add("W-D16", not re.search(r"(?m)^\s*[-•*]\s*\*?\*?[A-Z][^:]{1,30}:", body), "", where)
    title_lines = [p for p in paras if len(p.split()) >= 3 and all(w[0].isupper() for w in p.split() if w[0].isalpha())
                   and not p.isupper() and not all(w.startswith("#") for w in p.split())]
    rep.add("W-D17", not title_lines, " | ".join(title_lines[:2]), where)
    rep.add("W-D18", not ("“" in body and '"' in body), "", where)
    bullets = re.findall(r"(?m)^\s*([-•*]|\d+[.)])\s+", body)
    rep.add("W-D19", len(bullets) <= 5 if is_news else True, "", where, len(bullets))
    long_paras = [p for p in paras if len(p.split()) > 60]
    rep.add("W-D20", not long_paras, "", where)

    # 1E specificity
    named = set(re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*|[A-Z]{2,})\b", body))
    named -= {"I", "SVPay", "A", "The", "And", "But", "So", "It", "My", "We", "You", "Your"}
    rep.add("W-E03", len(named) >= 2, ", ".join(sorted(named)[:8]), where, len(named))
    place = re.search(r"\b(" + STATES + "|" + LOCAL + r")\b", body)
    rep.add("W-E04", bool(place), place.group(0) if place else "", where)
    if is_news:
        rep.add("W-E05", bool(re.search(r"\b(" + MONTHS + r")\b|\b20\d\d\b", body)), "", where)
        anchor = re.search(r"\b(today|yesterday|this week|last week|this month|on (" + MONTHS + r")\s+\d{1,2})\b", body, re.I)
        rep.add("W-E28", bool(anchor), anchor.group(0) if anchor else "", where)
    generic = count_any(lowbody, ["businesses", "companies", "people", "owners everywhere", "business owners"])
    rep.add("W-E11", len(generic) <= 2, ", ".join(generic), where, len(generic))
    propers = [w for w in re.findall(r"\b[A-Z][a-zA-Z]+\b", body)]
    rep.add("W-E12", len(propers) * 100 / nwords >= 3, "", where, round(len(propers) * 100 / nwords, 1))
    vague = count_any(lowbody, ["thousands of", "millions of", "countless", "a lot of", "tons of", "lots of"])
    rep.add("W-E15", not vague, ", ".join(vague), where)
    vq = count_any(lowbody, ["many", "most", "often"])
    rep.add("W-E16", len(vq) <= 1, "", where, len(vq))
    vv = count_any(lowbody, ["impact", "impacts", "affect", "affects", "address", "tackle", "tackles"])
    rep.add("W-E20", len(vv) <= 1, ", ".join(vv), where, len(vv))
    pct_no_base = [m.group(0) for m in re.finditer(r"\d+(?:\.\d+)?\s?%(?!\s+(of|on|cap|rate|back|all-in|card rate|fee))", body)]
    rep.add("W-E24", not pct_no_base, ", ".join(pct_no_base[:4]), where, len(pct_no_base))
    ex = re.findall(r"(?i)example only", body)
    footer_ex = bool(paras) and re.search(r"(?i)^\(?\s*example only", paras[-1] or "")
    rep.add("W-E25", len(ex) <= 1 and not footer_ex, "disclaimer sits in a footer" if footer_ex else "", where)
    concrete = [p for p in paras if re.search(r"\d|\b[A-Z][a-z]+\b(?<!^)|\"|“", p[1:] if p else "")]
    rep.add("W-E27", len(concrete) / max(1, len(paras)) >= 0.8, "", where, round(len(concrete) / max(1, len(paras)), 2))

    # 1F voice
    actions = re.findall(r"\bI (asked|called|counted|talked|saw|sat|watched|met|visited|ran|checked|read|looked|heard|spoke|walked|pulled)\b", body)
    rep.add("W-F07", bool(actions), ", ".join(actions), where)
    i_n = len(re.findall(r"\bI\b", body))
    we_n = len(re.findall(r"\bwe\b", body, re.I))
    rep.add("W-F08", not (i_n and we_n), "", where)
    sv_sent = [s for s in ss if "svpay" in s.lower()]
    sv_share = sum(len(s.split()) for s in sv_sent) / nwords
    rep.add("W-F19", sv_share <= 0.20, "", where, round(sv_share, 2))

    # 1G nurse persona (rolling ones use history)
    nurse = bool(re.search(r"\b(nurse|nursing|ICU|RN)\b", body))
    rep.add("W-G05", not re.search(r"(?i)\bas an? (icu |er )?nurse,? i\b", body), "", where)
    pitch_idx = [i for i, s in enumerate(ss) if "svpay" in s.lower()]
    cred_before_pitch = any(i > 0 and re.search(r"\b(nurse|nursing|ICU|RN)\b", ss[i - 1]) for i in pitch_idx)
    rep.add("W-G02", not cred_before_pitch, "", where)
    rep.add("W-G07", not re.search(r"(?i)\bDAISY\b", body) or not is_news, "", where)
    rep.add("W-G11", not re.search(r"(?i)\bmethodist\b", body), "", where)
    med = count_any(lowbody, MED_METAPHORS)
    if history is not None:
        recent = history[-3:]
        nurse_recent = sum(1 for h in recent if h.get("mentions_nurse"))
        rep.add("W-G01", not (nurse and nurse_recent >= 1), f"nurse in {nurse_recent} of last 3 posts", where)
        med_recent = sum(1 for h in recent if h.get("medical_metaphor"))
        rep.add("W-G04", len(med) == 0 or (len(med) <= 1 and med_recent == 0), ", ".join(med), where)
        month_ago = [h for h in history if h.get("date", "") >= (dt.date.today() - dt.timedelta(days=30)).isoformat()]
        tag_recent = sum(1 for h in month_ago if h.get("tagline"))
        tag_now = "not a hedge fund" in lowbody
        rep.add("W-G09", not tag_now or tag_recent == 0, "", where)

    # 1H hook
    fl_words = fl.split()
    rep.add("W-H01", len(fl_words) <= 12, fl, where, len(fl_words))
    rep.add("W-H02", bool(re.search(r"\d", fl) or re.search(r"(?<!^)\b[A-Z][a-z]+", fl[1:])), fl, where)
    rep.add("W-H03", not fl.endswith("?"), fl, where)
    opener = " ".join(paras[:2]).lower() if paras else ""
    cl = [h for h in HOOK_CLICHES if h in opener]
    rep.add("W-H07", not cl, ", ".join(cl), where)
    rep.add("W-H08", not re.match(r"(?i)i'?m (so )?(excited|thrilled|proud)", fl), "", where)
    split = len(paras) >= 2 and not re.search(r"[.!?:]$", paras[0]) and paras[1][:1].islower()
    rep.add("W-H10", not split, "", where)
    caps = [w for w in re.findall(r"\b[A-Z]{3,}\b", fl) if w not in ACRONYMS + ["SVPAY", "ICU", "USA", "CEO"]]
    rep.add("W-H15", not caps, ", ".join(caps), where)
    rep.add("W-H17", not re.match(r"(?i)(most people think|everyone thinks)", fl), "", where)
    if history is not None:
        prev_hooks = [h.get("hook", "") for h in history[-20:] if h.get("network") == net]
        start = " ".join(plain_words(fl)[:4])
        clash = [h for h in prev_hooks if start and " ".join(plain_words(h)[:4]) == start]
        four = shingles(fl, 4)
        clash4 = [h for h in prev_hooks[-10:] if four & shingles(h, 4)]
        rep.add("W-H11", not clash and not clash4, " | ".join((clash + clash4)[:2]), where)

    # 1I news structure
    if is_news:
        states_no_bill = []
        for m in re.finditer(r"\b(" + STATES + r")\b", body):
            ctx = body[m.end(): m.end() + 80]
            if re.search(r"\bbills?\b", ctx, re.I) and not re.search(r"\b(SB|HB|S\.|H\.B\.|A|S|Bill)\s?\d{2,5}\b", ctx):
                states_no_bill.append(m.group(0))
        rep.add("W-I14", len(states_no_bill) <= 1, ", ".join(states_no_bill), where, len(states_no_bill))
        unnamed = count_any(lowbody, ["lawmakers", "card groups", "the industry", "officials", "regulators", "banks and card"])
        rep.add("W-I16", len(unnamed) <= 1, ", ".join(unnamed), where, len(unnamed))

    # 1J artifacts
    rep.add("W-J11", not re.search(r"utm_source=(chatgpt|openai)", low), "", where)
    rep.add("W-J12", not re.search(r"(?i)as an ai\b|turn\d+search\d+|oaicite|\[insert|certainly! here'?s", text), "", where)
    rep.add("W-J13", not re.search(r"\[(name|company|date|link|x+)\]|\bXX%|20\d\d-xx-xx", text, re.I), "", where)
    rep.add("W-J14", not re.search(r"(?m)^#{1,3}\s|\*\*|^---\s*$", text), "", where)

    # 1K calls to action
    tags = re.findall(r"(?<!\w)@[A-Za-z0-9_.]+", body)
    cap = {"linkedin": 3, "x": 2}.get(net, 3)
    rep.add("W-K05", len(tags) <= cap, ", ".join(tags), where, len(tags))
    if history is not None:
        q_recent = sum(1 for h in history[-9:] if h.get("ends_with_question"))
        ends_q = bool(paras) and paras[-1].rstrip().endswith("?")
        rep.add("W-K03", q_recent + (1 if ends_q else 0) <= 5, f"{q_recent} of last 9 ended in a question", where)

    # 1L readability
    fk, fre = readability(body)
    rep.add("W-L01", 5 <= fk <= 8, "", where, fk)
    rep.add("W-L02", 60 <= fre <= 85, "", where, fre)
    passive = re.findall(r"\b(is|are|was|were|be|been|being)\s+\w+ed\b", body, re.I)
    pshare = len(passive) / max(1, len(ss))
    rep.add("W-L03", 0.03 <= pshare <= 0.15 or len(ss) < 8, "", where, round(pshare, 2))
    bad_acr = []
    for a in ACRONYMS:
        m = re.search(r"\b" + a + r"\b", body)
        if m and not re.search(r"\(" + a + r"\)", body):
            bad_acr.append(a)
    rep.add("W-L05", not bad_acr, ", ".join(bad_acr), where)
    mixed = bool(re.search(r"\$\d[\d,]{3,}", body)) and bool(re.search(r"\$?\d+(\.\d)?k\b", body))
    rep.add("W-L06", not mixed, "", where)

    # 1N sameness
    if history is not None:
        prev = [h for h in history[-20:]]
        sims = [(round(cosine(body, h.get("text", "")), 2), h.get("date", "")) for h in prev if h.get("text")]
        mx = max(sims) if sims else (0, "")
        rep.add("W-N01", mx[0] <= 0.85, f"closest {mx}", where, mx[0])
        ht_prev = next((h.get("hashtags") for h in reversed(history) if h.get("network") == net), None)
        rep.add("W-N09", ht_prev is None or sorted(ht_prev) != sorted(hashtags(text)) or not ht_prev, "", where)

    # 2x platform limits and placement
    n = len(text)
    lim = LIMITS[net]
    gate_id = {"linkedin": "LI-21", "x": "X-01", "threads": "TH-01", "gbp": "GBP-05"}.get(net)
    if gate_id:
        rep.add(gate_id, n <= lim, f"{n} characters (limit {lim})", where, n)
    tags_n = len(hashtags(text))
    if net == "linkedin":
        rep.add("LI-22", 1200 <= n <= 1800, "", where, n)
        first140 = body[:140]
        rep.add("LI-23", bool(re.search(r"\$\d|\d%|\b(law|bill|court|Act|ruling)\b", first140)), first140, where)
        rep.add("LI-24", not re.search(r"#|https?://|@", text[:210]), "", where)
        rep.add("LI-34", tags_n <= 3, "", where, tags_n)
        rep.add("LI-35", not URL_RE.search(text), "", where)
    if net == "instagram":
        rep.add("IG-16", tags_n <= 5, "", where, tags_n)
        rep.add("IG-19", not URL_RE.search(text), "", where)
        caps_n = len([w for w in re.findall(r"\b[A-Z]{3,}\b", body) if w not in ACRONYMS + ["SVPAY", "ICU"]])
        rep.add("IG-24", caps_n <= 1, "", where, caps_n)
    if net == "facebook":
        rep.add("FB-10", tags_n <= 2, "", where, tags_n)
        caps_n = len([w for w in re.findall(r"\b[A-Z]{3,}\b", body) if w not in ACRONYMS + ["SVPAY", "ICU"]])
        rep.add("FB-11", caps_n <= 1, "", where, caps_n)
        rep.add("FB-13", not URL_RE.search(text), "", where)
    if net == "x":
        rep.add("X-18", not URL_RE.search(text), "", where)
        rep.add("X-19", tags_n <= 1, "", where, tags_n)
    if net == "threads":
        rep.add("TH-10", tags_n == 1, "", where, tags_n)
    if net == "gbp":
        phone = re.search(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}|[\w.+-]+@[\w-]+\.[\w.]+|(?<!\w)@[A-Za-z0-9_]{3,}", text)
        rep.add("GBP-01", not phone, phone.group(0) if phone else "", where)
        wn = len(text.split())
        rep.add("GBP-05", n <= 1500, f"{n} characters, {wn} words (target 80-100)", where, n)
    rep.add("ALL-10", tags_n <= {"x": 1, "gbp": 0, "instagram": 5}.get(net, 3), "", where, tags_n)
    bait = [p for p in ENGAGEMENT_BAIT if re.search(p, body, re.I | re.M)]
    rep.add("ALL-03", not bait, ", ".join(bait), where)

    # copy rules and compliance phrases
    copy_checks(rep, net, text, first_comment)


def copy_checks(rep: Report, where: str, text: str, first_comment: str = ""):
    low = text.lower()
    rep.add("SC01", not re.search(r"\bcash[\s-]back\b", low), "", where)
    brit = [w for w in BRITISH if re.search(r"\b" + w + r"s?\b", low)]
    rep.add("SC02", not brit, ", ".join(brit), where)
    banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"s?\b", low)]
    rep.add("SC03", not banned, ", ".join(banned), where)
    rep.add("SC05", not re.search(r"(?i)\b(built|made|created|written) (with|using|by) (ai|artificial intelligence|chatgpt|claude)\b", text), "", where)
    rep.add("SC07", not re.search(r"(?i)never holds? (your|their|any) money|non-custodial|noncustodial", text), "", where)
    rep.add("CO18", not re.search(r"(?i)never holds? (your|their|any) money|non-custodial|noncustodial|never touches your money", text), "", where)
    bare = re.findall(r"(?<![/.\w])svpay\.app", low)
    rep.add("SC08", not bare, "", where)
    rep.add("SC09", not re.search(r"Derrik Savig(?!,? BSN, RN)", text), "", where)
    rep.add("SC13", not re.search(r"(?i)\bUSDC\b|\bSolana\b|\bstablecoin", text), "", where)
    rep.add("CO15", not re.search(r"(?i)payment stablecoin", text), "", where)
    rep.add("SC14", not re.search(r"(?i)(waiting|pending) (on|for) (a )?(lawyer|counsel|attorney)", text), "", where)
    rep.add("SC20", not re.search(r"(?i)\bSVPay is free\b|\bfree forever\b|\bno fees ever\b", text), "", where)
    if re.search(r"\$5\b", text) and re.search(r"(?i)first payment", text):
        rep.add("SC21", "$5 back on your first payment" in text, "", where)
    if re.search(r"\$20\b", text) and re.search(r"(?i)first order", text):
        rep.add("SC21", "first order up to $20 on us" in text, "", where)
    savings = re.search(r"(?i)\b(save|saves|saving|savings|keep|kept)\b[^.\n]{0,60}(\$\d|\d%)|(\$\d[\d,]*|\d+%)[^.\n]{0,40}\b(saved|savings|back in your pocket)\b", text)
    if savings:
        has_upto = bool(re.search(r"(?i)\bup to\b", text))
        has_note = bool(re.search(r"(?i)savings vary|example only|assum", text))
        rep.add("SC06", has_upto and has_note, savings.group(0), where)
    rep.add("CO12", not re.search(r"(?i)\bFDIC\b|\binsured\b|\bbank account\b|deposit insurance", text), "", where)
    rep.add("CO16", not re.search(r"(?i)safe as a bank|risk[- ]free|\bguaranteed\b", text), "", where)
    rep.add("CO19", not re.search(r"(?i)\bSVPay (is|are)?\s*(fully |federally |state[- ])?(licensed|regulated|approved)\b", text), "", where)
    rep.add("CO20", not re.search(r"(?i)unhackable|fraud[- ]proof|100% secure|completely secure", text), "", where)
    if re.search(r"(?i)\bsurcharg", text):
        rep.add("CO38", not re.search(r"(?i)surcharg[^.]{0,60}\bdebit\b|\bdebit\b[^.]{0,60}surcharg", text), "", where)
    if re.search(r"(?i)no hidden fees", text):
        rep.add("CO51", "svpay.app/pricing" in (text + first_comment).lower(), "", where)
    rep.add("CR33", not re.search(r"(?i)free money|zero cost, zero catch|save 100%|never pay (a )?fees? again", text), "", where)
    rep.add("CR20", not re.search(r"(?i)\b(always|guaranteed|100%)\b", strip_urls(text)), "", where)
    rep.add("CR17", not re.search(r"(?i)\bgoing viral\b|\bwent viral\b|\bimpressions\b|\bfollowers\b", text), "", where)
    rep.add("AF24", not re.search(r"(?i)to the moon|passive income|get rich|\bgains\b|price (is )?(up|pumping)", text), "", where)
    rep.add("AF25", not re.search(r"(?i)\b(make|earn) (an extra )?\$\d[\d,]*( more)?( a| per)? (month|year|week)", text), "", where)
    rep.add("AF40", not re.search(r"(?i)(set ?up|switch|get started) in (under )?\d+ (minutes|seconds)", text), "", where)
    rep.add("PP08", not re.search(r"(?i)send \$\d+[^.]{0,30}get \$\d+", text), "", where)
    owner_terms = re.search(r"(?i)swipe fees?|card fees?|processing fees?|surcharg|cash discount|dual pricing|chargebacks?", text)
    rep.add("AF02", bool(owner_terms), "", where)
    for u in URL_RE.findall(text + "\n" + first_comment):
        if "utm_" in u:
            bad = [p for p in re.findall(r"utm_[a-z]+=([^&\s]+)", u) if not re.fullmatch(r"[a-z0-9_\-]+", p)]
            rep.add("CO47", not bad, u, where)


# ----------------------------------------------------------------------------------------------- package checks
def check_package(pkg: dict, rep: Report, history: list[dict] | None, private_terms: list[str], offline: bool):
    nets = pkg.get("networks", {})
    kind = pkg.get("kind", "news")
    facts = pkg.get("facts", [])
    slides = pkg.get("slides", [])
    all_text = "\n".join((v.get("text", "") + "\n" + v.get("first_comment", "") + "\n" + v.get("reply", "")) for v in nets.values())
    slide_text = "\n".join(s.get("text", "") for s in slides)

    for net in NETWORKS:
        v = nets.get(net)
        if not v:
            continue
        check_text(rep, net, v.get("text", ""), kind, history, v.get("first_comment", ""))

    # private terms (pilot shops, customers): never in any text, slide, alt text or file name
    hay = (all_text + "\n" + slide_text + "\n" + " ".join(a for v in nets.values() for a in v.get("alt", []))
           + "\n" + " ".join(os.path.basename(m) for v in nets.values() for m in v.get("media", []))).lower()
    found = [t for t in private_terms if t and t.lower() in hay]
    rep.add("SC04", not found, "a private name appears" if found else "", "all")
    banned_slides = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"s?\b", (slide_text + " " + " ".join(
        os.path.basename(m) for v in nets.values() for m in v.get("media", []))).lower())]
    rep.add("SC25", not banned_slides, ", ".join(banned_slides), "slides")

    # cross-network
    texts = {n: strip_urls(v.get("text", "")) for n, v in nets.items() if v.get("text")}
    worst = (0.0, "")
    ns = list(texts)
    for i in range(len(ns)):
        for j in range(i + 1, len(ns)):
            o = overlap(texts[ns[i]], texts[ns[j]])
            if o > worst[0]:
                worst = (round(o, 2), f"{ns[i]}/{ns[j]}")
    rep.add("ALL-01", worst[0] <= 0.60, f"highest overlap {worst}", "all", worst[0])
    hooks = Counter(first_line(t).lower() for t in texts.values())
    rep.add("W-M06", (max(hooks.values()) if hooks else 0) < 3, "", "all")
    concrete_each = [n for n, t in texts.items() if not re.search(r"\d|\b[A-Z][a-z]+\b", t)]
    rep.add("ALL-02", not concrete_each, ", ".join(concrete_each), "all")

    # media on every network except X; counts and files
    no_media = [n for n in NETWORKS if n in nets and n != "x" and not nets[n].get("media") and not nets[n].get("pdf")]
    rep.add("V-A18", not no_media, "text only on: " + ", ".join(no_media) if no_media else "", "all")
    if "facebook" in nets:
        rep.add("FB-12", bool(nets["facebook"].get("media")), "", "facebook")
    if "x" in nets:
        rep.add("V-A13", len(nets["x"].get("media", [])) <= 4, "", "x", len(nets["x"].get("media", [])))
    if "threads" in nets:
        rep.add("V-A14", len(nets["threads"].get("media", [])) <= 10, "", "threads")
    if "instagram" in nets:
        k = len(nets["instagram"].get("media", []))
        rep.add("V-A15", k <= 20, "", "instagram", k)
        rep.add("V-F08", 5 <= k <= 10, "", "instagram", k)
    li = nets.get("linkedin", {})
    if li.get("pdf_pages") is not None:
        rep.add("V-F09", 8 <= int(li["pdf_pages"]) <= 12, "", "linkedin", li["pdf_pages"])
    if li.get("pdf_title") is not None:
        rep.add("V-A10", 0 < len(li["pdf_title"]) <= 60, "", "linkedin", len(li["pdf_title"]))
    alt_missing = [n for n, v in nets.items() if n != "linkedin" and v.get("media") and len(v.get("alt", [])) < len(v["media"])]
    rep.add("V-M01", not alt_missing, ", ".join(alt_missing), "all")
    long_alt = [a for v in nets.values() for a in v.get("alt", []) if len(a) > 250]
    rep.add("V-M03", not long_alt, "", "all")
    alt_banned = [w for v in nets.values() for a in v.get("alt", []) for w in BANNED_WORDS if re.search(r"\b" + w + r"\b", a.lower())]
    if alt_banned:
        rep.add("SC03", False, "banned word in alt text: " + ", ".join(alt_banned), "alt")

    check_files(rep, nets)
    check_slides(rep, slides, nets, kind)
    check_facts(rep, pkg, facts, all_text, slide_text)
    if not offline:
        check_links(rep, nets)

    # history-based post checks
    if history is not None:
        recent = [h for h in history if h.get("date", "") >= (dt.date.fromisoformat(pkg.get("date", dt.date.today().isoformat())) - dt.timedelta(days=30)).isoformat()]
        stats_now = {norm_num(n) for n in numbers_in(all_text)}
        reused = [n for h in recent for n in h.get("stats_used", []) if norm_num(n) in stats_now]
        rep.add("W-N05", not reused, ", ".join(sorted(set(reused))[:5]), "all")
        last10 = history[-10:]
        cq = pkg.get("closing_question_format")
        if cq:
            rep.add("W-N04", sum(1 for h in last10 if h.get("closing_question_format") == cq) < 3, "", "all")
        fmts = {h.get("format") for h in last10[-9:]} | {pkg.get("format")}
        rep.add("W-N08", len(fmts - {None}) >= 3 or len(history) < 6, "", "all", len(fmts - {None}))
        topic = pkg.get("topic")
        if topic:
            two_weeks = [h for h in history if h.get("topic") == topic and h.get("date", "") >= (dt.date.fromisoformat(pkg.get("date")) - dt.timedelta(days=14)).isoformat()]
            rep.add("W-N07", len(two_weeks) <= 1 or bool(pkg.get("new_development")), "", "all")
        sk = pkg.get("skeleton")
        if sk:
            rep.add("W-N03", all(h.get("skeleton") != sk for h in history[-5:]), "", "all")


def check_files(rep: Report, nets: dict):
    try:
        from PIL import Image  # noqa
    except Exception:
        Image = None
    dims = {}
    for net, v in nets.items():
        for m in v.get("media", []):
            if not os.path.exists(m):
                rep.add("V-A17", False, f"missing file {m}", net)
                continue
            size = os.path.getsize(m)
            if net in ("x", "gbp"):
                rep.add("V-A04", size <= 5_000_000, f"{os.path.basename(m)} {size // 1000} KB", net, size)
            if Image is None:
                continue
            with Image.open(m) as im:
                w, h = im.size
                fmt = im.format
                dims.setdefault(net, set()).add((w, h))
                if net == "gbp":
                    rep.add("GBP-03", fmt in ("JPEG", "PNG") and 10_000 <= size <= 5_000_000 and min(w, h) >= 720,
                            f"{fmt} {w}x{h} {size // 1000} KB", net)
                if net == "instagram":
                    rep.add("V-A07", w >= 1080, f"{w}px", net, w)
                if net in ("linkedin", "instagram", "threads", "facebook"):
                    rep.add("V-A01", (w, h) == (2160, 2700) or abs(w / h - 0.8) < 0.01 or abs(w / h - 0.75) < 0.01, f"{w}x{h}", net)
                light_checks(rep, im, net, os.path.basename(m))
    for net, ds in dims.items():
        rep.add("V-A02", len(ds) <= 1, f"{sorted(ds)}", net)
    if Image is None and any(v.get("media") for v in nets.values()):
        rep.add("V-D06", None, "Pillow not installed: run pip install pillow and re-run (this gate must run)", "slides")


# V-D06 calibration: the research's starting value was mean L* 85; the founder-approved Nov 4 Aurora deck measures
# 80-85, so the gate is set at 75 (still blocks any dark slide) and the 85 target stays a soft visual goal.
LIGHT_MEAN_MIN = 75


def srgb_to_lstar(r: float, g: float, b: float) -> float:
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    y = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return 116 * (y ** (1 / 3)) - 16 if y > 0.008856 else 903.3 * y


def light_checks(rep: Report, im, net: str, name: str):
    small = im.convert("RGB").resize((108, 135))
    px = list(small.get_flattened_data()) if hasattr(small, "get_flattened_data") else list(small.getdata())
    ls = [srgb_to_lstar(*p) for p in px]
    mean_l = sum(ls) / len(ls)
    dark = sum(1 for l in ls if l < 40) / len(ls)
    navy = sum(1 for (r, g, b), l in zip(px, ls) if l < 30) / len(ls)
    rep.add("V-D06", mean_l >= LIGHT_MEAN_MIN and dark <= 0.10, f"{name}: mean L* {mean_l:.0f}, dark area {dark:.0%}", net, round(mean_l, 1))
    rep.add("V-L01", navy <= 0.05, f"{name}: black or navy fill {navy:.0%}", net, round(navy, 3))


def check_slides(rep: Report, slides: list[dict], nets: dict, kind: str):
    if not slides:
        return
    roles = [s.get("role") for s in slides]
    rep.add("V-F01", {"hook", "cta"} <= set(roles) and len(set(roles)) >= 4, ", ".join(map(str, roles)), "slides")
    for i, s in enumerate(slides, 1):
        t = s.get("text", "")
        wc = len(t.split())
        where = f"slide {i}"
        if s.get("role") == "hook":
            rep.add("V-G02", wc <= 12, "", where, wc)
            rep.add("V-C23", len(first_line(t).split()) <= 10, "", where)
        elif s.get("role") == "cta":
            rep.add("V-G03", wc <= 15, "", where, wc)
        else:
            rep.add("V-G01", wc <= 30, "", where, wc)
        rep.add("V-C21", not EMOJI_RE.search(t), "", where)
        if s.get("example"):
            rep.add("V-H17", bool(re.search(r"\bExample\b", t)), "", where)
        if s.get("has_stat"):
            rep.add("V-G11", bool(s.get("source_line")), "", where)
        if s.get("logos"):
            rep.add("V-K05", False, "third-party logo on a slide: company names go in words (decision 1)", where)
        if s.get("genai_people"):
            rep.add("V-I19", False, "generative-AI person, hands, shop, receipt or screen", where)
        if s.get("invented_ui"):
            rep.add("V-E18", False, "invented app screen", where)
        for cid in ("W-A11", "W-B04", "W-B05", "W-C01"):
            pass
    deck = "\n".join(s.get("text", "") for s in slides)
    rep.add("V-G04", len(deck.split()) <= 180, "", "slides", len(deck.split()))
    fk, _ = readability(deck)
    rep.add("V-G06", fk <= 8, "", "slides", fk)
    rep.add("V-F03", bool(re.search(r"\$\d|\d%|\d", slides[0].get("text", ""))), "", "slide 1")
    last = slides[-1].get("text", "")
    rep.add("V-F10", "svpay.app" in last.lower(), "", f"slide {len(slides)}")
    rep.add("V-F21", "Payments, without the fee." in last, "", f"slide {len(slides)}")
    rep.add("V-F18", bool(re.search(r"(?i)barber|food truck|salon|restaurant|\bbar\b|retail|coffee|bakery|shop", deck)), "", "slides")
    if kind == "news" and len(slides) > 1:
        rep.add("V-F19", bool(re.search(r"\b(" + MONTHS + r")\b", slides[1].get("text", "") + slides[1].get("source_line", ""))), "", "slide 2")
        rep.add("V-G12", bool(re.search(r"\b(" + MONTHS + r")\s+20\d\d\b", deck + " ".join(s.get("source_line", "") for s in slides))), "", "slides")
    ig = nets.get("instagram", {}).get("text", "")
    if ig:
        rep.add("V-F23", first_line(slides[0].get("text", "")).lower() != first_line(ig).lower(), "", "slide 1")
    for i, s in enumerate(slides, 1):
        sub = Report()
        check_slide_words(sub, s.get("text", ""))
        bad = [r["id"] for r in sub.results if r["status"] == "fail"]
        rep.add("W-M08", not bad, ", ".join(bad), f"slide {i}")


def check_slide_words(rep: Report, t: str):
    low = t.lower()
    cluster = set()
    for cid, (lst, limit) in A_LISTS.items():
        hits = count_any(low, lst)
        rep.add(cid, len(hits) <= limit, ", ".join(hits), "slide")
    for cid, lst in B_LISTS.items():
        rep.add(cid, not count_any(low, lst, word_boundary=False), "", "slide")
    rep.add("W-C01", not re.search(r",\s+not\s+(just\s+)?[a-z]+[.!]|\bnot (just|only)\b", t, re.I), "", "slide")


def check_facts(rep: Report, pkg: dict, facts: list[dict], all_text: str, slide_text: str):
    # Every quote in the post must be in the facts log, found verbatim in a dated, linked source.
    quotes = []
    for line in all_text.split("\n"):
        if re.search(r"https?://|^\s*sources?:", line, re.I):
            continue  # article titles in source lines are not quotes
        quotes += re.findall(r"[“\"]([^”\"]{12,})[”\"]", line)
    logged = [f for f in facts if f.get("quote")]
    bad_q = []
    for q in quotes:
        hit = [f for f in logged if q.strip().lower() in f["quote"].lower() or f["quote"].lower() in q.strip().lower()]
        if not hit or not all(f.get("quote_found") and f.get("source_url") and f.get("source_date") for f in hit):
            bad_q.append(q[:60])
    rep.add("CR11", not bad_q, " | ".join(bad_q), "all")
    # Every figure in the text and on the slides traces to the facts log (as a source figure or a shown result).
    logged_nums = {norm_num(n) for f in facts for n in f.get("numbers", [])}
    for m in pkg.get("math", []):
        logged_nums.add(norm_num(str(m.get("shown", ""))))
    text_nums = {norm_num(n) for n in numbers_in(all_text)}
    bases = {norm_num(x) for x in ("$0", "0%", "$100", "100%", "100")}  # "per $100" and "100%" are bases, not claims
    logged_plain = {x.rstrip("%") for x in logged_nums}
    missing = sorted(n for n in text_nums if n not in logged_nums and n.rstrip("%") not in logged_plain and n not in bases)
    rep.add("PP12", not missing and all(f.get("source_url") or f.get("own_data") or f.get("example") for f in facts) and bool(facts),
            "untraced: " + ", ".join(missing[:8]) if missing else ("" if facts else "no facts log"), "all")
    slide_nums = {norm_num(n) for n in numbers_in(slide_text)}
    off = sorted(n for n in slide_nums if n not in text_nums and n not in logged_nums and n.rstrip("%") not in logged_plain and n not in bases)
    rep.add("CR41", not off, ", ".join(off[:6]), "slides")
    # Arithmetic recomputed.
    wrong = []
    for m in pkg.get("math", []):
        try:
            val = eval(m["expr"], {"__builtins__": {}}, {})  # numbers and + - * / ( ) only, written by the job
        except Exception:
            wrong.append(m.get("expr"))
            continue
        shown = float(re.sub(r"[^\d.]", "", str(m.get("shown", "nan"))) or "nan")
        if abs(val - shown) > max(0.01, abs(val) * 0.001):
            wrong.append(f"{m['expr']} = {val:g}, shown {m.get('shown')}")
    rep.add("CR09", not wrong, "; ".join(wrong), "all")
    # Law and policy status re-checked on publish day.
    laws = [f for f in facts if f.get("type") == "law_status"]
    today = pkg.get("date")
    stale = [f.get("text", "")[:50] for f in laws if f.get("checked_on") != today]
    if laws:
        rep.add("CR06", not stale, "status not re-checked today: " + "; ".join(stale), "all")
    elif re.search(r"(?i)\b(law|bill|court|ruling|lawsuit|injunction)\b", all_text):
        rep.add("CR06", False, "the post names a law, bill or court case but the facts log has no law_status entry", "all")
    # Freshness of the news.
    if pkg.get("kind") == "news":
        dates = [f.get("source_date") for f in facts if f.get("source_date")]
        if dates:
            newest = max(dates)
            age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(newest[:10])).days
            rep.add("ALL-13", age <= 2 or (age <= 14 and bool(pkg.get("why_now"))), f"newest source {newest}, {age} days", "all", age)
    # Zero-fee promise explains how SVPay makes money.
    if re.search(r"(?i)without (the|a|any) fees?|no (processing )?fees|zero fees|\$0 (to|in) (fees|cash out)|without it\b", all_text):
        rep.add("CR13", bool(re.search(r"(?i)how (svpay|we) make money|svpay makes money|plans? (start|cost)|paid plans?|svpay earns", all_text)),
                "a no-fee promise with no line on how SVPay makes money", "all")


def check_links(rep: Report, nets: dict):
    urls = set()
    for v in nets.values():
        urls |= set(URL_RE.findall(v.get("text", "") + "\n" + v.get("first_comment", "") + "\n" + v.get("reply", "")))
        urls |= {u for u in v.get("media_urls", [])}
        if v.get("cta_url"):
            urls.add(v["cta_url"])
    bad = []
    for u in sorted(urls):
        u = u.rstrip(".,)")
        try:
            req = urllib.request.Request(u, method="GET", headers={"User-Agent": "svpay-post-check/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status != 200:
                    bad.append(f"{u} {r.status}")
        except Exception as e:  # network errors count as failures: a link must open at publish time
            bad.append(f"{u} {type(e).__name__}")
    rep.add("SC12", not bad, "; ".join(bad[:5]), "all")


# ----------------------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--history")
    ap.add_argument("--private-terms")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    try:
        pkg = json.load(open(a.package, encoding="utf-8"))
    except Exception as e:
        print(f"package unreadable: {e}", file=sys.stderr)
        sys.exit(2)
    history = json.load(open(a.history, encoding="utf-8")) if a.history and os.path.exists(a.history) else None
    terms = [l.strip() for l in open(a.private_terms, encoding="utf-8")] if a.private_terms and os.path.exists(a.private_terms) else []
    rep = Report()
    check_package(pkg, rep, history, terms, a.offline)
    fails = [r for r in rep.results if r["status"] == "fail"]
    gates = rep.gates_failed()
    out = {"package": os.path.basename(a.package), "checks_run": len(rep.results),
           "distinct_criteria": len({r["id"] for r in rep.results}), "failed": len(fails),
           "gates_failed": [{"id": g["id"], "where": g["where"], "detail": g["detail"]} for g in gates],
           "results": rep.results}
    txt = json.dumps(out, indent=1, ensure_ascii=False)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt)
    print(f"checks run: {out['checks_run']} ({out['distinct_criteria']} criteria); failed: {out['failed']}; gates failed: {len(gates)}")
    for g in gates:
        print(f"  GATE {g['id']} [{g['where']}] {g['test'][:70]} :: {g['detail'][:90]}")
    soft = [r for r in fails if not r["gate"]]
    for r in sorted(soft, key=lambda r: -int(r["weight"] if str(r["weight"]).isdigit() else 1))[:25]:
        print(f"  fail {r['id']} (w{r['weight']}) [{r['where']}] {r['test'][:60]} :: {str(r['detail'])[:70]}")
    sys.exit(1 if gates else 0)


if __name__ == "__main__":
    main()
