# Copyright (c) 2026 Novain Shibu. AGPL-3.0-or-later, or a commercial licence (see COMMERCIAL.md).
"""Unshape-emb4 = unshape_emb3 (pooling fixes, sec 29) + cmap inference checked against ground truth (sec 30):
  - interpolation between known pairs with equal code point -> glyph offset (99.5% precision on hidden real entries);
  - virama from half/haln ligatures whose FIRST glyph is a known glyph of this script (131 ok / 0 bad / 104 abstain);
  - NO producer-text harvest (56% precision -> unsafe).
Inference runs after pooling (so the pool's real entries come first), only for code points still missing.
Usage (in invent7): python unshape_emb4.py originals/*.pdf
"""
import collections, io, sys, unicodedata

_args, sys.argv = sys.argv[1:], sys.argv[:1]
import unshape_emb as E
import unshape_emb3 as E3
from fontTools.ttLib import TTFont

VIR = {0x0900: 0x094D, 0x0980: 0x09CD, 0x0A00: 0x0A4D, 0x0A80: 0x0ACD, 0x0B00: 0x0B4D, 0x0B80: 0x0BCD,
       0x0C00: 0x0C4D, 0x0C80: 0x0CCD, 0x0D00: 0x0D4D, 0x0D80: 0x0DCA}


def infer(tt, known, blk):
    order = tt.getGlyphOrder()
    have_g = set(known.values())
    add = {}
    v = VIR.get(blk)
    if v is not None and v not in known and "GSUB" in tt:
        blk_g = {order[g] for cp, g in known.items() if blk <= cp < blk + 0x80 and g < len(order)}
        comp = collections.Counter()
        gs = tt["GSUB"].table
        hl = {li for fr in gs.FeatureList.FeatureRecord if fr.FeatureTag in ("half", "haln")
              for li in fr.Feature.LookupListIndex}
        for li in hl:
            lk = gs.LookupList.Lookup[li]
            for st in lk.SubTable:
                typ = lk.LookupType
                if typ == 7:
                    st, typ = st.ExtSubTable, st.ExtensionLookupType
                if typ == 4:
                    for first, ligs in st.ligatures.items():
                        if first in blk_g:
                            comp.update(lig.Component[0] for lig in ligs if len(lig.Component) == 1)
        if comp:
            gname, n = comp.most_common(1)[0]
            g = tt.getGlyphID(gname)
            if n >= 3 and g not in have_g:
                add[v] = g
    kn = sorted({**{u: g for u, g in known.items() if blk <= u < blk + 0x80}, **add}.items())
    used = have_g | set(add.values())
    for (c1, g1), (c2, g2) in zip(kn, kn[1:]):
        if c2 - c1 > 1 and g2 - g1 == c2 - c1:
            for cp in range(c1 + 1, c2):
                g = g1 + (cp - c1)
                if cp not in add and unicodedata.name(chr(cp), "") and g not in used and g < len(order):
                    add[cp] = g; used.add(g)
    return add


def embedded_faces(doc):
    faces = E3.embedded_faces(doc)
    for name, fc in list(faces.items()):
        if fc.block is None:
            continue
        tt = TTFont(io.BytesIO(fc._buf))
        add = infer(tt, dict(fc.cmap), fc.block)
        if not add:
            continue
        order = tt.getGlyphOrder()
        for sub in tt["cmap"].tables:
            if sub.isUnicode():
                sub.cmap.update({u: order[g] for u, g in add.items()})
        b = io.BytesIO(); tt.save(b)
        nf = E.EmbFace(b.getvalue()); nf.block = fc.block; nf.added = len(add)
        faces[name] = nf
    return faces


_init = E.EmbFace.__init__


def _init_keep(self, buf):
    _init(self, buf); self._buf = buf


E.EmbFace.__init__ = _init_keep
E.embedded_faces = embedded_faces

if __name__ == "__main__":
    sys.argv = sys.argv[:1] + _args
    E.main()
