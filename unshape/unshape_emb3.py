# Copyright (c) 2026 Novain Shibu. AGPL-3.0-or-later, or a commercial licence (see COMMERCIAL.md).
"""Unshape-emb3: pooling fixes on top of the FROZEN unshape_emb.py (FINDINGS sec 29). The frozen file is not edited.
Diagnosis (diag_pool*.py, 2026-10-18):
  1. SCRIPT FLIP: EmbFace.block = majority Unicode block of the MERGED cmap. For a multi-script font (Arial Unicode MS)
     the pool adds other scripts' code points (a Gurmukhi doc got 300+ Devanagari entries) -> block flips -> decoded
     with the wrong script's inventory (195/203 verified -> 0/147).
     Fix: the script is decided by the doc's OWN cmap (pool only as fallback when the doc has no complex-script entry),
     and the pool may only fill code points of that script block (+ dandas U+0964/5, ZWNJ/ZWJ, non-complex code points).
  2. CRASH: when a thin subset cmap shapes no inventory syllable, unshape_multi4.inventory() raises (max of empty).
     The saturation harness skipped such docs -> its k=0 average covered only the easy docs (composition artefact; the
     "pooling hurts Mangal 6.9" curve). Fix: such runs fall back to the producer text and count as unverified.
  (The doc's own pairs were never overwritten by the pool: pool fills only code points the doc lacks.)
Usage (in invent7): python unshape_emb3.py originals/*.pdf
"""
import collections, io, sys

_args, sys.argv = sys.argv[1:], sys.argv[:1]
import unshape_emb as E
from fontTools.ttLib import TTFont

ALWAYS = {0x0964, 0x0965, 0x200C, 0x200D}


def block_of(cps):
    b = collections.Counter(cp & ~0x7F for cp in cps if 0x0900 <= cp < 0x0E00 and cp not in ALWAYS)
    return b.most_common(1)[0][0] if b else None


def embedded_faces(doc):
    copies = collections.defaultdict(list)
    seen = set()
    for pno in range(len(doc)):
        for xref, ext, typ, base, *_ in doc.get_page_fonts(pno, full=True):
            if ext != "ttf" or xref in seen:
                continue
            seen.add(xref)
            try:
                buf = doc.extract_font(xref)[3]
                tt = TTFont(io.BytesIO(buf))
            except Exception:
                continue
            if "GSUB" in tt:
                copies[base.split("+", 1)[-1]].append((buf, tt))
    faces = {}
    for name, cs in copies.items():
        buf, tt = max(cs, key=lambda c: len(c[1].getBestCmap() or {}))
        order = tt.getGlyphOrder()
        merged, bad = {}, set()
        for _, t2 in cs:
            if t2.getGlyphOrder() != order:
                continue
            for cp, gname in (t2.getBestCmap() or {}).items():
                if merged.get(cp, gname) != gname:
                    bad.add(cp)
                merged[cp] = gname
        for cp in bad:
            merged.pop(cp)
        pool = E.POOL.get(E.build_key(name, tt), {})
        blk = block_of(merged) or block_of(pool)
        for cp, gid in pool.items():
            in_script = blk is not None and (blk <= cp < blk + 0x80 or cp in ALWAYS or not 0x0900 <= cp < 0x0E00)
            if in_script and cp not in merged and cp not in bad and gid < len(order):
                merged[cp] = order[gid]
        if len(merged) > len(tt.getBestCmap() or {}):
            for sub in tt["cmap"].tables:
                if sub.isUnicode():
                    sub.cmap = {cp: g for cp, g in merged.items() if sub.format != 4 or cp <= 0xFFFF}
            out = io.BytesIO(); tt.save(out); buf = out.getvalue()
        f = E.EmbFace(buf)
        f.block = blk if blk is not None else f.block
        if f.block in E.SCRIPT:
            faces[name] = f
    return faces


_decode = E.decode


def decode(run, fc, st):
    try:
        return _decode(run, fc, st)
    except ValueError:  # no shapeable inventory syllable for this (thin) cmap
        st["unverified"] += 1
        st["no_inventory"] += 1
        return "".join(chr(u) for _, u in run if u > 0)


E.embedded_faces = embedded_faces
E.decode = decode

if __name__ == "__main__":
    sys.argv = sys.argv[:1] + _args
    E.main()
