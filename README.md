# svpay-social

Images for SVPay's social posts (www.svpay.app). Everything here is public, so Metricool can attach the slides by link.

- `posts/<date>-<topic>/` holds each post's slides (`slide-1.jpg` …, 2160×2700, 4:5) and its caption.
- `pipeline/` is how the slides are made: 3D scenes in three.js, rendered in headless Chromium, with the text laid over in HTML.
- `STANDARDS.md` is the quality bar every post has to clear before it goes out.

Company logos used on news slides are supplied for each post and are not stored here. SVPay is not affiliated with the companies it reports on.

## Making a post

```
npm install                          # three, opentype.js, fonts
cd pipeline && ln -s ../node_modules node_modules
node shot.js bgpano.html 6480 1350 2 bg_pano.png      # background, then cut into bg_1..6.png (2160x2700 each)
node run3d.js 1 2 3 4 5 6                              # 3D hero per slide -> hero_N.png (+ info_N.json for label positions)
node render.js 2 slides.html out                       # text over the heroes -> out1.png .. out6.png at 2160x2700
```

Headless Chromium needs WebGL through SwiftShader (`run3d.js` passes the flags).
