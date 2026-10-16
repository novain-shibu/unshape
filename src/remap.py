"""Map glyphs of an embedded, renumbered subset font back to glyph IDs of a full reference font by OUTLINE matching.

Exact key: identical point coordinates + contour ends (after upem scaling). Fuzzy fallback: same contour structure and
mean point distance < tol (font versions often re-hint or nudge a few points). Returns {subset_gid: (full_gid, dist)}.
"""
import io
import math

from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import DecomposingRecordingPen


def _points(font, gname):
    gs = font.getGlyphSet()
    pen = DecomposingRecordingPen(gs)
    gs[gname].draw(pen)
    pts, ends = [], []
    for op, args in pen.value:
        pts.extend(a for a in args if a is not None)
        if op in ("closePath", "endPath"):
            ends.append(len(pts))
    s = 1000.0 / font["head"].unitsPerEm
    return [(x * s, y * s) for x, y in pts], tuple(ends)


def build_index(full):
    idx = {}
    for i, g in enumerate(full.getGlyphOrder()):
        pts, ends = _points(full, g)
        if pts:
            idx.setdefault((len(pts), ends), []).append((i, pts))
    return idx


def remap(sub_bytes, full, idx=None, tol=6.0):
    sub = TTFont(io.BytesIO(sub_bytes)) if isinstance(sub_bytes, (bytes, bytearray)) else sub_bytes
    idx = idx or build_index(full)
    out = {}
    for sg, g in enumerate(sub.getGlyphOrder()):
        pts, ends = _points(sub, g)
        if not pts:
            continue
        best = None
        for fg, fpts in idx.get((len(pts), ends), []):
            d = sum(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(pts, fpts)) / len(pts)
            if best is None or d < best[1]:
                best = (fg, d)
        if best and best[1] <= tol:
            out[sg] = best
    return out
