# The SVPay post algorithm

Every post is checked against 887 criteria before it publishes; 115 of them are hard gates, and any one gate failing
blocks the post. It replaces the old "independent reviewer scores 1-10, ship at 9" step, which let the Nov 11, 2026
card-fees post through. That post fails 11 gates (`checks/labels/2026-11-11-tax-tips-REJECTED.json`).

- `checks/criteria.json`: all 887 criteria (ID, the check, pass threshold, weight 1-5, tier, gate, score category).
- `checks/check_post.py`: the script tier, run on the post package (below). Exit code 1 means a gate failed.
- `checks/judge_batches.py judge` / `panel`: the judge-tier and panel-tier checks, 15 per batch (`--skip-visuals` / `--only-visuals` split the judge's work in two).
- `checks/merge.py`: merges the judges' and the panel's answers into `judge.json` and `panel.json`.
- `checks/score.py`: combines the three into PUBLISH, HOLD or REWRITE.
- `checks/labels/`: posts the founder has labeled (APPROVE or REJECT, with his reason). They anchor the judges.

## The nine steps

| Step | What happens | Hard rule |
|---|---|---|
| 1. Research | One story inside SVPay's niche (fees, payments, local small business). Primary sources (bill or case number, filing, agency page) plus two independent reports. Every fact, number and quote goes in the package's `facts` log with its link and date. Law and court status is re-checked on publish day (`type: "law_status"`, `checked_on: <today>`). Pull one real item from the founder's facts log if there is one. | No real angle means no news take: use another format (owner how-to, data breakdown). |
| 2. Write | Draft 3 candidates. Each network gets its own version (at most 60% shared 3-word runs between any two). | Never: "not X, it's Y", "As a nurse, I learned...", "here's my unpopular take", "Here's something most people don't know", "Agree?/Thoughts?", unnamed "experts say". |
| 3. Render | Slides from `pipeline/` at 2160x2700. LinkedIn: one 4:5 portrait image for a news take, or an 8-12 page PDF for a data or explainer post. Instagram 5-10 slides. X at most 4 images, each 5 MB or less. Google Business Profile one JPG or PNG, 10 KB-5 MB. Alt text on every image. | No text-only post on any network except X. |
| 4. Script checks | `python3 checks/check_post.py post.json --history history.json --private-terms <names file> --link-results links.json --out script.json`. `links.json` holds each link's result from a web tool (`{"<url>": true}`), for runs where the shell can't reach the web. | Any gate failure: rewrite before any judge runs. |
| 5. Judge | A fresh reviewer that did not write the post. It reads `checks/judge_batches.py judge` and rules on every check in batches of at most 15: reason first, then pass, fail or na, quoting the failing words. It sees the labeled posts in `checks/labels/` as anchors. It never edits. | `merge.py judge` writes `judge.json`. |
| 6. Panel | 3 more fresh reviewers, each reading as a different person (a busy shop owner, a payments professional, a skeptic who spots generic, machine-made copy fast). Each answers the panel checks and compares the post with the 5 reference posts, in both orders. | At least 2 of 3 say it reads as written by a person; the post wins or ties at least 3 of 5 references. `merge.py panel` writes `panel.json`. |
| 7. Score | `python3 checks/score.py script.json judge.json panel.json --out decision.json` | See the decision table. |
| 8. Publish | Metricool publishes. One hour after each slot: the post renders, its link works, nothing is cut off. | Fix or delete within 1 hour. |
| 9. Learn | Results at 24 h, 72 h and 7 days, as rates per network. | Learning never relaxes a gate or a copy rule. |

## The decision

| Result | What happens |
|---|---|
| Any gate fails | REWRITE only the failed parts, with the failing words quoted. At most 2 rewrites, then BACKUP. |
| All gates pass, every category minimum met, Q >= 0.85, panel 2-3 "written by a person", 3 of 5 references won or tied | PUBLISH automatically. |
| Q 0.75-0.85, a minimum missed by 0.05 or less, or a 2-1 panel split | HOLD: schedule it as a Metricool draft and tell the founder, with the failed checks quoted. |
| Q below 0.75 | REWRITE (at most 2), then BACKUP. |
| A HOLD with no answer 2 hours before the slot, or no rewrite passes | Publish a founder-approved backup post. Until the backup bank has 6 approved posts per network, skip the slot instead. The held draft never publishes on its own. |
| The founder flags any published post as bad | The next 4 posts need his approval. |

- **Score:** each category is the weighted share of its checks passed (weights 1-5; a criterion that fails on any network fails once). Q = Writing^0.30 x Audience value^0.25 x Visuals^0.20 x Platform fit^0.15 x Brand^0.10.
- **Minimums:** Writing 0.85, Brand 0.80, Visuals 0.80, Platform fit 0.75, Audience value 0.75, compliance checks that are not gates 0.95.
- **Compliance** can block a post but never adds points.

## The package (`post.json`)

```json
{
  "date": "2026-11-12", "slug": "story-slug", "kind": "news", "topic": "story-slug", "format": "data",
  "skeleton": "number-hook/news/what-it-means/example/ask", "closing_question_format": "your-number",
  "why_now": "one line when the news is more than 2 days old",
  "networks": {
    "linkedin":  {"text": "...", "first_comment": "...", "media": ["posts/<folder>/slide-1.jpg"], "alt": ["..."], "pdf_title": "...", "pdf_pages": 9},
    "facebook":  {"text": "...", "first_comment": "...", "media": ["..."], "alt": ["..."]},
    "instagram": {"text": "...", "media": ["..."], "alt": ["..."]},
    "x":         {"text": "...", "reply": "...", "media": ["..."], "alt": ["..."]},
    "threads":   {"text": "...", "reply": "...", "media": ["..."], "alt": ["..."]},
    "gbp":       {"text": "...", "media": ["..."], "alt": ["..."], "cta_url": "https://www.svpay.app/..."}
  },
  "facts": [
    {"text": "the claim", "numbers": ["$1.62 billion"], "source_url": "https://...", "source_date": "2026-11-03"},
    {"text": "Illinois law status", "type": "law_status", "checked_on": "2026-11-12", "numbers": [], "source_url": "https://...", "source_date": "2026-10-20"},
    {"text": "the quote's context", "quote": "exact words", "quote_found": true, "source_url": "https://...", "source_date": "2026-11-10"},
    {"text": "worked example", "numbers": ["$500,000", "2.6%", "$13,000"], "example": true}
  ],
  "math": [{"expr": "500000*0.026", "shown": "$13,000"}],
  "slides": [{"file": "posts/<folder>/slide-1.jpg", "role": "hook", "text": "all words on the slide", "example": false, "has_stat": true, "source_line": "Source: ..."}]
}
```

`history.json` is the last 20 posts, one entry per network post: `date, network, text, hook, hashtags, stats_used, topic, format, skeleton, closing_question_format, ends_with_question, mentions_nurse, medical_metaphor, tagline`.

## Decisions applied (founder, Nov 11, 2026)

1. **Company logos:** company names in plain words on slides; no third-party logos until counsel clears it; Visa and Mastercard marks never.
2. **"Created by a nurse, not a hedge fund":** bios, banners and print; in post bodies at most once a month.
3. **LinkedIn:** one 4:5 portrait image for news takes; an 8-12 page PDF for data or explainer posts; never text only.
4. **The nurse credential:** at most 1 post in 4, only with a specific ICU detail that changes the argument, never right before the pitch.
5. **New post formats:** the first post of a new format is held for the founder's one-tap approval.
6. **How SVPay works:** only the founder-approved plain sentence, and only when a post explains how zero fees work. There is none yet for what SVPay runs on, so posts don't explain it.

## Approved lines (pricing, free, no fees)

`checks/approved_lines.json` holds the lines a post may use word for word whenever it says "no fees", "without the fee", "$0", "free" or anything about price (CR13, CR14, CR34, CO04, CO06). They were chosen on Nov 11, 2026 from the founder's pricing decision ("Never paid by volume") and www.svpay.app/pricing, because the founder asked Claude to choose. Use them next to the claim, not in a footnote. A pricing sentence that is not one of these lines fails the judge. Avoid "free" unless the `free_calculator` or `pricing_full` line sits next to it.

## Network specs

| Network | Format | Text | Hashtags | Link |
|---|---|---|---|---|
| LinkedIn (founder's profile) | 4:5 portrait image, or 8-12 page PDF (title 60 characters or fewer) | 1,200-1,800 characters; the concrete hook in the first 140 | 0-2 (max 3) | First comment |
| Instagram | Carousel, 5-10 slides | Main point in the first 125 characters | 0-3 (hard cap 5) | "link in bio" |
| Facebook Page | Original image | Situation, what happened, takeaway | 2 max | First comment |
| X | Text first; images only when they carry data (max 4, 5 MB each) | 280 characters max; one claim plus one number | 0-1 | Self-reply |
| Threads | Image or carousel | 500 characters max | Exactly 1 topic tag | Reply |
| Google Business Profile | One real photo or one clean, low-text original graphic | 80-100 words (1,500 max); main point in the first 100 characters; no phone numbers, emails or handles | 0 | CTA button to the matching www.svpay.app page |

## Learning

- Rates per network (actions / impressions), log scale. Trust order: sign-ups > tracked link clicks > saves and shares > comments of 6+ words > reactions > impressions.
- **Leaning:** at least 80% probability that B beats A. It only changes what gets tried next.
- **Proven:** at least 90% probability under a skeptical prior, with at least 12 posts per side. Only Proven changes the playbook. One post idea on 6 networks counts as one test.
- At most 2 variables per post, at most 4 options each; Thompson sampling picks the next option.
- Learning can move a soft check's weight by 1 step a month at most. It never touches a gate or a copy rule.

## Lessons from the first runs (Nov 11, 2026)

The first post under the algorithm (FTC v. Riverside Payments) went through two rewrites and three rounds of fresh reviewers and was skipped: the readers split 2 to 1 on "written by a person", and one gate (CR06) still failed. What every reviewer said, and the rule it becomes:

- **Voice by account.** LinkedIn is the founder's own profile: first person, and it says he started SVPay. Facebook, Instagram, X, Threads and Google Business Profile are SVPay's accounts: write them as SVPay ("we"), not as the founder. Pasting "I started SVPay, which builds..." into every network read as a template to every reader.
- **One real detail beats polish.** The readers who said "machine-written" all pointed to the same gap: nothing only Derrik or SVPay could say. Each post needs one true, specific thing from SVPay itself (a number from the pricing page, how SVPay handles the issue in the story, or a founder fact from his facts log). Never invent one; when none exists, pick a story where SVPay has a real angle.
- **Court cases:** every network that names a lawsuit states where it stands (for example "filed with a proposed settlement that still needs a judge"), not only the first comment (CR06). Pair a denial with that status.
- **Attribute every allegation** ("the FTC says", "the complaint says") and keep the source's own hedges ("about", "approximately") on every number.
- **No set-piece closers or bridge lines** ("This case shows why.", "Those answers take a few minutes..."), no "my advice is simple", no closing question repeated across networks.
- **3D:** only extruded numerals have rendered clean so far. Arrows, question marks and coins showed seams and stair-step bevels (V-I07). Data marks are flat 2D. Every glyph is extruded on its own with even gaps.
- **Shadow floor:** a faint stair-stepped line across the background behind the numerals was the edge of the light's shadow camera (V-I07, Nov 11). The bounds are now +-90; zoom to 300% behind every 3D object and check for any line before review.
- **Text on slides:** measure contrast on the rendered pixels and check letter spacing in crops before review; the script tier does not measure either yet.

## Known limits (Nov 11, 2026)

- The research calls for reviewers from at least 2 independent setups. Today the judge and the panel share one setup, kept independent by giving each a fresh start with no view of the drafting. Add a second, independent reviewer when one is available.
- Each judge check should carry a founder-labeled pass example and fail example. Until the founder labels 60-100 posts, the judges anchor on the labeled posts in `checks/labels/`.
- Thresholds are starting values. V-D06 (light background) is calibrated on the founder-approved Nov 4 deck: mean L* 75 or higher (the research's 85 stays a soft target).
