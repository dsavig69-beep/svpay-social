"""Checks that need a tool this machine cannot install. Each returns na with "tool not installed" and never fails a
post for that reason (ruling 7). When the tool becomes available, replace the stub with the method in criteria.json.

  pyiqa (needs PyTorch, too large for the shared 8 GB builder):       R-M01 BRISQUE, R-M02 NIQE, R-M03 MUSIQ/TOPIQ
  PyMeshLab (no wheel installed; mesh repair and intersection tests): R-D02, R-D14, R-D15
  CAMBI (Netflix libvmaf, not packaged here):                        R-B01
  CLIP and other learned image models (PyTorch):                     R-K10, R-K11, R-K12, QB28, QB29
  EasyOCR (PyTorch):                                                  T-O02
  Google Cloud Vision SafeSearch (a paid API):                        F-GB12
  veraPDF (Java):                                                     QA25
  c2patool (Rust binary):                                             MC-R06
  logo and seal detectors (trained models):                           MC-N01
"""
from . import check
from .core import na, ok

STUBS = {
    "R-M01": "pyiqa (BRISQUE)", "R-M02": "pyiqa (NIQE)", "R-M03": "pyiqa (MUSIQ, TOPIQ, CLIP-IQA)",
    "R-D02": "PyMeshLab (self-intersection test)", "R-D14": "PyMeshLab (watertight and winding test)", "R-D15": "PyMeshLab (outward normals, volume)",
    "R-B01": "CAMBI (libvmaf)", "R-K11": "CLIP (AI-look probe)", "R-K12": "an AI-image detector",
    "QB28": "CLIP image embeddings (brand centroid)", "QB29": "CLIP image embeddings (similarity ceiling)", "T-O02": "EasyOCR (second OCR engine)",
    "F-GB12": "Google Cloud Vision SafeSearch", "QA25": "veraPDF (PDF/UA)", "MC-R06": "c2patool (C2PA validation)",
    "MC-N01": "a government-seal and badge detector",
}


def _stub(tool):
    def f(post):
        return na(f"tool not installed: {tool}")
    return f


for _cid, _tool in STUBS.items():
    check(_cid, scope="post")(_stub(_tool))


@check("R-A03", scope="post")
def supersampled_reference(post):
    return na("needs a supersampled reference pass (run3d.js does not write one yet)")


@check("R-M04", scope="post")
def laplacian_band(post):
    return na("needs the supersampled reference pass of R-A03")


@check("R-S03", scope="post")
def shadow_footprint(post):
    return na("needs a shadows-off beauty pass (run3d.js does not write one yet)")


@check("R-S04", scope="post")
def shadow_acne(post):
    return na("needs a shadows-off beauty pass (run3d.js does not write one yet)")


@check("R-S10", scope="post")
def contact_hardening(post):
    return na("needs per-shadow contact geometry from the scene")


@check("R-X06", scope="post")
def calibration_chip(post):
    return na("needs the offscreen calibration-chip render")


@check("R-G04", scope="post")
def draw_order(post):
    return na("needs 3 beauty renders with shuffled draw order (run3d.js does not write them yet)")


@check("R-G11", scope="post")
def css_blur_samples(post):
    return na("needs the hero render in the passes folder")


@check("R-F04", scope="post")
def convergence(post):
    return na("no progressive or path-traced renders in the pipeline")


@check("R-N04", scope="post")
def uv_seams(post):
    return na("needs a UV pass")


@check("QM04", scope="post")
def input_ai_provenance(post):
    return na("needs the input asset list (provenance sidecar)")


@check("QM07", scope="post")
def photo_rotation(post):
    return na("no photos in the package")


@check("MC-M01", scope="image")
def network_lookalikes(post, sl):
    """Overlapping red and yellow discs (the Mastercard look) on the image."""
    import numpy as np
    import cv2
    hsv = cv2.cvtColor(sl.final[::2, ::2], cv2.COLOR_RGB2HSV)
    red = ((hsv[..., 0] < 8) | (hsv[..., 0] > 172)) & (hsv[..., 1] > 150) & (hsv[..., 2] > 120)
    yel = (hsv[..., 0] > 18) & (hsv[..., 0] < 32) & (hsv[..., 1] > 150) & (hsv[..., 2] > 150)
    def discs(m):
        cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in cs:
            a = cv2.contourArea(c)
            if a < 200:
                continue
            (x, y), r = cv2.minEnclosingCircle(c)
            if a / (np.pi * r * r) > 0.6:
                out.append((x, y, r))
        return out
    pairs = [(a, b) for a in discs(red) for b in discs(yel) if abs(a[2] - b[2]) / max(a[2], b[2]) < 0.3 and np.hypot(a[0] - b[0], a[1] - b[1]) < 1.6 * max(a[2], b[2])]
    return ok(not pairs, f"{len(pairs)} red and yellow overlapping discs")
