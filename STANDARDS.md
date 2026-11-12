# The bar for every SVPay post

Each post has to beat the one before it. The goal is a reader who stops scrolling, swipes to the last slide and wants more.

## Story
- Slide 1 is the hook: one shocking, true number and one plain sentence. No warm-up.
- One idea per slide. The carousel builds: the news, what it means, where the money goes, what it means for *your* shop, what to do, then the call to action.
- Plain words. No jargon ("fintech", "interchange", "take rate") on the slides; keep technical terms for the caption or replies.
- Never the words blockchain, on-chain, crypto, token, deposit, withdrawal or KYB. "cashback" is one word. American spelling.

## Truth
- Every number traces to a primary source (SEC filing, official release) named on the slide.
- Derived numbers are shown exactly as the arithmetic gives them, and the words never claim more than the source does (say "mostly", "on average", "up to" where they are true).
- Savings are always "up to", with a one-line footnote stating the example's assumptions.
- A competitor's logo appears only to identify the company in the news, with "SVPay is not affiliated with <company>." on every slide that shows it.

## Look
- Light, glassy, holographic. Never a dark background.
- One 3D hero per slide, in the shared materials: glossy holographic "jelly" (cobalt → violet → pink) and pearl white details. No hand-drawn art, no emoji.
- The same frame on every slide: header (company chip with logo on news slides, "What it means for you" after), slide counter, SVPay logo with "Everybody Wins" bottom-left, www.svpay.app bottom-right.
- 72px side margins. Hero objects stay inside the frame unless a crop is deliberate.
- Letters never touch, in 2D or 3D. Text never sits on a shadow, a reflection or a busy area.
- Big type, few words. Body copy 25px or more at 1080 wide.

## Before it goes out
Every post runs the algorithm in `ALGORITHM.md`: 1,578 checks, 227 hard gates, script checks first (`checks/check_post.py`), then a judge and a three-reader panel, then `checks/score.py`. A post publishes on its own only when every gate passes, every category clears its minimum and Q is 0.85 or higher. This replaces the old 1-10 reviewer, which let the Nov 11 card-fees post through.

1. Render at 2160×2700 and look at every slide at full size, plus close-up crops.
2. Run the script checks, the judge and the panel. Fix what fails, with the failing words quoted. At most 2 rewrites.
3. Captions match the slides, carry the source, and link to www.svpay.app with tracking.

## Keep improving
- Before each new post, check how the last posts did (reach, saves, shares, comments, link clicks) and write down what to repeat and what to drop.
- Change at least one thing each time on purpose (the hook, the hero, the layout, the color moment) and note the result.

## Log
- **2026-11-04, Toast Q3 (6 slides).** First 3D carousel. Reviewer's first pass: 6/10. Fixed before posting: savings said as "up to" with a footnote; the hook reworded to what the filing supports ("took in $1.62B from payments"); "× 100" added to the rate formula; "card-issuing banks"; 100-coin grid to show $2.62 per $100 honestly; solid coins so the colour split falls on a coin edge; softer shadows and weaker reflections behind text; one consistent frame and the disclaimer on every Toast slide. Next time: start from these fixes, and try a cover slide with even more contrast in the hook.
- **2026-11-11, card fees on sales tax and tips (no slides).** Published text only on LinkedIn, Facebook and Google Business Profile; the founder rejected it as low quality and generic. It fails 11 gates of the new algorithm: text only, an out-of-date law status, an unfound quote, unsourced bills, a no-fee promise with no line on how SVPay makes money, "not just the symptom", "As a nurse, I learned...", "here's my unpopular take", "Here's something most people don't know". From now on the algorithm in ALGORITHM.md decides what publishes.
- **2026-11-11 (evening), FTC v. Riverside Payments: SKIPPED.** Two rewrites and three rounds of fresh reviewers. Round 1 Q 0.819 (gates CO01, CO04, CO06, V-I07; two factual slips: "up to 60%", "the complaint says typically"). Round 2: 1 of 3 readers said "written by a person" (the pasted pricing lines). Round 3: 2 of 3 readers, 5 of 5 references won or tied, facts all verified, but CR06 (case status missing on four networks) still failed, so the slot was skipped. What changed for next time: see ALGORITHM.md, "Lessons from the first runs" (voice by account, one real SVPay detail, case status everywhere, 3D only on numerals, measure contrast before review).
