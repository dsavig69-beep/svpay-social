# svpay-social

Images for SVPay's social posts (www.svpay.app). Everything here is public, so Metricool can attach the slides by link.

- `posts/<date>-<topic>/` holds each post's slides (`slide-1.jpg` …, 2160×2700, 4:5) and its caption.
- `pipeline/` is how the slides are made: 3D scenes in three.js, rendered in headless Chromium, with the text laid over in HTML.
- `STANDARDS.md` is the quality bar every post has to clear before it goes out; `ALGORITHM.md` and `checks/` are how it is enforced (887 checks, 115 hard gates).

Company logos used on news slides are supplied for each post and are not stored here. SVPay is not affiliated with the companies it reports on.

## Making a post

```
npm install                          # three, opentype.js, fonts
cd pipeline && ln -s ../node_modules node_modules
node shot.js bgpano.html 6480 1350 2 bg_pano.png      # background, then cut into bg_1..6.png (2160x2700 each)
P=../posts/<folder>/passes
node run3d.js 1 2 3 4 5 6 --passes $P                  # 3D hero per slide -> hero_N.png (+ info_N.json), and its 3D passes
node render.js 2 slides.html out --passes $P           # text over the heroes -> out1.png .. out6.png at 2160x2700, and the text passes
python3 pack_passes.py $P                              # 1-bit masks, JPEG backgrounds: small enough to commit
cd .. && python3 pipeline/export.py posts/<folder> --png pipeline/out --update-post
                                                       # slide-N.jpg (master) + slide-N-1440.jpg, routed per network,
                                                       # files.json and provenance.json next to the slides
python3 checks/check_post.py posts/<folder>/post.json --out script.json
```

Run every render and every full check under `flock /home/claude/work/heavy.lock` on the shared builder.
Headless Chromium needs WebGL through SwiftShader (`run3d.js` passes the flags).

- **Render passes** (`--passes`): a text-free background, a text-only mask, an unclipped copy, a manifest of every text
  element (intended text, role, box, font, size, color), and for each 3D hero an object-ID pass for the numerals, a
  shadow-only pass and `scene.json`. `checks/check_post.py` measures text and 3D on them; without them a gate is
  "needs passes" and counts as failing.
- **Pre-sizing** (`export.py`): Instagram, Facebook and Threads get the 1440×1800 JPEG (quality 92, 4:4:4); LinkedIn
  and X get the 2160×2700 master when it is under 5 MB, else the 1440 file.
- **Tests:** `python3 -m unittest discover -s checks/tests -t .` (synthetic slides, no render needed), and
  `flock /home/claude/work/heavy.lock python3 checks/regression/run.py` for the regression set (approved Nov 4 deck,
  rejected Nov 11 renders).
