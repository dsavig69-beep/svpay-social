"""Text-on-image rules (checks/image/words.py and other package-level checks) on small synthetic posts.

Each row: check id, a post that passes, a post that fails. A post is built from slide texts (and optional extras); the
slide images are tiny blanks, so these checks read the slide text (no passes, no OCR needed).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import check_post  # noqa: E402
import image  # noqa: E402
from PIL import Image  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="svpay-rules-")
N = [0]


def post(texts, date="2026-11-12", kind="news", facts=None, nets=None, slide_extra=None, **top):
    N[0] += 1
    d = os.path.join(TMP, f"r{N[0]}")
    os.makedirs(d)
    slides = []
    for i, t in enumerate(texts, 1):
        f = os.path.join(d, f"slide-{i}.jpg")
        Image.new("RGB", (216, 270), (246, 247, 252)).save(f, quality=90)
        s = {"file": f, "role": "hook" if i == 1 else ("cta" if i == len(texts) else "proof"), "text": t}
        if slide_extra and i in slide_extra:
            s.update(slide_extra[i])
        slides.append(s)
    media = [s["file"] for s in slides]
    pkg = {"date": date, "kind": kind, "format": top.pop("format", "news"), "facts": facts if facts is not None else
           [{"text": "x", "numbers": ["$2,250", "$47", "60%", "2.6%", "$500,000", "$13,000", "4 years", "48 months"], "source_url": "https://www.ftc.gov/a", "source_date": "2026-11-02"}],
           "math": [], "slides": slides, "networks": nets if nets is not None else {"instagram": {"text": "Caption.", "media": media, "alt": [f"alt {i}" for i in range(len(media))]}}}
    pkg.update(top)
    return pkg


def run(pkg, cid):
    rep = check_post.Report()
    image.run(rep, pkg, base_dir=REPO, extra={}, only={cid})
    rows = [r for r in rep.results if r["id"] == cid]
    if any(r["status"] == "fail" for r in rows):
        return "fail"
    if any(r["status"] == "pass" for r in rows):
        return "pass"
    return "na"


H = "Shops paid about $2,250."
T2 = "The FTC sued Riverside on Nov 2, 2026."
CTA = "See what your shop would keep. www.svpay.app"
UP = "Keep up to $13,000 a year, if every sale is paid with SVPay, on the free Starter plan. *Example: $500,000 in card sales at 2.6% (Square pricing, Nov 2026) on the free Starter plan. Your savings depend on your sales and current rates."

ROWS = [
    # copy rules on images (ruling 5)
    ("SC25", post([H, CTA]), post(["Get paid in USDC, no bank needed.", CTA])),
    ("SC25", post(["$1.24B went to card-issuing banks.", CTA]), post(["SVPay is like a bank account.", CTA])),
    ("SC07", post([H, CTA]), post(["SVPay never holds your money.", CTA])),
    ("SC01", post(["Customers get cashback.", CTA]), post(["Customers get cash back.", CTA])),
    ("SC02", post(["Pick a color.", CTA]), post(["Pick a colour.", CTA])),
    # slide structure
    ("F-IG02", post(["Shops paid $2,250 to lease.", CTA]), post(["One shop paid about $2,250 to lease a machine.", CTA])),
    ("F-IG06", post([H, CTA]), post([H + " Follow us for more.", CTA])),
    ("F-IG07", post(["4 fees to ask about.", "1 Rate 2 Cancel 3 Lease 4 Renewal", CTA]), post(["5 fees to ask about.", "1 Rate 2 Cancel 3 Lease", CTA])),
    ("F-IG09", post([H, CTA]), post(["Did you know shops pay $2,250?", CTA])),
    ("F-IG14", post([H, "The pitch said 60% off.", CTA]), post([H, "The pitch said you would get 60% off every single card fee you pay.", CTA])),
    ("F-IG15", post([H, "Riverside's pitch: 60% off.", CTA]), post([H, "This is why it matters.", CTA])),
    ("F-IG16", post([H, "The pitch said 60% off.", CTA]), post([H, "The pitch said 60% off and", CTA])),
    ("F-IG22", post([H, "The FTC sued on Nov 2.", CTA]), post([H, "Let's dive in to the details here today.", CTA])),
    ("F-IG23", post([H, "Ask: 1 Every fee 2 Cancel terms 3 Lease 4 Renewal", CTA]), post([H, "Ask: 1 Every fee 2 Cancel 3 Lease 4 Renewal 5 Term", CTA])),
    ("F-IG25", post([H, T2, "x", "y", CTA], format="news"), post([H, T2, "x", "y", "z", "w", "v", "u", CTA], format="news")),
    ("F-IG29", post([H, "Ask these 3. 1 Rate 2 Fees 3 Term Source: FTC", CTA], slide_extra={2: {"source_line": "Source: FTC"}}),
     post([H, "The questions every shop should ask before it signs anything at all. 1 Rate 2 Fees", CTA])),
    ("F-IG31", post([H, CTA]), post([H, "Like if you agree.", CTA])),
    ("F-IG33", post([H, CTA]), post([H, "See more at www.svpay.app/savings-calculator"])),
    ("F-IG34", post([H, "See what $2,250 buys. www.svpay.app"]), post([H, "Save $9,999 now. www.svpay.app"])),
    ("F-SW03", post([H, T2, CTA]), post([H, "Keep swiping for more.", CTA])),
    ("F-NI09", post([H, CTA]), post(["BREAKING: Shops paid $2,250.", CTA])),
    ("T-K15", post([H, CTA]), post(["Shops  paid about $2,250 .", CTA])),
    ("T-L13", post(["Shops paid about $2,250.", "The pitch was 60% off.", CTA]), post(["Shops paid about $2,250.", "The Pitch Was Sixty Percent Off.", CTA])),
    ("T-L14", post(["The FTC’s case.", CTA]), post(["The FTC's case...", CTA])),
    ("T-N03", post([H, CTA]), post(["Shops paid $ 2,250.", CTA])),
    ("T-N04", post(["60% off.", CTA]), post(["60 % off.", CTA])),
    ("T-N05", post(["A loss of −$450.", CTA]), post(["Shops got -$450.", CTA])),
    ("T-N12", post(["$1.62B and $1.24B.", CTA]), post(["$1.62B and $1.24 billion.", CTA])),
    ("T-N13", post(["$9,000–$13,000.", CTA]), post(["$9,000 - 13,000.", CTA])),
    ("T-N14", post(["3× the price.", CTA]), post(["3x the price.", CTA])),
    ("T-N15", post(["0.5% more.", CTA]), post([".5% more.", CTA])),
    ("T-N16", post(["$2.50 a swipe.", CTA]), post(["$2.5 a swipe.", CTA])),
    # number format and sources
    ("MC-D01", post(["$13,000 a year.", CTA]), post(["$13K a year.", CTA])),
    ("MC-D03", post(["About $2,250.", CTA]), post(["~$2,250.", CTA])),
    ("MC-D04", post(["About $2,300 in fees.", CTA], facts=[]), post(["Shops paid $2,300 in fees.", CTA], facts=[])),
    ("MC-D08", post(["$47 a month.", CTA]), post(["$47/mo.", CTA])),
    ("MC-D09", post(["Nov 10, 2026.", CTA]), post(["11/10/26.", CTA])),
    ("MC-E01", post([H, CTA], slide_extra={1: {"has_stat": True, "source_line": "Source: FTC complaint, Nov 2, 2026"}}),
     post([H, CTA], slide_extra={1: {"has_stat": True, "source_line": "Source: FTC"}})),
    ("MC-E03", post([H, CTA], slide_extra={1: {"has_stat": True, "source_line": "Source: FTC v. Riverside, W.D. Wash. No. 2:26-cv-1234, Nov 2, 2026"}}),
     post([H, CTA], slide_extra={1: {"has_stat": True, "source_line": "Source: FTC v. Riverside, Nov 2026"}})),
    ("MC-E06", post([H, CTA]), post(["Experts say shops pay 3%.", CTA])),
    ("MC-E07", post(["Restaurants ran $61.8B in Q3 2026.", CTA], slide_extra={1: {"has_stat": True, "source_line": "Source: Toast Q3 2026 results, Nov 2026"}}),
     post(["Restaurants ran $61.8B through Toast.", CTA], slide_extra={1: {"has_stat": True, "source_line": "Source: Toast results, Nov 2026"}})),
    ("MC-E10", post([H, CTA], facts=[{"text": "case", "type": "law_status", "checked_on": "2026-11-12", "numbers": []}]),
     post([H, CTA], facts=[{"text": "case", "type": "law_status", "checked_on": "2026-11-01", "numbers": []}])),
    ("MC-F02", post(["Example: on $500,000 a year in card sales, a 2.6% rate costs $13,000.", CTA], slide_extra={1: {"example": True}}),
     post(["Example: a rate costs $13,000.", CTA], slide_extra={1: {"example": True}})),
    ("MC-F06", post(["Example: say a shop sells $500,000.", CTA], slide_extra={1: {"example": True}}),
     post(["Example: this shop saved $13,000. A real case study.", CTA], slide_extra={1: {"example": True}})),
    # savings, disclosures, free claims
    ("MC-H01", post([UP, CTA]), post(["Keep $13,000 a year, up to. *Example: $500,000.", CTA])),
    ("MC-H05", post([UP, CTA]), post(["Keep up to $13,000 a year. *Example: $500,000 at 2.6%.", CTA])),
    ("MC-H06", post([UP, CTA]), post(["Keep up to $13,000 a year if every sale is paid with SVPay.", CTA])),
    ("MC-H07", post([UP, CTA]), post(["Keep up to $13,000 a year if every sale is paid with SVPay. *Example.", CTA])),
    ("MC-H08", post([UP, CTA]), post(["You will save $13,000.", CTA])),
    ("MC-H11", post(["Save up to 2.6% of each card sale.", CTA]), post(["Save 2.6% now.", CTA])),
    ("MC-I03", post(["Up to $13,000.* *Example: $500,000.", CTA]), post(["Up to $13,000.* Keep more.", CTA])),
    ("MC-I06", post([UP, CTA]), post(["Up to $13,000. *T&Cs apply.", CTA])),
    ("MC-I07", post([UP, CTA]), post(["Up to $13,000. *Example: " + " ".join(["word"] * 40) + ".", CTA])),
    ("MC-K01", post(["When customers pay with SVPay instead of a card, the shop pays 0% in processing fees and $0 to cash out; SVPay makes money from paid software plans instead.", CTA]),
     post(["No fees, ever. Totally free.", CTA])),
    ("MC-K02", post(["$0 to cash out on every plan.", CTA]), post(["$0 to cash out*", CTA])),
    ("MC-K05", post(["$5 back on your first payment.", CTA]), post(["Get $5 back now.", CTA])),
    ("MC-L02", post(["The FTC says “typically paid more.”", CTA], facts=[{"text": "q", "quote": "typically paid more", "quote_found": True, "source_url": "https://x.gov", "source_date": "2026-11-02", "numbers": []}]),
     post(["The FTC says “they always pay more.”", CTA], facts=[])),
    ("MC-M04", post([H, CTA]), post(["SVPay, in partnership with Visa.", CTA])),
    ("MC-M07", post(["Toast took in $1.62B. SVPay is not affiliated with Toast.", CTA]), post(["Toast took in $1.62B.", CTA])),
    ("MC-N03", post(["The FTC sued Riverside.", CTA]), post(["FTC-approved payments.", CTA])),
    ("MC-N04", post(["See ftc.gov.", CTA], facts=[{"text": "x", "numbers": [], "source_url": "https://ftc.gov/a", "source_date": "2026-11-02"}]), post(["See svpay-payments.gov.", CTA])),
    ("MC-P04", post([H, CTA]), post(["Always worth $1.", CTA])),
    ("MC-P06", post([H, CTA]), post(["Check your wallet balance.", CTA])),
    ("MC-P07", post([H, CTA]), post(["SVPay is fully licensed.", CTA])),
    ("MC-S01", post(["Shops paid about $2,250.", CTA]), post(["Shops paid about $7,777.", CTA])),
    ("MC-S03", post(["Rates fell from 2.6% to 2.5%, down 0.1 percentage points.", CTA]), post(["Rates fell from 2.6% to 2.5%, down 0.1%.", CTA])),
    ("MC-S06", post(["$47 a month for 4 years is about $2,250.", CTA]), post(["$47 a month for 4 years is $2,100.", CTA])),
    ("MC-S08", post(["3 times as much.", CTA]), post(["3 times more.", CTA])),
    ("MC-S10", post(["Filed Monday, Nov 2, 2026.", CTA]), post(["Filed Tuesday, Nov 2, 2026.", CTA])),
    ("V-K15", post(["Visa cards and Mastercard.", CTA]), post(["Visa's network.", CTA])),
    ("MC-Q05", post(["4.8 from 37 Google reviews, Nov 2026 ★★★★★", CTA]), post(["★★★★★ 4.8 stars", CTA])),
    ("MC-Q08", post(["Trusted by 12 shops.", CTA], facts=[{"text": "pilot", "numbers": ["12"], "own_data": True}]), post(["Trusted by 10,000 shops.", CTA])),
]


class Rules(unittest.TestCase):
    pass


def _mk(i, cid, good, bad):
    def t(self):
        self.assertEqual(run(good, cid), "pass", f"{cid} should pass its passing case")
        self.assertEqual(run(bad, cid), "fail", f"{cid} should fail its failing case")
    return t


for _i, (_c, _g, _b) in enumerate(ROWS):
    setattr(Rules, f"test_{_i:02d}_{_c.replace('-', '_')}", _mk(_i, _c, _g, _b))

TESTED = sorted({r[0] for r in ROWS})


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
