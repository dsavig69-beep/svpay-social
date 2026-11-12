#!/bin/bash
# Re-render a past version of a post's slides with today's render.js / run3d.js, to write the measurement passes the
# script tier needs (the past renders themselves are the slide JPGs from git). Runs under the shared heavy lock.
#
#   checks/regression/rerender.sh <commit> <slides.html> <scene.html> <passes dir> <names> [<final png dir>]
#   names: section id to slide file stem, e.g. 1:slide-1,1L:slide-1-single,2:slide-2
#
# Third-party logos are not stored in the repo (README), so a missing one is replaced by a transparent stand-in of the
# width given in LOGO_STANDINS ("toast-logo.png:150x60"), which keeps the text where it was on the approved slide.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
COMMIT="$1"; SLIDES="$2"; SCENE="$3"; PASSES="$(realpath -m "$4")"; NAMES="$5"; FINAL="${6:-}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
git -C "$REPO" archive "$COMMIT" pipeline | tar -x -C "$WORK"
cd "$WORK/pipeline"
ln -sf "$REPO/node_modules" node_modules
cp "$REPO/pipeline/render.js" "$REPO/pipeline/run3d.js" "$REPO/pipeline/passes3d.js" .
for f in "$REPO"/pipeline/bg_*.png "$REPO"/pipeline/svpay-logo.png; do [ -e "$(basename "$f")" ] || cp "$f" .; done
for s in ${LOGO_STANDINS:-}; do n="${s%%:*}"; wh="${s##*:}"; [ -e "$n" ] || python3 -c "from PIL import Image; w,h=map(int,'$wh'.split('x')); Image.new('RGBA',(w,h),(0,0,0,0)).save('$n')"; done
if grep -q "screen.png" "$SCENE" && [ ! -e screen.png ]; then flock "${HEAVY_LOCK:-/home/claude/work/heavy.lock}" node shot.js screen.html 980 2120 1 screen.png; fi
IDS="$(echo "$NAMES" | tr ',' '\n' | cut -d: -f1 | paste -sd, -)"
JOBS=""
for h in $(grep -o 'src="[a-z_]*hero_[0-9]*\.png"' "$SLIDES" | sort -u | sed 's/src="\(.*\)\.png"/\1/'); do n="${h##*_}"; JOBS="$JOBS $n:$h"; done
mkdir -p "$PASSES"
flock "${HEAVY_LOCK:-/home/claude/work/heavy.lock}" env SCENE="$SCENE" node run3d.js $JOBS --passes "$PASSES"
flock "${HEAVY_LOCK:-/home/claude/work/heavy.lock}" env IDS="$IDS" node render.js 2 "$SLIDES" out --passes "$PASSES" --names "$NAMES"
python3 "$REPO/pipeline/pack_passes.py" "$PASSES"
if [ -n "$FINAL" ]; then mkdir -p "$FINAL"; for kv in $(echo "$NAMES" | tr ',' ' '); do cp "out${kv%%:*}.png" "$FINAL/${kv##*:}.png"; done; fi
echo "RERENDER_DONE $COMMIT -> $PASSES"
