# The SVPay post algorithm

Every post is checked against 1,578 criteria before it publishes; 227 of them are hard gates, and any one gate failing
blocks the post. 911 of them check the images (139 gates), and 761 of those run in the script tier on the rendered
pixels. It replaces the old "independent reviewer scores 1-10, ship at 9" step, which let the Nov 11, 2026 card-fees
post through. That post fails 11 gates (`checks/labels/2026-11-11-tax-tips-REJECTED.json`).

- `checks/criteria.json`: all 1,578 criteria (ID, the check, pass threshold, weight 1-5, tier, gate, score category, merged duplicates).
- `checks/image/`: the image checks of the script tier (text on the pixels, 3D, files, layout, brand, charts, delivery), read by `check_post.py`.
- `checks/tests/` (unit tests on synthetic slides) and `checks/regression/` (`run.py`: the approved Nov 4 deck re-rendered, the rejected Nov 11 renders and the tax-tips post).
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
| 3. Render | Slides from `pipeline/` at 2160x2700, rendered with `--passes` (render.js and run3d.js write a text-free background, a text-only mask, an unclipped copy, a manifest of every text element, and per 3D hero an object-ID pass, a shadow-only pass and `scene.json` into `posts/<folder>/passes/`). Then `pipeline/export.py` pre-sizes the files (network specs below) and writes `files.json` and `provenance.json`. LinkedIn: one 4:5 portrait image for a news take, or an 8-12 page PDF for a data or explainer post. Instagram 5-10 slides. X at most 4 images, each 5 MB or less. Google Business Profile one JPG or PNG, 10 KB-5 MB. Alt text on every image. | No text-only post on any network except X. No passes, no publish: an image gate measured without its passes is "needs passes" and counts as failing. |
| 4. Script checks | `python3 checks/check_post.py post.json --history history.json --private-terms <names file> --link-results links.json --out script.json`. `links.json` holds each link's result from a web tool (`{"<url>": true}`), for runs where the shell can't reach the web. The image checks read the passes next to each slide; the `--out` folder also gets a contrast and size report and a crop sheet. | Any gate failure: rewrite before any judge runs. |
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

Optional fields the image checks read when a post has them: `charts` (declared chart marks, values, pixel heights, colors and axis), `linkedin.pdf` and `linkedin.account`, `x.thread`, `releases` (people's release ledger), `real_shops` (permission files), `claim_checks` (the render-time and pre-publish claim re-checks) and `delivery` / `og_audit` (what the networks served after publishing).

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
| LinkedIn (founder's profile) | 4:5 portrait image: the 2160×2700 master JPEG when it is under 5 MB, else the 1440×1800 file; or an 8-12 page PDF of 4:5 pages (title 60 characters or fewer; PDFs at most half of LinkedIn posts until 5,000 followers) | 1,200-1,800 characters; the concrete hook in the first 140 | 0-2 (max 3) | First comment |
| Instagram | Carousel, 5-10 slides (API limit 10), 4:5 only, 1440×1800 JPEG quality 92 4:4:4 | Main point in the first 125 characters | 0-3 (hard cap 5) | "link in bio" |
| Facebook Page | Original image, 1440×1800 JPEG quality 92 4:4:4 | Situation, what happened, takeaway | 2 max | First comment |
| X | Text first; images only when they carry data (max 4, 5 MB each): the 2160×2700 master when under 5 MB, else 1440×1800 | 280 characters max; one claim plus one number | 0-1 | Self-reply |
| Threads | Image or carousel (at most 10; the API allows 2-20), 1440×1800 JPEG quality 92 4:4:4 | 500 characters max | Exactly 1 topic tag | Reply |
| Google Business Profile | One real photo or one clean, low-text original graphic | 80-100 words (1,500 max); main point in the first 100 characters; no phone numbers, emails or handles | 0 | CTA button to the matching www.svpay.app page |

## Image checks (Nov 11 research round)

The five image-research files and the 220 V checks were merged into 911 image checks: 694 new, 92 researched checks dropped as duplicates (the most specific one kept; the others are listed in its `merged` field), 3 V checks replaced. Gates are only for compliance breaches, wrong or untraceable numbers, unreadable or clipped text, broken 3D and placeholder leaks (139 image gates). Calibration (ruling 4) on the approved Nov 4 deck, re-rendered with its passes (`checks/regression/nov4-rerender`): 7 gates that failed it became weight-5 checks (V-D01, T-S05, T-O01, T-O03, T-O11, V-C20, R-X01); the compliance and number gates it fails stay gates (MC-I08, MC-K01, MC-S01, V-G08, V-H17, V-K12); the 9 V-I07 gates (R-D01, R-N01, R-Z03, R-Z04, R-Z06, R-S01, R-S02, R-G01, R-G02) apply to posts dated Nov 11, 2026 or later, because the Nov 4 scene had the same shadow camera and bevels fixed that day. Every rejected Nov 11 slide fails a gate on the reason it was rejected (`flock /home/claude/work/heavy.lock python3 checks/regression/run.py`). Checks that need a tool this machine cannot install return "tool not installed" and never fail a post (`checks/image/stubs.py`). The merge log is `checks/image/merge_log.json`.

- **F-IG** Instagram carousels (39, 2 gates): slide-1 hook words, swipe flow, counters, carousel length and API limits.
- **F-LD** LinkedIn documents (17, 1 gate): PDF pages, title, page order, size, PDF share of LinkedIn posts.
- **F-NI** News single images (12): one claim per image, no third-party screenshots or raw phone screens.
- **F-X** X images (10, 2 gates): image count, crops in the timeline, paired images, threads.
- **F-TH** Threads images (2): image count and the single-image cut.
- **F-GB** Google Business Profile images (16, 1 gate): 4:3 file, size floor, no filters, no duplicates, posting pace.
- **F-SW** Swipe cues and seams (9, 1 gate): panorama seams line up, a cue that the deck continues.
- **F-TN** Thumbnails and crops (15, 1 gate): the hook survives the grid, square and timeline crops.
- **T-S** Type size (measured) (12): x-height and stems on the pixels, size floors by role.
- **T-C** Contrast (measured) (21): WCAG and APCA on the text-free background, after delivery compression.
- **T-G** Text on glass (3): blur and opacity behind text on frosted panels.
- **T-K** Spacing (18): glyph clearance, tracking, kerning, label gaps.
- **T-L** Lines and breaks (18): line length, widows, rag, paragraph spacing.
- **T-N** Numbers on images (18, 2 gates): lining figures, numbers never split across lines, $ and % sizing.
- **T-D** 3D numerals (type) (6, 1 gate): glyph topology, slivers, counters that stay open.
- **T-J** Delivery and JPEG (7): text stays legible after the networks re-encode.
- **T-O** OCR and text integrity (12, 3 gates): exact OCR read-back, digits-only reads, no clipped or placeholder text.
- **T-P** Text coverage and panels (4): share of the slide under text, panel fit.
- **T-F** Fonts (13): fonts loaded and pinned, weights, optical sizes.
- **R-P** Render pipeline (5, 1 gate): fonts loaded before capture, pinned renderer, regression set.
- **R-A** Anti-aliasing (10, 1 gate): MSAA kept, text composited after any screen-space pass.
- **R-S** Shadows (15, 2 gates): shadow camera covers the floor, no frustum line, contact shadows.
- **R-Z** Depth and intersections (9, 4 gates): no z-fighting, speckle or glyphs cutting into each other.
- **R-N** Normals and seams (8, 1 gate): no unexplained edges or seams inside a glyph, front faces only.
- **R-B** Banding (6): gradients without steps, before and after compression.
- **R-X** Tone and highlights (9): tone mapping, color space, glyphs separate from a light background.
- **R-F** Noise and grain (5): even grain, no fireflies.
- **R-G** Glass (11, 2 gates): no glyph cut by a card, transmission settings.
- **R-D** 3D glyph geometry (15, 4 gates): bevel feasibility, even glyph gaps, 3D only on numerals.
- **R-T** Renderer settings (5): shadow radius, environment, renderer flags.
- **R-K** AI and template look (23): repeated layouts, default palettes, stock-3D tells.
- **R-Q** Material and light (7): value structure at thumbnail size, highlights.
- **R-C** Composition and depth (10): focal point, depth cues, crop safety.
- **R-M** Image quality metrics (4): no-reference quality scores (stubs until pyiqa can run).
- **MC-A** Chart choice (9): allowed chart types, one chart per slide, plot area.
- **MC-B** Chart scales (11, 3 gates): bars from zero, no broken or dual axes, proportional marks.
- **MC-C** Chart labels (7, 1 gate): direct labels at legible sizes, no logo labels.
- **MC-D** Number format (6): currency and percent format, rounding.
- **MC-E** Sources on images (10, 2 gates): a dated source line on every data slide.
- **MC-F** Worked examples (7, 2 gates): the 'Example' label next to the hero number, inputs shown.
- **MC-G** Data color (5): series colors from the data palette.
- **MC-H** Savings claims (12, 9 gates): 'up to' figures with the every-sale note and footnote.
- **MC-I** Disclosures (11, 6 gates): footnotes legible and clear of effects.
- **MC-J** Competitor comparisons (10, 5 gates): same basis and date on both sides.
- **MC-K** Free and $0 claims (8, 5 gates): approved lines only.
- **MC-L** Legal news (8, 5 gates): case status and verbatim quotes.
- **MC-M** Third-party marks (7, 4 gates): company names in plain words, no altered logos.
- **MC-N** Government marks (4, 3 gates): no seals or badges.
- **MC-O** People and releases (6, 4 gates): release ledger, bystanders, real shops with permission.
- **MC-P** Money wording (6, 4 gates): public images never use the banned money words.
- **MC-Q** Reviews and testimonials (10, 7 gates): real, sourced, star fill matches the rating.
- **MC-R** AI provenance (6, 4 gates): generative-AI use disclosed, provenance metadata.
- **MC-S** Number scripts (10, 6 gates): every number on the image traces to the facts log and the math.
- **QF** Files and pre-sizing (25, 6 gates): 1440x1800 q92 4:4:4 to IG, FB, Threads; the master to LinkedIn and X under 5 MB.
- **QM** Metadata (13, 1 gate): no EXIF, GPS, XMP or C2PA in the files; a private provenance sidecar.
- **QB** Brand across posts (28): palette, margins, type tokens and covers stay in the series.
- **QS** Series system (5): series tags and recurring formats.
- **QA** Accessibility (26): color-blind safety, alt text, tagged PDFs.
- **QR** Render regression (22, 1 gate): pinned versions, deterministic renders, goldens.
- **QD** Delivery after publish (28, 6 gates): the networks served the files as sent, in order, with alt text.

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
- **Text on slides:** contrast, size and letter spacing are measured on the rendered pixels and the render passes (T-C, T-S, T-K), before any reviewer sees the slides.

## Known limits (Nov 11, 2026)

- The research calls for reviewers from at least 2 independent setups. Today the judge and the panel share one setup, kept independent by giving each a fresh start with no view of the drafting. Add a second, independent reviewer when one is available.
- Each judge check should carry a founder-labeled pass example and fail example. Until the founder labels 60-100 posts, the judges anchor on the labeled posts in `checks/labels/`.
- Thresholds are starting values. V-D06 (light background) is calibrated on the founder-approved Nov 4 deck: mean L* 75 or higher (the research's 85 stays a soft target).
- The Nov 4 deck's 3D render knobs were not recorded, so its re-render (used for calibration) frames the 3D larger than the approved JPGs; record every `run3d.js` job string in the post folder. Passes that do not match their slide's pixels count as missing (`needs passes`).
- Stubs (tool not installed, never fail a post): R-M01-R-M03 (pyiqa), R-D02, R-D14, R-D15 (PyMeshLab), R-B01 (CAMBI), R-K10-R-K12, QB28, QB29 (CLIP and AI-image detectors), T-O02 (EasyOCR), F-GB12 (Cloud Vision), QA25 (veraPDF), MC-R06 (c2patool), MC-N01 (seal detector). R-A03, R-M04, R-S03, R-S04, R-S10, R-X06, R-G04, R-G11, R-F04 and R-N04 wait for render passes the pipeline does not write yet.
