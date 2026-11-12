"""Text on the images: copy rules, slide structure, number formats, sources, savings and disclosure wording.

Words come from the render manifest when the passes exist (what was actually set on the image), else from the
package's slide text. Text checks are exact string rules, so they need no passes; a gate here is never an estimate.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

from . import check
from .core import NEEDS, Outcome, estimate, na, ok

APPROVED = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "approved_lines.json"), encoding="utf-8"))
APPROVED_LINES = [v for k, v in APPROVED.items() if not k.startswith("_") and k != "rule"]
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
MONTH_NUM = {m: i for i, ms in enumerate([("January", "Jan"), ("February", "Feb"), ("March", "Mar"), ("April", "Apr"), ("May",), ("June", "Jun"), ("July", "Jul"),
                                          ("August", "Aug"), ("September", "Sep", "Sept"), ("October", "Oct"), ("November", "Nov"), ("December", "Dec")], 1) for m in ms}
COMPANIES = ["Toast", "Square", "Stripe", "Clover", "PayPal", "Shopify", "Heartland", "Riverside", "Fiserv", "Worldpay", "Visa", "Mastercard", "Amex",
             "American Express", "Discover", "Venmo", "Cash App", "Zelle", "Coinbase", "Bridge", "Privy", "Apple Pay", "Google Pay", "Lightspeed", "SpotOn"]
AGENCIES = ["FTC", "CFPB", "FDIC", "Federal Reserve", "SEC", "OCC", "attorney general", "Attorney General", "court", "Court", "government", "Government"]
PUBLIC_IMAGE_BANNED = [r"blockchain", r"on-?chain", r"crypto\w*", r"tokens?", r"usdc", r"stablecoins?", r"deposits?", r"withdrawals?", r"kyb",
                       r"bank(?:ing|\s+accounts?)?", r"(?<!card-issuing )(?<!issuing )banks", r"insured", r"fdic"]
NUM_RE = re.compile(r"(?<![\w.])[−\-–]?\$?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|¢|[KMB]\b|million|billion|thousand))?")


# ------------------------------------------------------------------ helpers
def img_text(post, sl):
    """The words on an image: the manifest's when the passes exist, else the package's slide text, else OCR."""
    if sl.manifest:
        return "\n".join(e["text"] for e in sl.elements if e.get("role") not in ("counter",))
    if sl.text:
        return sl.text
    return sl.ocr_all_text() or ""


def els(sl, *roles):
    return [e for e in sl.elements if not roles or e.get("role") in roles]


def headline(sl):
    """Slide headline text (manifest headline elements in reading order, else the slide text's first sentence)."""
    if sl.manifest:
        hs = sorted(els(sl, "headline"), key=lambda e: (e["box"]["y"], e["box"]["x"]))
        if hs:
            return " ".join(e["text"] for e in hs), True
    t = sl.text.strip()
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    return (m.group(1) if m else t), False


def words_of(s):
    return re.findall(r"[A-Za-z0-9$%][A-Za-z0-9$%,.'’\-]*", s)


def numbers(s):
    out = []
    for m in NUM_RE.finditer(s):
        tok = m.group(0).strip()
        if not re.search(r"\d", tok):
            continue
        out.append((tok, m.start(), m.end()))
    return out


def norm_value(tok):
    t = tok.replace("−", "-").replace("–", "-").replace(",", "").replace("$", "").replace(" ", "").lower()
    mult = 1.0
    for suf, m in (("billion", 1e9), ("million", 1e6), ("thousand", 1e3), ("b", 1e9), ("m", 1e6), ("k", 1e3)):
        if t.endswith(suf):
            t, mult = t[: -len(suf)], m
            break
    pct = t.endswith("%")
    t = t.rstrip("%¢")
    try:
        return float(t) * mult, pct
    except ValueError:
        return None, pct


def is_claim_number(s, tok, start, end):
    """Money, percents, and counts with a unit: the numbers a slide claims (not list markers, counters or years)."""
    if "$" in tok or "%" in tok or "¢" in tok or re.search(r"[KMB]$|million|billion", tok):
        return True
    v, _ = norm_value(tok)
    if v is None:
        return False
    before, after = s[max(0, start - 12):start], s[end:end + 24]
    if re.match(r"\s*/\s*\d", after) or re.search(r"\d\s*/\s*$", before):
        return False                                   # 2 / 6 counters, 24/7
    if 1990 <= v <= 2100 and float(v).is_integer():
        return False                                   # years
    if re.search(rf"\b({MONTHS})\.?\s*$", before):
        return False                                   # Nov 2
    if re.search(r"\bQ\s*$|Form\s*$|\d-\s*$", before) or re.match(r"-K\b", after):
        return False
    if re.match(r"\s*(months?|years?|days?|weeks?|pages?|shops?|locations?|stores?|merchants?|customers?|payments?|transactions?|businesses|restaurants?|people)\b|-page|-year|-month|-day", after):
        return True
    return v >= 1000


def claim_numbers(s):
    return [(t, a, b) for t, a, b in numbers(s) if is_claim_number(s, t, a, b)]


def logged_values(post):
    vals = set()
    for f in post.pkg.get("facts", []):
        for n in f.get("numbers", []):
            v, p = norm_value(str(n))
            if v is not None:
                vals.add((round(v, 6), p))
                vals.add((round(v, 6), not p))
    for m in post.pkg.get("math", []):
        v, p = norm_value(str(m.get("shown", "")))
        if v is not None:
            vals.add((round(v, 6), p))
            vals.add((round(v, 6), not p))
    return vals


def has_about(s, start):
    return bool(re.search(r"\b(about|around|roughly|nearly|almost|approximately|up to|more than|over|under|less than|at least)\s+$", s[max(0, start - 24):start], re.I))


def sig_figs(tok):
    digits = re.sub(r"[^\d.]", "", tok.split(".")[0] if not re.search(r"\.\d", tok) else tok)
    d = digits.replace(".", "").lstrip("0").rstrip("0") if "." not in digits else digits.replace(".", "").lstrip("0")
    return max(1, len(d))


def is_news(post):
    return post.pkg.get("kind") == "news"


def deck(post):
    return post.slides


def single_images(post):
    return [s for s in post.all_images() if s.idx == 0]


def is_example(sl):
    return bool(sl.spec.get("example")) or bool(re.search(r"\bExample\b", sl.text or ""))


SAVINGS_RE = re.compile(r"(?i)\b(save|saves|saving|savings|keep|keeps|kept|would keep|back in your pocket)\b[^.\n]{0,50}?(\$\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?%)|(\$\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?%)[^.\n]{0,30}?\b(saved|savings|in savings)\b")


def savings_claims(s):
    return [m for m in SAVINGS_RE.finditer(s)]


def footnotes(sl):
    if sl.manifest:
        return [e for e in sl.elements if e.get("role") in ("footnote", "source", "legal") or re.match(r"^[*†‡]|^(Example|Assumes|Figures are|Savings|Your savings)", e["text"])]
    return [{"text": l} for l in re.split(r"(?<=[.])\s+", sl.text or "") if re.match(r"^[*†‡]|^(Example|Assumes|Figures are|Savings|Your savings)", l)]


# ------------------------------------------------------------------ copy rules on every image (ruling 5)
@check("SC25", scope="image")
def banned_on_image(post, sl):
    t = img_text(post, sl) + "\n" + os.path.basename(sl.file)
    o = sl.ocr_all_text() if sl.manifest is None else None
    if o:
        t += "\n" + o
    hits = sorted({m.group(0) for p in PUBLIC_IMAGE_BANNED for m in re.finditer(r"(?i)\b" + p + r"\b", t)})
    return ok(not hits, "banned on a public image: " + ", ".join(hits) if hits else "")


@check("SC07", scope="image")
def never_holds_on_image(post, sl):
    t = img_text(post, sl)
    m = re.search(r"(?i)never holds? (your|their|any) money|non-?custodial|never touches your money", t)
    return ok(not m, m.group(0) if m else "")


@check("SC01", scope="image")
def cashback_on_image(post, sl):
    m = re.search(r"(?i)\bcash[\s-]back\b", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("SC02", scope="image")
def american_on_image(post, sl):
    brit = ["colour", "favour", "behaviour", "organise", "organisation", "centre", "cheque", "analyse", "realise", "recognise", "labour", "honour",
            "neighbour", "programme", "catalogue", "travelled", "cancelled", "modelling", "licence", "defence"]
    t = img_text(post, sl).lower()
    hits = [w for w in brit if re.search(r"\b" + w + r"s?\b", t)]
    return ok(not hits, ", ".join(hits))


# ------------------------------------------------------------------ Instagram carousel structure (F-IG)
HOOK_OPENERS = r"^(did you know|ever wondered|stop scrolling|here'?s why|you won'?t believe|here'?s what|this is why|let'?s talk)"


@check("F-IG02", scope="post")
def hook_words_target(post):
    if not post.slides:
        return na("no slides")
    h, measured = headline(post.slides[0])
    n = len(words_of(h))
    return ok(n <= 7, f"slide-1 headline {n} words: {h[:80]}", n)


@check("F-IG05", scope="post")
def slide1_blocks(post):
    if not post.slides:
        return na("no slides")
    sl = post.slides[0]
    if not sl.manifest:
        n = len([s for s in re.split(r"(?<=[.!?])\s+", sl.text) if s.strip()])
        return estimate(n <= 2, f"{n} sentences on slide 1", n)
    blocks = [e for e in sl.elements if e["role"] in ("body", "cta")]
    kick = [e for e in sl.elements if e["role"] == "label" and len(words_of(e["text"])) > 6]
    return ok(not blocks and not kick, "; ".join(e["text"][:50] for e in blocks + kick), len(blocks))


@check("F-IG06", scope="post")
def slide1_cta(post):
    if not post.slides:
        return na("no slides")
    sl = post.slides[0]
    t = "\n".join(e["text"] for e in sl.elements if e["role"] != "footer") if sl.manifest else re.sub(r"www\.svpay\.app\s*$", "", sl.text)
    m = re.search(r"(?i)https?://|www\.\S+|link in (the )?bio|\bfollow\b|\bdm us\b|\bdm me\b", t)
    return ok(not m, m.group(0) if m else "")


WORDNUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


@check("F-IG07", scope="post")
def count_promise(post):
    if not post.slides:
        return na("no slides")
    h = post.slides[0].text
    m = re.search(r"(?i)\b(\d+|two|three|four|five|six|seven|eight|nine|ten)\s+(?:\w+\s){0,2}?(fees|ways|questions|things|mistakes|steps|signs|tips|answers|rules|reasons|red flags|charges|terms)\b", h)
    if not m:
        return na("slide 1 promises no count")
    n = int(m.group(1)) if m.group(1).isdigit() else WORDNUM[m.group(1).lower()]
    items = sum(1 for s in post.slides if s.role == "item")
    if not items:
        items = max([len(re.findall(r"(?:^|\s)(\d)[.)]?\s+(?=[A-Z“\"])", s.text)) for s in post.slides] + [0])
    return ok(items == n, f"slide 1 promises {n} {m.group(2)}, the deck has {items}", items)


@check("F-IG09", scope="post")
def hook_openers(post):
    if not post.slides:
        return na("no slides")
    h, _ = headline(post.slides[0])
    m = re.search(HOOK_OPENERS, h.strip(), re.I) or re.search(HOOK_OPENERS, post.slides[0].text.strip(), re.I)
    return ok(not m, m.group(0) if m else "")


@check("F-IG10", scope="post")
def slide1_counter(post):
    if not post.slides:
        return na("no slides")
    sl = post.slides[0]
    if sl.manifest:
        c = [e["text"] for e in sl.elements if e["role"] == "counter"]
        return ok(not c, ", ".join(c))
    o = sl.ocr_all_text()
    if o is None:
        return na("needs OCR")
    m = re.search(r"\b1\s*/\s*\d+\b", o)
    return estimate(not m, m.group(0) if m else "")


@check("F-IG11", scope="post")
def hook_formula_repeat(post):
    prev = [h.get("slide1_headline") for h in post.history if h.get("slide1_headline")][-5:]
    if not post.slides:
        return na("no slides")
    if not prev:
        return na("history has no slide-1 headlines yet (history.json field slide1_headline)")
    h, _ = headline(post.slides[0])
    first = " ".join(w.lower() for w in words_of(h)[:3])
    clash = [p for p in prev if " ".join(w.lower() for w in words_of(p)[:3]) == first]
    return ok(not clash, " | ".join(clash[:2]))


SERIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "series.json")


@check("F-IG12", scope="post")
def kicker_series(post):
    if not post.slides or not post.slides[0].manifest:
        return na("needs the manifest (kicker role)")
    kick = [e["text"] for e in post.slides[0].elements if e["role"] == "label" and not re.search(r"(?i)results|case|news|\d", e["text"])]
    names = json.load(open(SERIES, encoding="utf-8")).get("series", []) if os.path.exists(SERIES) else []
    if not kick:
        return na("no kicker on slide 1")
    if not names:
        return na("no approved series names yet (checks/image/series.json)")
    bad = [k for k in kick if k.strip().lower() not in [n.lower() for n in names]]
    return ok(not bad, ", ".join(bad))


def headline_count(sl):
    hs = sorted(els(sl, "headline"), key=lambda e: (e["box"]["y"], e["box"]["x"]))
    n = 0
    prev = None
    for e in hs:
        if prev is None or re.search(r"[.!?:]\s*$", prev["text"]):
            n += 1
        prev = e
    return n


@check("F-IG13", scope="slide")
def one_headline(post, sl):
    if sl.idx in (1, len(post.slides)):
        return na("content slides only")
    if not sl.manifest:
        return na("needs the manifest (headline roles)")
    n = headline_count(sl)
    return ok(n == 1, f"{n} headlines", n)


@check("F-IG14", scope="slide")
def middle_headline_len(post, sl):
    if sl.idx in (1, len(post.slides)):
        return na("middle slides only")
    h, measured = headline(sl)
    n = len(words_of(h))
    return (ok if measured else estimate)(n <= 8, f"{n} words: {h[:70]}", n)


@check("F-IG15", scope="slide")
def dangling_headline(post, sl):
    h, _ = headline(sl)
    m = re.match(r"(?i)(this|that|it|these|those|and|but|so|also|here'?s why)\b", h.strip())
    return ok(not m, h[:60] if m else "")


@check("F-IG16", scope="slide")
def sentence_across_seam(post, sl):
    if sl.idx == len(post.slides):
        return na("last slide")
    t = re.sub(r"\s*(www\.svpay\.app|\d+\s*/\s*\d+)\s*$", "", sl.text.strip())
    body = re.sub(r"(?i)\s*Sources?:.*$", "", t).strip()
    bad = re.search(r"(…|\.\.\.|,|\b(and|or|but|so|because|which|that|the|a|an|to|of))$", body)
    return ok(not bad, body[-40:] if bad else "")


@check("F-IG22", scope="post")
def slide2_filler(post):
    if len(post.slides) < 2:
        return na("no slide 2")
    t = post.slides[1].text
    m = re.match(r"(?i)\s*(let'?s dive in|here'?s what you need to know|a quick guide|thread\b|let'?s break)", t)
    first12 = " ".join(words_of(t)[:12])
    fact = re.search(r"\d|\b[A-Z][a-z]+\b", first12[1:])
    return ok(not m and bool(fact), (m.group(0) if m else "") or ("no fact or number in the first 12 words" if not fact else ""))


def list_items(text):
    parts = re.split(r"(?:^|\s)(?=\d[.)]?\s+[A-Z“\"])", text)
    items = [p.strip() for p in parts[1:] if p.strip()]
    return [re.sub(r"^\d[.)]?\s+", "", i) for i in items]


@check("F-IG23", scope="slide")
def bullets(post, sl):
    items = list_items(sl.text)
    if not items:
        return na("no list on this slide")
    long = [i for i in items if len(words_of(i)) > 6]
    return ok(len(items) <= 4 and not long, f"{len(items)} items; {len(long)} longer than 6 words", len(items))


@check("F-IG25", scope="post")
def count_by_type(post):
    k = len(post.nets.get("instagram", {}).get("media", []))
    if not k:
        return na("no Instagram carousel")
    fmt = post.pkg.get("format") or post.pkg.get("kind")
    lo, hi = (7, 10) if fmt in ("data", "explainer") else (5, 7)
    return ok(lo <= k <= hi, f"{fmt}: {k} slides (target {lo}-{hi})", k)


@check("F-IG29", scope="post")
def summary_slide(post):
    if len(post.slides) < 3:
        return na("too few slides")
    sl = post.slides[-2]
    h, _ = headline(sl)
    items = list_items(sl.text)
    src = bool(sl.spec.get("source_line")) or bool(re.search(r"(?i)source", sl.text))
    good = len(words_of(h)) <= 6 and len(items) <= 5 and all(len(words_of(i)) <= 6 for i in items) and src
    return ok(good, f"title {len(words_of(h))} words, {len(items)} items, source {'yes' if src else 'no'}")


@check("F-IG30", scope="post")
def save_prompt(post):
    hits = [(s.idx, m.group(0)) for s in post.slides for m in re.finditer(r"(?i)\b(save|send|share) this[^.]*", s.text)]
    if not hits:
        return na("no save or send prompt")
    bad = [h for h in hits if h[0] == 1 or len(words_of(h[1])) > 8 or not re.search(r"(?i)\bto an? \w+|friend|owner|barber|manager", h[1])]
    return ok(len(hits) == 1 and not bad, "; ".join(f"slide {i}: {t}" for i, t in hits))


BAIT = [r"\blike if\b", r"\bcomment\s+['\"]?[A-Z]{2,}", r"\btag \d+ friends?\b", r"\btag (someone|a friend)\b", r"\bshare if you agree\b", r"\bfollow for part \d\b", r"\bdouble tap\b"]


@check("F-IG31", scope="image")
def bait_on_image(post, sl):
    t = img_text(post, sl)
    hits = [p for p in BAIT if re.search(p, t, re.I)]
    return ok(not hits, ", ".join(hits))


CTA_VERBS = r"(?i)^(see|try|find|check|get|ask|start|visit|calculate|run|book|download|sign up|join|compare|estimate|work out|do the math|grab)\b"


@check("F-IG32", scope="post")
def cta_largest(post):
    if not post.slides:
        return na("no slides")
    sl = post.slides[-1]
    if not sl.manifest:
        return na("needs the manifest (text sizes)")
    big = max(sl.elements, key=lambda e: e["size"]) if sl.elements else None
    return ok(big is not None and re.search(CTA_VERBS, big["text"].strip()), (big or {}).get("text", "")[:60])


@check("F-IG33", scope="image")
def printed_url(post, sl):
    urls = re.findall(r"(?i)(https?://\S+|www\.\S+|\b[a-z0-9-]+\.(?:app|com|org|gov)\S*)", img_text(post, sl))
    if not urls:
        return na("no printed destination")
    bad = [u for u in urls if len(u.rstrip(".")) > 20 or u.lower().startswith("http")]
    return ok(not bad, ", ".join(bad))


@check("F-IG34", scope="post")
def cta_new_numbers(post):
    if len(post.slides) < 2:
        return na("no CTA slide")
    earlier = {norm_value(t)[0] for s in post.slides[:-1] for t, a, b in claim_numbers(s.text)}
    new = [t for t, a, b in claim_numbers(post.slides[-1].text) if norm_value(t)[0] not in earlier]
    return ok(not new, ", ".join(new))


@check("F-SW03", scope="slide")
def swipe_words(post, sl):
    if sl.idx == 1:
        return na("slide 1")
    m = re.search(r"(?i)\bswipe\b|keep swiping|→|➡|»", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("F-SW04", scope="post")
def swipe_word_cue(post):
    if not post.slides:
        return na("no slides")
    sl = post.slides[0]
    if not re.search(r"(?i)\bswipe\b", img_text(post, sl)):
        return na("no word cue")
    if not sl.manifest:
        return na("needs the manifest (cue size)")
    cue = [e for e in sl.elements if re.search(r"(?i)\bswipe\b", e["text"])]
    biggest = max(e["size"] for e in sl.elements)
    good = all(len(words_of(e["text"])) <= 2 and e["size"] <= 120 and e["size"] < biggest and not re.search(r"→|➡|»", e["text"]) for e in cue)
    return ok(good, "; ".join(e["text"] for e in cue))


# ------------------------------------------------------------------ single images, X, GBP, LinkedIn documents
@check("F-NI04", scope="image")
def single_news_source(post, sl):
    if sl.idx != 0 or not is_news(post):
        return na("single news images only")
    t = img_text(post, sl)
    good = re.search(rf"\b({MONTHS})\b\.?\s*(\d{{1,2}},\s*)?20\d\d", t) and re.search(r"(?i)source|FTC|SEC|court|according to|reports?", t)
    return ok(bool(good), "" if good else "no outlet and Month YYYY on the image")


@check("F-NI05", scope="slide")
def claim_source_distance(post, sl):
    if not sl.spec.get("has_stat"):
        return na("no statistic")
    if not sl.manifest:
        return na("needs the manifest (positions)")
    src = [e for e in sl.elements if e["role"] in ("source",) or re.match(r"(?i)sources?:", e["text"])]
    claims = [e for e in sl.elements if e["role"] in ("headline", "body") and re.search(r"\d", e["text"])]
    if not src or not claims:
        return na("no source line or numeric claim element")
    def gap(c, s):
        cb, sb = c["box"], s["box"]
        return max(0, sb["y"] - (cb["y"] + cb["h"]), cb["y"] - (sb["y"] + sb["h"]))
    lh = lambda c: max(1, c["lines"][0]["h"])
    worst = max(min(gap(c, s) for s in src) / lh(c) for c in claims)
    return ok(worst <= 1.5, f"claim to source {worst:.1f} line-heights", round(worst, 2))


@check("F-NI06", scope="image")
def single_news_words(post, sl):
    if sl.idx != 0:
        return na("single images only")
    if sl.manifest:
        t = " ".join(e["text"] for e in sl.elements if e["role"] not in ("source", "footnote", "footer", "counter", "legal"))
        n = len(words_of(t))
        return ok(n <= 20, f"{n} words", n)
    o = sl.ocr_all_text()
    if o is None:
        return na("needs OCR")
    n = len(words_of(re.sub(r"(?is)source:.*", "", o)))
    return estimate(n <= 20, f"{n} words (OCR)", n)


@check("F-NI09", scope="image")
def breaking_tropes(post, sl):
    m = re.search(r"\b(BREAKING|JUST IN|ALERT)\b|🚨", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("F-GB06", scope="image")
def gbp_words(post, sl):
    if sl not in post.media("gbp"):
        return na("not a GBP image")
    if sl.manifest:
        n = len(words_of(" ".join(e["text"] for e in sl.elements)))
        return ok(n <= 12, f"{n} words", n)
    o = sl.ocr_all_text()
    return na("needs OCR") if o is None else estimate(len(words_of(o)) <= 12, f"{len(words_of(o))} words (OCR)")


@check("F-GB07", scope="image")
def gbp_contacts(post, sl):
    if sl not in post.media("gbp"):
        return na("not a GBP image")
    t = img_text(post, sl)
    if not sl.manifest:
        t += "\n" + (sl.ocr_all_text() or "")
    m = re.search(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}|[\w.+-]+@[\w-]+\.[\w.]+|(?<![\w/])@[A-Za-z0-9_]{3,}", t)
    return ok(not m, m.group(0) if m else "")


@check("F-GB08", scope="image")
def gbp_url(post, sl):
    if sl not in post.media("gbp"):
        return na("not a GBP image")
    m = re.search(r"(?i)www\.\S+|https?://\S+|\w+\.(app|com)\b", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("F-GB19", scope="image")
def gbp_promo(post, sl):
    if sl not in post.media("gbp"):
        return na("not a GBP image")
    m = re.search(r"(?i)\bfree\b|\d+% off|first month", img_text(post, sl))
    offer = post.nets.get("gbp", {}).get("post_type") == "offer" and post.nets["gbp"].get("start") and post.nets["gbp"].get("end")
    return ok(not m or bool(offer), m.group(0) if m else "")


def li_document(post):
    li = post.nets.get("linkedin", {})
    return bool(li.get("pdf") or li.get("pdf_title")) and len(li.get("media", [])) > 1


@check("F-LD02", scope="post")
def ld_cover_blocks(post):
    if not li_document(post) or not post.slides:
        return na("no LinkedIn document")
    h, _ = headline(post.slides[0])
    return ok(len(words_of(h)) <= 8, f"cover headline {len(words_of(h))} words")


@check("F-LD04", scope="post")
def ld_page2(post):
    if not li_document(post) or len(post.slides) < 2:
        return na("no LinkedIn document")
    t = post.slides[1].text
    bad = re.search(r"(?i)\b(agenda|contents|about me|in this guide|table of contents)\b", t)
    return ok(not bad and bool(re.search(r"\d", t)), bad.group(0) if bad else "")


@check("F-LD11", scope="slide")
def ld_fake_buttons(post, sl):
    if not li_document(post):
        return na("no LinkedIn document")
    m = re.search(r"(?i)\bclick here\b|\btap here\b", img_text(post, sl))
    under = [r for e in sl.elements for r in e.get("runs", []) if "underline" in (r.get("decoration") or "")] if sl.manifest else []
    return ok(not m and not under, (m.group(0) if m else "") + (" underlined text" if under else ""))


@check("F-LD13", scope="slide")
def ld_author_tag(post, sl):
    if not li_document(post):
        return na("no LinkedIn document")
    t = img_text(post, sl)
    return ok(bool(re.search(r"Derrik Savig", t)), "" if "Derrik Savig" in t else "no founder name on the page")


@check("F-LD15", scope="post")
def ld_cta_face(post):
    if not li_document(post) or not post.slides:
        return na("no LinkedIn document")
    return ok("Derrik Savig" in img_text(post, post.slides[-1]), "")


@check("F-LD16", scope="post")
def ld_cover_swipe(post):
    if not li_document(post) or not post.slides:
        return na("no LinkedIn document")
    m = re.search(r"(?i)\bswipe\b|→", img_text(post, post.slides[0]))
    return ok(not m, m.group(0) if m else "")


@check("F-LD17", scope="post")
def ld_title_vs_cover(post):
    li = post.nets.get("linkedin", {})
    if not li_document(post) or not li.get("pdf_title"):
        return na("no LinkedIn document title")
    h, _ = headline(post.slides[0])
    nouns = lambda s: {w.lower().strip(".,") for w in words_of(s) if len(w) > 3}
    shared = nouns(li["pdf_title"]) & nouns(h)
    return ok(bool(shared) and li["pdf_title"].strip() != h.strip(), f"shared: {', '.join(sorted(shared))}")


@check("F-LD10", scope="post")
def ld_share(post):
    if "linkedin" not in post.nets:
        return na("no LinkedIn post")
    followers = post.extra.get("linkedin_followers")
    if followers is not None and followers >= 5000:
        return na("profile has 5,000+ followers")
    since = (dt.date.fromisoformat(post.date) - dt.timedelta(days=28)).isoformat() if post.date else ""
    li = [h for h in post.history if h.get("network") == "linkedin" and h.get("date", "") >= since]
    if not li:
        return na("no LinkedIn history in the last 4 weeks")
    docs = sum(1 for h in li if h.get("format") in ("document", "pdf") or h.get("li_document")) + (1 if li_document(post) else 0)
    share = docs / (len(li) + 1)
    return ok(share <= 0.5, f"{docs} of {len(li) + 1} LinkedIn posts are documents", round(share, 2))


# ------------------------------------------------------------------ typography strings (T-K15, T-L13, T-L14, T-N)
@check("T-K15", scope="image")
def spacing_hygiene(post, sl):
    raw = [r["text"] for e in sl.elements for r in e.get("runs", [])] if sl.manifest else [sl.text]
    t = "\n".join(raw)
    bad = re.findall(r" {2,}| [?!:;%]|\t|[\u200b-\u200d\u2060\ufeff\u00ad]", t)
    return ok(not bad, repr(bad[:5]) if bad else "")


@check("T-L13", scope="post")
def headline_case(post):
    hs = [headline(s)[0] for s in post.slides]
    hs = [h for h in hs if len(words_of(h)) >= 3]
    if len(hs) < 2:
        return na("fewer than 2 headlines")
    def style(h):
        ws = [w for w in words_of(h) if w[0].isalpha() and len(w) > 3]
        caps = sum(1 for w in ws if w[0].isupper())
        return "title" if ws and caps / len(ws) > 0.7 else "sentence"
    styles = {style(h) for h in hs}
    return ok(len(styles) == 1, ", ".join(sorted(styles)))


@check("T-L14", scope="image")
def typographic_punct(post, sl):
    t = re.sub(r"https?://\S+|www\.\S+", "", img_text(post, sl))
    bad = []
    if re.search(r"(?<!\w)'|'(?!\w)|\w'\w|\"", t):
        bad.append("straight quote or apostrophe")
    if "--" in t:
        bad.append("double hyphen")
    if "..." in t:
        bad.append("three dots")
    if re.search(r"\d\s?-\s?\$?\d", t):
        bad.append("hyphen in a range")
    return ok(not bad, ", ".join(bad))


@check("T-N03", scope="image")
def dollar_string(post, sl):
    m = re.search(r"\$\s+\d", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("T-N04", scope="post")
def percent_string(post):
    t = "\n".join(img_text(post, s) for s in post.all_images())
    space = re.search(r"\d\s+%", t)
    mixed = "%" in t and re.search(r"\d\s*percent\b", t)
    return ok(not space and not mixed, (space.group(0) if space else "") + (" mixes % and percent" if mixed else ""))


@check("T-N05", scope="image")
def true_minus(post, sl):
    t = img_text(post, sl)
    hy = re.search(r"(?<![\w$])-\$?\d", t)
    neg = re.search(r"[−–]\$?\d", t)
    words = re.search(r"(?i)\b(loss|lost|down|less|lower|fell|drop)", t)
    return ok(not hy and (not neg or bool(words)), (hy.group(0) if hy else "") + (" negative value not stated in words" if neg and not words else ""))


@check("T-N12", scope="post")
def abbrev_style(post):
    t = "\n".join(s.text for s in post.slides)
    short = re.findall(r"\$?\d[\d.,]*\s?[KMB]\b", t)
    long_ = re.findall(r"\$?\d[\d.,]*\s(million|billion|thousand)\b", t)
    lower = re.findall(r"\$?\d[\d.,]*\s?[kmb]\b", t)
    spaced = [s for s in short if " " in s]
    return ok(not (short and long_) and not lower and not spaced, f"{len(short)} short, {len(long_)} spelled, {len(lower)} lowercase, {len(spaced)} spaced")


@check("T-N13", scope="post")
def ranges(post):
    t = "\n".join(img_text(post, s) for s in post.all_images())
    bad = re.findall(r"\$\d[\d,]*\s*[-–]\s*\d|\d\s?-\s?\$?\d+\s?%?|\d\s+–\s+\d", t)
    styles = {"to" if re.search(r"\d%\s+to\s+\d", t) else None, "dash" if re.search(r"\d%?–\d", t) else None} - {None}
    return ok(not bad and len(styles) <= 1, ", ".join(bad[:4]) + (" two range styles" if len(styles) > 1 else ""))


@check("T-N14", scope="image")
def times_sign(post, sl):
    m = re.search(r"\d\s?x\b|\bx\s?\d", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("T-N15", scope="image")
def leading_zero(post, sl):
    m = re.search(r"(?<![\d$])\.\d", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("T-N16", scope="post")
def cents(post):
    t = "\n".join(img_text(post, s) for s in post.all_images())
    one = re.findall(r"\$\d+\.\d\b(?!\d)", t)
    mixed = bool(re.search(r"\d¢", t)) and bool(re.search(r"\$0\.\d\d", t))
    return ok(not one and not mixed, ", ".join(one) + (" mixes ¢ and $0.xx" if mixed else ""))


# ------------------------------------------------------------------ number format and sources (MC-D, MC-E)
@check("MC-D01", scope="image")
def full_numbers(post, sl):
    t = img_text(post, sl)
    bad = re.findall(r"\$?\d[\d.,]*\s?[kKmMbB]\b", t)
    return ok(not bad, ", ".join(bad))


@check("MC-D02", scope="post")
def rate_precision(post):
    logged = {}
    for f in post.pkg.get("facts", []):
        for n in f.get("numbers", []):
            if str(n).strip().endswith("%"):
                v, _ = norm_value(str(n))
                logged.setdefault(round(v, 1), set()).add(str(n).strip())
    shown = [t for s in post.all_images() for t, a, b in numbers(img_text(post, s)) if t.endswith("%")]
    trunc = []
    for t in shown:
        v, _ = norm_value(t)
        same = logged.get(round(v, 1), set())
        if same and t not in same and all(len(t) < len(x) for x in same):
            trunc.append(t)
    return ok(not trunc, ", ".join(trunc))


@check("MC-D03", scope="image")
def about_symbols(post, sl):
    m = re.search(r"~\s?\$?\d|≈|\bapprox\.", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("MC-D04", scope="image")
def rounded_needs_about(post, sl):
    t = img_text(post, sl)
    logged = logged_values(post)
    bad = []
    for tok, a, b in claim_numbers(t):
        if not ("$" in tok or "%" in tok):
            continue
        v, p = norm_value(tok)
        if v is None or v < 10:
            continue
        if sig_figs(re.sub(r"[KMB]|million|billion", "", tok)) <= 2 and not has_about(t, a) and (round(v, 6), p) not in logged:
            bad.append(tok)
    return ok(not bad, ", ".join(bad))


@check("MC-D08", scope="image")
def periods_spelled(post, sl):
    m = re.search(r"/\s?(yr|mo|month|year|wk)\b", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("MC-D09", scope="image")
def no_numeric_dates(post, sl):
    m = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


def source_lines(sl):
    out = []
    if sl.spec.get("source_line"):
        out.append(sl.spec["source_line"])
    if sl.manifest:
        out += [e["text"] for e in sl.elements if e["role"] == "source" or re.match(r"(?i)sources?:", e["text"])]
    return list(dict.fromkeys(out))


@check("MC-E01", scope="slide")
def source_parts(post, sl):
    if not sl.spec.get("has_stat"):
        return na("no statistic")
    src = source_lines(sl)
    if not src:
        return ok(False, "no source line")
    s = " ".join(src)
    pub = re.search(r"\b[A-Z][A-Za-z&.]+", re.sub(r"(?i)^sources?:\s*", "", s))
    date = re.search(rf"\b({MONTHS})\b\.?\s*(\d{{1,2}},\s*)?20\d\d", s)
    title = re.search(r"(?i)results|report|complaint|release|filing|form|order|survey|study|page|article|press|data|\bv\.", s)
    missing = [n for n, x in (("publisher", pub), ("title", title), ("date", date)) if not x]
    return ok(not missing, "missing " + ", ".join(missing) if missing else "")


@check("MC-E03", scope="slide")
def court_source(post, sl):
    s = " ".join(source_lines(sl))
    if not re.search(r"\bv\.\s", s):
        return na("no court source")
    parts = {"court": re.search(r"(?i)\b(court|district|circuit|N\.D\.|S\.D\.|W\.D\.|E\.D\.)\b", s), "docket": re.search(r"(?i)\b(no\.|case|docket)\s*[\d:-]+|\d:\d{2}-cv-\d+", s),
             "date": re.search(rf"\b({MONTHS})\b", s)}
    missing = [k for k, v in parts.items() if not v]
    return ok(not missing, "missing " + ", ".join(missing) if missing else "")


RATE_RE = re.compile(r"(?i)\b(" + "|".join(COMPANIES) + r")(?:'s)?\s+(?:standard\s+)?(?:rate|charges|fee|pricing|takes)\b[^.\n]{0,30}?(\d+(?:\.\d+)?%(?:\s*\+\s*\$?\d+(?:\.\d+)?¢?)?)")


def competitor_rates(t):
    return [(m.group(1), m.group(2)) for m in RATE_RE.finditer(t)]


@check("MC-E04", scope="slide")
def competitor_rate_source(post, sl):
    rates = competitor_rates(img_text(post, sl))
    if not rates:
        return na("no named competitor rate")
    s = " ".join(source_lines(sl)) + " " + img_text(post, sl)
    bad = []
    for co, r in rates:
        m = re.search(rf"{co}\s+pricing page,\s+checked\s+({MONTHS})\.?\s+(\d{{1,2}}),\s+(20\d\d)", s)
        if not m:
            bad.append(f"{co}: no 'pricing page, checked <date>'")
            continue
        d = dt.date(int(m.group(3)), MONTH_NUM[m.group(1)], int(m.group(2)))
        if post.date and (dt.date.fromisoformat(post.date) - d).days > 30:
            bad.append(f"{co}: checked {d}, over 30 days")
    return ok(not bad, "; ".join(bad))


@check("MC-E05", scope="image")
def svpay_data_cite(post, sl):
    t = img_text(post, sl)
    if not re.search(r"(?i)\b(svpay data|our data|svpay shops)\b", t):
        return na("no SVPay data")
    return ok(bool(re.search(r"SVPay data,\s*[^,]+,\s*n\s*=\s*\d", t)), "")


@check("MC-E06", scope="image")
def unnamed_source(post, sl):
    t = " ".join(source_lines(sl)) + "\n" + img_text(post, sl)
    m = re.search(r"(?i)source:\s*(google|the internet|chatgpt|ai|experts?)\b|\b(studies show|experts say|research shows|according to (ai|chatgpt|google))\b", t)
    return ok(not m, m.group(0) if m else "")


@check("MC-E07", scope="slide")
def period_stated(post, sl):
    s = " ".join(source_lines(sl))
    if not sl.spec.get("has_stat") or not re.search(r"(?i)results|report|data|survey|study|earnings", s):
        return na("no reported data")
    t = img_text(post, sl) + " " + s
    per = re.search(rf"(?i)\b(Q[1-4]\s*20\d\d|20\d\d full year|full year|({MONTHS})\s+(to|through|–|-)\s*({MONTHS})|\bin \d+ months|first half|second half|year to date)\b", t)
    return ok(bool(per), "" if per else "no data period on the slide")


@check("MC-E08", scope="slide")
def old_data_tag(post, sl):
    if not sl.spec.get("has_stat") or not post.date:
        return na("no statistic")
    d0 = dt.date.fromisoformat(post.date)
    old = [f for f in post.pkg.get("facts", []) if f.get("source_date") and (d0 - dt.date.fromisoformat(f["source_date"][:10])).days > 730
           and any(str(n) in sl.text for n in f.get("numbers", []))]
    if not old:
        return na("no data older than 24 months")
    return ok(bool(re.search(r"\(?(19|20)\d\d data\)?", sl.text)), "")


@check("MC-E09", scope="slide")
def web_source_domain(post, sl):
    s = " ".join(source_lines(sl))
    if not s:
        return na("no source line")
    from urllib.parse import urlparse
    bad = []
    for f in post.pkg.get("facts", []):
        u = f.get("source_url") or ""
        host = urlparse(u).netloc.lower().replace("www.", "")
        if not host or host.endswith(".gov") or host.endswith("sec.gov") or "court" in host:
            continue
        name = host.split(".")[0]
        if re.search(rf"(?i)\b{re.escape(name)}\b", s) and host not in s.lower():
            bad.append(host)
    return ok(not bad, "no domain on the image for: " + ", ".join(sorted(set(bad))) if bad else "")


@check("MC-E10", scope="post")
def status_recheck(post):
    facts = [f for f in post.pkg.get("facts", []) if f.get("type") in ("law_status", "competitor_rate", "plan_price", "case_status")]
    if not facts:
        return na("no status-sensitive facts")
    stale = [f.get("text", "")[:40] for f in facts if not f.get("checked_on") or (post.date and (dt.date.fromisoformat(post.date) - dt.date.fromisoformat(f["checked_on"][:10])).days > 1)]
    return ok(not stale, "; ".join(stale))


# ------------------------------------------------------------------ examples (MC-F)
@check("MC-F02", scope="slide")
def example_inputs(post, sl):
    if not is_example(sl):
        return na("not an example")
    t = sl.text
    vol = re.search(r"\$\d[\d,]{3,}", t)
    rate = re.search(r"\d+(\.\d+)?%", t)
    per = re.search(r"(?i)\b(a|per|each|every)\s+(year|month|week|day)\b|\bannual|\bmonthly|\byearly", t)
    missing = [n for n, x in (("sales volume", vol), ("rate", rate), ("period", per)) if not x]
    return ok(not missing, "missing " + ", ".join(missing) if missing else "")


@check("MC-F06", scope="image")
def example_real_wording(post, sl):
    t = img_text(post, sl)
    if not (is_example(sl) or re.search(r"(?i)\bexample\b|\bsay a shop\b|\bimagine\b", t)):
        return na("not an example")
    m = re.search(r"(?i)case study|real results|this shop saved|actual customer|true story", t)
    return ok(not m, m.group(0) if m else "")


@check("MC-F07", scope="post")
def example_in_alt(post):
    ex = [s for s in post.slides if re.search(r"\bExample\b", s.text)]
    if not ex:
        return na("no slide says Example")
    bad = []
    for net, v in post.nets.items():
        files = v.get("media", [])
        alts = v.get("alt", [])
        for s in ex:
            for i, f in enumerate(files):
                if os.path.abspath(post.path(f)) == os.path.abspath(s.file) and (i >= len(alts) or "example" not in alts[i].lower()):
                    bad.append(f"{net} alt {i + 1}")
        if files and any(os.path.abspath(post.path(f)) in [os.path.abspath(s.file) for s in ex] for f in files) and "example" not in v.get("text", "").lower():
            bad.append(f"{net} caption")
    return ok(not bad, ", ".join(bad))


# ------------------------------------------------------------------ savings, disclosures, free claims (MC-H, MC-I, MC-K)
def every_sale_note(t):
    return re.search(r"(?i)(if|assum\w*|when)\b[^.]{0,40}\b(every|all|each)\b[^.]{0,30}\b(sale|payment|card sale|sales)\b[^.]{0,30}\b(paid|made|is|are)\b[^.]{0,20}\bSVPay", t)


@check("MC-H01", scope="image")
def upto_same_line(post, sl):
    t = img_text(post, sl)
    sv = savings_claims(t)
    if not sv:
        return na("no savings figure")
    if sl.manifest:
        bad = []
        for e in sl.elements:
            for ln in e["lines"]:
                for m in re.finditer(r"\$\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?%", ln["text"]):
                    if savings_claims(e["text"]) and not re.search(r"(?i)up to\s*$", ln["text"][:m.start()]):
                        bad.append(m.group(0))
        return ok(not bad, "no 'up to' on the line before: " + ", ".join(bad))
    return ok(all(re.search(r"(?i)up to\s*$", t[:m.start(2) if m.group(2) else m.start(3)]) for m in sv), "")


@check("MC-H05", scope="image")
def every_sale(post, sl):
    t = img_text(post, sl)
    if not re.search(r"(?i)\bup to\b", t) or not savings_claims(t):
        return na("no 'up to' savings figure")
    return ok(bool(every_sale_note(t)), "" if every_sale_note(t) else "no note that the figure assumes every sale is paid with SVPay")


@check("MC-H06", scope="image")
def plan_price(post, sl):
    t = img_text(post, sl)
    if not savings_claims(t):
        return na("no savings figure")
    return ok(bool(re.search(r"(?i)free Starter plan|minus the (plan|SVPay plan)|after the \$?\d+ plan|plan price", t)), "")


@check("MC-H07", scope="image")
def upto_footnote(post, sl):
    t = img_text(post, sl)
    if not re.search(r"(?i)\bup to\b", t) or not savings_claims(t):
        return na("no 'up to' savings figure")
    fn = " ".join(f["text"] for f in footnotes(sl)) or t
    parts = {"sales volume": re.search(r"\$\d[\d,]{3,}", fn), "rate and its date": re.search(rf"\d+(\.\d+)?%[^.]*({MONTHS})", fn),
             "plan": re.search(r"(?i)Starter plan|Growth|Pro plan|plan", fn), "wording": "Your savings depend on your sales and current rates." in fn}
    missing = [k for k, v in parts.items() if not v]
    return ok(not missing, "footnote missing " + ", ".join(missing) if missing else "")


@check("MC-H08", scope="image")
def guarantee_words(post, sl):
    m = re.search(r"(?i)you will save|you'?ll (save|keep) \$|guaranteed (savings|to save)|you save \$", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("MC-H11", scope="image")
def percent_base(post, sl):
    t = img_text(post, sl)
    bad = [m.group(0) for m in re.finditer(r"(?i)\bsave\s+(up to\s+)?\d+(\.\d+)?%(?!\s+(of|on))", t)]
    return ok(not bad, ", ".join(bad))


def qualified(t):
    return bool(re.search(r"(?i)\bup to\b|[*†‡]", t)) or bool(savings_claims(t)) or bool(re.search(r"\bExample\b", t))


@check("MC-I01", scope="image")
def footnote_same_image(post, sl):
    t = img_text(post, sl)
    if not qualified(t):
        return na("no qualified claim")
    return ok(bool(footnotes(sl)), "" if footnotes(sl) else "the qualifying note is not on this image")


@check("MC-I02", scope="image")
def footnote_near(post, sl):
    t = img_text(post, sl)
    if not qualified(t) or not sl.manifest:
        return na("no qualified claim" if not qualified(t) else "needs the manifest (positions)")
    fns = [f for f in footnotes(sl) if "box" in f]
    claims = [e for e in sl.elements if qualified(e["text"]) and e not in fns]
    if not fns or not claims:
        return na("no footnote or claim element")
    d = max(min(max(0, f["box"]["y"] - (c["box"]["y"] + c["box"]["h"])) for f in fns) for c in claims)
    return ok(d <= 600, f"{d:.0f}px below the claim", round(d))


@check("MC-I03", scope="image")
def markers_matched(post, sl):
    t = img_text(post, sl)
    marks = re.findall(r"[*†‡]", t)
    if not marks:
        return na("no reference marks")
    fn = " ".join(f["text"] for f in footnotes(sl))
    orphan = [m for m in set(marks) if not re.search(re.escape(m) + r"\s*\S", fn)]
    return ok(len(set(marks)) <= 2 and not orphan, f"{len(set(marks))} symbols; unmatched: {''.join(orphan)}")


@check("MC-I06", scope="image")
def footnote_plain(post, sl):
    fns = [f["text"] for f in footnotes(sl)]
    if not fns:
        return na("no footnote")
    m = re.search(r"(?i)\b(T&Cs apply|terms apply|restrictions apply|see terms|conditions apply)\b", " ".join(fns))
    return ok(not m, m.group(0) if m else "")


@check("MC-I07", scope="image")
def footnote_short(post, sl):
    fns = [f["text"] for f in footnotes(sl) if not re.match(r"(?i)sources?:", f["text"])]
    if not fns:
        return na("no footnote")
    n = max(len(words_of(f)) for f in fns)
    return ok(n <= 30, f"{n} words", n)


@check("MC-I08", scope="slide")
def own_disclosure(post, sl):
    t = img_text(post, sl)
    if not re.search(r"(?i)\bup to\b", t) and not savings_claims(t) and not (is_example(sl) and re.search(r"\$\d", t)):
        return na("no qualified number")
    return ok(bool(footnotes(sl)) or bool(re.search(r"\bExample\b", t)), "")


@check("MC-I10", scope="post")
def caption_qualifier(post):
    bad = []
    for net, v in post.nets.items():
        txt = v.get("text", "")
        for m in re.finditer(r"(?i)\bup to\s+\$?\d", txt):
            if m.start() > 125:
                bad.append(net)
        for m in savings_claims(txt):
            if not re.search(r"(?i)up to|example", txt[:125]):
                bad.append(net)
    return ok(not bad, ", ".join(sorted(set(bad)))) if bad or any(savings_claims(v.get("text", "")) for v in post.nets.values()) else na("no qualified number in captions")


@check("MC-I11", scope="image")
def self_contained(post, sl):
    if sl.idx != 0 and not any(sl in post.media(n) for n in ("x", "gbp", "threads")):
        return na("not posted alone")
    t = img_text(post, sl)
    if not qualified(t):
        return na("no qualified claim")
    return ok(bool(footnotes(sl)), "")


@check("MC-J01", scope="image")
def competitor_rate_matches(post, sl):
    rates = competitor_rates(img_text(post, sl))
    if not rates:
        return na("no named competitor rate")
    logged = {(f.get("company"), f.get("rate")) for f in post.pkg.get("facts", []) if f.get("type") == "competitor_rate"}
    bad = [f"{c} {r}" for c, r in rates if (c, r) not in logged]
    return ok(not bad, "not in the facts log as the company's published rate: " + ", ".join(bad))


@check("MC-J02", scope="image")
def rate_in_full(post, sl):
    t = img_text(post, sl)
    rates = competitor_rates(t)
    if not rates:
        return na("no named competitor rate")
    full = {f.get("company"): f.get("rate") for f in post.pkg.get("facts", []) if f.get("type") == "competitor_rate"}
    bad = [c for c, r in rates if full.get(c) and "+" in full[c] and "+" not in r and "percentage part only" not in t]
    return ok(not bad, ", ".join(bad))


@check("MC-J05", scope="slide")
def avg_fee_study(post, sl):
    if not re.search(r"(?i)\baverage\b[^.]{0,40}\b(card fee|processing|rate|swipe fee)", sl.text):
        return na("no average card-fee statistic")
    s = " ".join(source_lines(sl))
    yr = re.findall(r"20\d\d", s)
    fresh = yr and post.date and int(post.date[:4]) - max(map(int, yr)) <= 2
    return ok(bool(s) and bool(fresh), s[:60])


@check("MC-J09", scope="image")
def price_guarantee(post, sl):
    t = img_text(post, sl)
    if not re.search(r"(?i)beat any price|price guarantee|lowest price", t):
        return na("no price guarantee")
    return ok(bool(re.search(r"(?i)conditions?|if |when |applies to", t)), "")


@check("MC-J10", scope="post")
def rounding_direction(post):
    rows = [m for m in post.pkg.get("math", []) if m.get("kind") in ("competitor_fee", "savings")]
    if not rows:
        return na("no math rows marked competitor_fee or savings")
    bad = []
    for m in rows:
        try:
            val = eval(m["expr"], {"__builtins__": {}}, {})
        except Exception:
            continue
        shown, _ = norm_value(str(m.get("shown", "")))
        if shown is not None and shown > val + 1e-9:
            bad.append(f"{m['expr']} = {val:g}, shown {m['shown']}")
    return ok(not bad, "; ".join(bad))


FREE_RE = re.compile(r"(?i)\$0\b|\bfree\b|\bno (processing |transaction |cash-?out |card )?fees?\b|\b0% (processing )?fees?\b|without (the|a|any) fees?\b|\bzero fees?\b")


@check("MC-K01", scope="image")
def free_claims_approved(post, sl):
    t = img_text(post, sl)
    claims = [m.group(0) for m in FREE_RE.finditer(t)]
    if not claims:
        return na("no $0, free or no-fee claim")
    flat = re.sub(r"\s+", " ", t)
    if any(l in flat for l in APPROVED_LINES):
        return ok(True, "approved line used")
    tag = "Payments, without the fee."
    rest = flat.replace(tag, "")
    left = [m.group(0) for m in FREE_RE.finditer(rest)]
    return ok(not left, "not an approved line: " + ", ".join(left))


@check("MC-K02", scope="image")
def free_condition_adjacent(post, sl):
    t = img_text(post, sl)
    m = re.search(r"(?i)(\$0|\bfree\b|no fees?)[^.\n]{0,20}[*†]", t)
    if not FREE_RE.search(t):
        return na("no free claim")
    return ok(not m, m.group(0) if m else "")


@check("MC-K05", scope="image")
def signup_offer(post, sl):
    t = img_text(post, sl)
    if not re.search(r"(?i)\$5 back|\$20 on us|first (order|payment)", t):
        return na("no sign-up offer")
    good = ("$5 back on your first payment" in t) or ("first order up to $20 on us" in t)
    return ok(good, "offer wording is not the approved line")


@check("MC-K07", scope="image")
def months_free(post, sl):
    t = img_text(post, sl)
    m = re.search(r"(?i)(\d+|one|two|three) months? free", t)
    if not m:
        return na("no months-free claim")
    logged = any(f.get("type") == "plan_price" for f in post.pkg.get("facts", []))
    return ok(logged, "no plan_price entry in the facts log to check it against")


# ------------------------------------------------------------------ legal, marks, money wording (MC-L, MC-M, MC-N, MC-P)
@check("MC-L02", scope="image")
def quotes_verbatim(post, sl):
    t = img_text(post, sl)
    qs = re.findall(r"[“\"]([^”\"]{6,})[”\"]", t)
    if not qs:
        return na("no quotation marks")
    logged = [f["quote"].lower() for f in post.pkg.get("facts", []) if f.get("quote") and f.get("quote_found")]
    bad = [q[:40] for q in qs if not any(q.lower().strip(" .,") in l for l in logged)]
    return ok(not bad, "not verbatim from a logged source: " + " | ".join(bad))


@check("MC-L08", scope="image")
def bps_dates(post, sl):
    t = img_text(post, sl)
    if not re.search(r"(?i)basis points?|\bbps\b|percentage points?", t):
        return na("no basis or percentage points")
    return ok(bool(re.search(rf"\b({MONTHS})\b|\b20\d\d\b", t)), "")


@check("MC-M04", scope="image")
def sponsorship(post, sl):
    t = img_text(post, sl)
    m = re.search(r"(?i)\b(partnered with|in partnership with|approved by|official (partner|sponsor)|works with (Visa|Mastercard|Amex))\b", t)
    logged = post.pkg.get("agreements", [])
    return ok(not m or bool(logged), m.group(0) if m else "")


@check("MC-M07", scope="slide")
def not_affiliated(post, sl):
    h, _ = headline(sl)
    cos = [c for c in COMPANIES if re.search(rf"\b{re.escape(c)}\b", h) and c not in ("Visa", "Mastercard")]
    if not cos:
        return na("no company in the headline")
    t = img_text(post, sl)
    miss = [c for c in cos if f"SVPay is not affiliated with {c}" not in t and not re.search(rf"SVPay is not affiliated with {c}\w*", t)]
    if miss:
        return ok(False, "no 'SVPay is not affiliated with' line for " + ", ".join(miss))
    if sl.manifest:
        small = [e for e in sl.elements if "not affiliated" in e["text"] and e["size"] < 61 * sl.scale]
        return ok(not small, f"set at {small[0]['size']:.0f}px" if small else "")
    return estimate(True, "size not measured")


@check("MC-N03", scope="image")
def agency_endorser(post, sl):
    m = re.search(r"(?i)\b(FTC|CFPB|FDIC|SEC|government|court)[- ](approved|compliant|backed|endorsed|certified)\b|\bapproved by the (FTC|CFPB|government)\b|\bFTC says (switch|use)\b", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("MC-N04", scope="image")
def gov_domains(post, sl):
    t = img_text(post, sl)
    doms = re.findall(r"\b[\w.-]+\.gov\b", t)
    if not doms:
        return na("no .gov domain")
    cited = {re.sub(r"^www\.", "", __import__("urllib.parse").parse.urlparse(f.get("source_url", "")).netloc) for f in post.pkg.get("facts", [])}
    bad = [d for d in doms if d.replace("www.", "") not in cited]
    return ok(not bad, ", ".join(bad))


@check("MC-P04", scope="image")
def price_stability(post, sl):
    m = re.search(r"(?i)always worth \$1|never loses value|guaranteed \$1|always \$1", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("MC-P06", scope="image")
def balances(post, sl):
    m = re.search(r"(?i)\b(your|account|wallet) balance\b|\bkeep your money (in|with) SVPay\b|SVPay (keeps|holds|stores) (your )?money|\bwallet\b", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("MC-P07", scope="image")
def licensing(post, sl):
    m = re.search(r"(?i)\bSVPay (is )?(fully |federally |state[- ])?(licensed|regulated|compliant|approved|audited)\b", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("MC-P11", scope="image")
def plan_price_full(post, sl):
    t = img_text(post, sl)
    m = re.search(r"(?i)\$\d+(\.\d\d)?\s*(/|a|per)\s*(month|mo|year|yr)\b[^.]{0,30}\b(plan|Growth|Pro|Starter)|\b(Growth|Pro) (plan )?(is|costs)\s+\$\d", t)
    if not m:
        return na("no plan price")
    logged = any(f.get("type") == "plan_price" for f in post.pkg.get("facts", []))
    return ok(logged, m.group(0) + ("" if logged else ": no plan_price entry in the facts log"))


# ------------------------------------------------------------------ reviews and AI provenance (MC-Q, MC-R)
def testimonials(post, sl):
    t = img_text(post, sl)
    return re.findall(r"[“\"][^”\"]{8,}[”\"]\s*[—–-]\s*[A-Z][a-z]+", t)


@check("MC-Q01", scope="image")
def testimonial_records(post, sl):
    q = testimonials(post, sl)
    if not q:
        return na("no testimonial")
    recs = post.pkg.get("testimonials", [])
    return ok(bool(recs) and all(r.get("consent_id") and r.get("source") for r in recs), f"{len(q)} testimonial(s), {len(recs)} records")


@check("MC-Q02", scope="image")
def results_testimonial(post, sl):
    q = [x for x in testimonials(post, sl) if re.search(r"(?i)saved|kept|\$\d", x)]
    if not q:
        return na("no results testimonial")
    return ok(bool(re.search(r"(?i)typical|most shops|median|results vary", img_text(post, sl))), "")


@check("MC-Q03", scope="image")
def material_connection(post, sl):
    recs = [r for r in post.pkg.get("testimonials", []) if r.get("connection")]
    if not recs:
        return na("no logged material connection")
    t = img_text(post, sl)
    return ok(all(r["connection"] in t for r in recs), "")


@check("MC-Q04", scope="image")
def insiders_as_customers(post, sl):
    q = testimonials(post, sl)
    if not q:
        return na("no testimonial")
    insiders = post.extra.get("insiders", ["Derrik", "Rob", "Robert Graves", "Andy"])
    bad = [x for x in q if any(i in x for i in insiders) and not re.search(r"(?i)founder|friend|advisor|staff|relationship", img_text(post, sl))]
    return ok(not bad, " | ".join(bad))


@check("MC-Q05", scope="image")
def star_ratings(post, sl):
    t = img_text(post, sl)
    if not re.search(r"★|\b\d\.\d\s*(stars?|from)\b", t):
        return na("no star rating")
    return ok(bool(re.search(rf"\d\.\d from \d+ [A-Z]\w+ reviews?,\s*({MONTHS})", t)), "")


@check("MC-Q08", scope="image")
def social_proof(post, sl):
    t = img_text(post, sl)
    hits = re.findall(r"(?i)\b(\d[\d,]*\+?)\s+(shops|businesses|merchants|payments|users|customers|followers|downloads)\b", t)
    if not hits:
        return na("no social-proof number")
    own = {norm_value(str(n))[0] for f in post.pkg.get("facts", []) if f.get("own_data") for n in f.get("numbers", [])}
    bad = [h[0] for h in hits if norm_value(h[0].rstrip("+"))[0] not in own]
    return ok(not bad, "not in the facts log as SVPay's own data: " + ", ".join(bad))


@check("MC-Q09", scope="image")
def invented_customers(post, sl):
    q = testimonials(post, sl)
    if not q:
        return na("no testimonial")
    recs = post.pkg.get("testimonials", [])
    return ok(bool(recs) and not any(r.get("generated") or r.get("composite") for r in recs), "")


def layers(post):
    return post.pkg.get("layers") or post.extra.get("layers") or []


@check("MC-R02", scope="post")
def genai_labels(post):
    ai = [l for l in layers(post) if l.get("gen_ai")]
    if not ai:
        return na("no generative-AI layer logged")
    return ok(all(post.pkg.get("ai_label", {}).get(n) for n in ("meta", "linkedin") if n in post.nets), "")


@check("MC-R04", scope="post")
def genai_never_numbers(post):
    ls = layers(post)
    if not ls:
        return na("no provenance log (layers) in the package")
    bad = [l.get("name") for l in ls if l.get("gen_ai") and l.get("kind") in ("number", "chart", "numeral", "document", "logo", "court", "agency")]
    return ok(not bad, ", ".join(bad))


@check("MC-R05", scope="post")
def genai_photoreal_note(post):
    ai = [l for l in layers(post) if l.get("gen_ai") and l.get("photoreal")]
    if not ai:
        return na("no photorealistic generative-AI layer")
    return ok(any("AI-generated image" in s.text for s in post.slides), "")


@check("MC-R07", scope="post")
def genai_textures_clean(post):
    ai = [l for l in layers(post) if l.get("gen_ai") and l.get("file")]
    if not ai:
        return na("no generative-AI texture or background logged")
    from .core import load_rgb, ocr
    bad = []
    for l in ai:
        words = ocr(load_rgb(post.path(l["file"])), 11)
        if words and any(w["conf"] > 60 and len(w["text"]) > 2 for w in words):
            bad.append(l["file"])
    return ok(not bad, ", ".join(bad))


# ------------------------------------------------------------------ number scripts (MC-S)
@check("MC-S01", scope="image")
def numbers_traced(post, sl):
    logged = logged_values(post)
    t = img_text(post, sl)
    toks = [x[0] for x in claim_numbers(t)]
    sc = sl.scene
    if sc:
        txt = "".join(o.get("text", "") for o in sc.get("objects", []) if o.get("role") in ("numeral", "glyph"))
        toks += [x[0] for x in claim_numbers(txt)]
    bases = {(100.0, False), (100.0, True), (0.0, False), (0.0, True)}
    bad = []
    for tok in toks:
        v, p = norm_value(tok)
        if v is None or (round(v, 6), p) in bases:
            continue
        if (round(v, 6), p) not in logged and (round(v, 6), not p) not in logged:
            bad.append(tok)
    if not post.pkg.get("facts"):
        return ok(not toks, "no facts log")
    return ok(not bad, "not in the facts log: " + ", ".join(sorted(set(bad))))


@check("MC-S03", scope="image")
def points_wording(post, sl):
    t = img_text(post, sl)
    bad = []
    for m in re.finditer(r"(?i)(\d+(?:\.\d+)?)%\s+to\s+(\d+(?:\.\d+)?)%[^.]{0,40}?\b(up|down|rise|fall|increase|decrease|drop|cut)\w*\s+(?:of\s+|by\s+)?(\d+(?:\.\d+)?)\s*(%|percent|percentage points?|points?)", t):
        a, b, n, unit = float(m.group(1)), float(m.group(2)), float(m.group(4)), m.group(5).lower()
        if abs(abs(b - a) - n) < 1e-6 and unit in ("%", "percent"):
            bad.append(m.group(0))
    return ok(not bad, " | ".join(bad)) if bad else na("no rate change stated")


@check("MC-S04", scope="image")
def bps_convert(post, sl):
    t = img_text(post, sl)
    m = re.search(r"(?i)(\d+(?:\.\d+)?)%[^.]{0,30}\b(\d+)\s*(bps|basis points)[^.]{0,30}?(\d+(?:\.\d+)?)%", t)
    if not m:
        return na("no basis-point conversion")
    a, bp, c = float(m.group(1)), float(m.group(2)), float(m.group(4))
    return ok(abs(abs(a - c) - bp / 100) < 1e-6, m.group(0))


@check("MC-S05", scope="post")
def same_quantity(post):
    shown = {}
    for s in post.all_images():
        t = img_text(post, s)
        for tok, a, b in claim_numbers(t):
            v, p = norm_value(tok)
            if v and ("$" in tok or p):
                shown[(round(v, 4), p)] = tok
    others = []
    for net, v in post.nets.items():
        others.append((f"{net} text", v.get("text", "") + "\n" + v.get("first_comment", "") + "\n" + v.get("reply", "")))
        others += [(f"{net} alt {i + 1}", a) for i, a in enumerate(v.get("alt", []))]
        if v.get("pdf_title"):
            others.append((f"{net} pdf title", v["pdf_title"]))
    bad = []
    for where, txt in others:
        for tok, a, b in claim_numbers(txt):
            v, p = norm_value(tok)
            if not v or not ("$" in tok or p) or (round(v, 4), p) in shown:
                continue
            near = [(k, t) for k, t in shown.items() if k[1] == p and k[0] and 0 < abs(k[0] - v) / k[0] <= 0.05]
            for (sv, _), stok in near:
                rounded = has_about(txt, a) and abs(sv - v) / sv <= 0.01
                if not rounded:
                    bad.append(f"{where}: {tok} vs slide {stok}")
    return ok(not bad, "; ".join(sorted(set(bad))[:6]))


@check("MC-S06", scope="image")
def period_math(post, sl):
    t = img_text(post, sl)
    m = re.search(r"\$(\d[\d,]*(?:\.\d+)?)\s+(?:a|per)\s+month\s+for\s+(\d+)\s+(years?|months?)", t)
    if not m:
        return na("no monthly amount with a term")
    per, n = float(m.group(1).replace(",", "")), int(m.group(2))
    months = n * 12 if m.group(3).startswith("year") else n
    total = per * months
    others = [(tok, a) for tok, a, b in claim_numbers(t) if "$" in tok]
    cands = [(norm_value(tok)[0], tok, a) for tok, a in others if norm_value(tok)[0] and norm_value(tok)[0] > per * 2]
    if not cands:
        return na("no total shown")
    bad = [tok for v, tok, a in cands if abs(v - total) > 0.5 and not (has_about(t, a) and abs(v - total) / total <= 0.01) and abs(v - total) / total < 0.2]
    return ok(not bad, f"${per:g} x {months} = ${total:,.0f}; shown {', '.join(bad)}" if bad else f"${per:g} x {months} = ${total:,.0f}")


@check("MC-S07", scope="post")
def parts_sum(post):
    rows = [m for m in post.pkg.get("math", []) if re.fullmatch(r"[\d.\s+\-]+", m.get("expr", "")) and "-" in m["expr"]]
    charts = [c for c in post.pkg.get("charts", []) if c.get("parts") and c.get("total") is not None]
    if not rows and not charts:
        return na("no parts and total")
    bad = []
    for c in charts:
        if abs(sum(c["parts"]) - c["total"]) > c.get("tolerance", 1):
            bad.append(c.get("title", "chart"))
    return ok(not bad, ", ".join(bad))


@check("MC-S08", scope="image")
def times_more(post, sl):
    m = re.search(r"(?i)\b\d+(\.\d+)?\s*(x|times)\s+more\b|\b\d+(\.\d+)?%\s+more than\b", img_text(post, sl))
    return ok(not m, m.group(0) if m else "")


@check("MC-S10", scope="image")
def dates_valid(post, sl):
    t = img_text(post, sl)
    bad = []
    for m in re.finditer(rf"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+({MONTHS})\.?\s+(\d{{1,2}}),\s+(20\d\d)", t):
        d = dt.date(int(m.group(4)), MONTH_NUM[m.group(2)], int(m.group(3)))
        if d.strftime("%A") != m.group(1):
            bad.append(m.group(0))
    if post.date:
        d0 = dt.date.fromisoformat(post.date)
        for m in re.finditer(rf"\b(filed|ruled|sued|approved|settled|signed|passed)\b[^.]{{0,30}}\b({MONTHS})\.?\s+(\d{{1,2}}),\s+(20\d\d)", t):
            if dt.date(int(m.group(5)), MONTH_NUM[m.group(2)], int(m.group(3))) > d0:
                bad.append(m.group(0))
    return ok(not bad, " | ".join(bad))
