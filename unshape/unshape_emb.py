# Copyright (c) 2026 Novain Shibu. AGPL-3.0-or-later, or a commercial licence (see COMMERCIAL.md).
"""Unshape-emb: decode complex-script PDFs with the font EMBEDDED in the PDF itself (no installed font, no catalogue).
Many producers (Word 2007-2013, iText, iLovePDF) embed TrueType subsets that keep the full glyph order, cmap and GSUB.
HarfBuzz is run on those embedded bytes, so the answer key is the document's own shaping: a stretch is render-verified iff
HarfBuzz(embedded font, decoded text) == the PDF's glyph IDs. Decoder = unshape_multi4 (normalised keys, k-best search,
5-gram tie-break), inventory from word_test/train_<lang>.txt. No ground truth for real PDFs -> report render-verified share
and the orthographic-invalid rate (validity.py) of the PDF's own text vs ours.
Usage (in invent7): python unshape_emb.py originals/*.pdf
"""
import collections, io, json, os, sys, time, unicodedata
from pathlib import Path

import pymupdf
import uharfbuzz as hb
from fontTools.ttLib import TTFont

sys.path.insert(0, str(Path(__file__).parent))
import unshape_multi4 as U
from validity import scan_text

SCRIPT = {0x0900: ("hi", "Deva"), 0x0980: ("bn", "Beng"), 0x0A00: ("pa", "Guru"), 0x0A80: ("gu", "Gujr"),
          0x0B00: ("or", "Orya"), 0x0B80: ("ta", "Taml"), 0x0C00: ("te", "Telu"), 0x0C80: ("kn", "Knda"),
          0x0D00: ("ml", "Mlym"), 0x0D80: ("si", "Sinh")}


class EmbFace:
    """Same interface as unshape_multi4.Face, built from embedded font bytes."""
    def __init__(self, buf):
        tt = TTFont(io.BytesIO(buf))
        self.hbf = hb.Font(hb.Face(hb.Blob(buf)))
        self.cmap = {cp: tt.getGlyphID(n) for cp, n in tt.getBestCmap().items()}
        self.space = self.cmap.get(32, -99)
        self.norm = U.single_classes(tt) if "GSUB" in tt else {}
        self.rules, self.cache, self.tables = {}, {}, {}
        blocks = collections.Counter(cp & ~0x7F for cp in self.cmap if 0x0900 <= cp < 0x0E00)
        self.block = blocks.most_common(1)[0][0] if blocks else None

    shape = U.Face.shape
    key = U.Face.key

    def wshape(self, t, lang):
        return self.shape(t)


def embedded_faces(doc):
    """basefont name (without subset prefix) -> EmbFace. Word writes one subset copy per page, each with a cmap of only
    that page's characters but the SAME full glyph order and GSUB -> merge the cmaps of all copies with equal glyph order
    (conflicting code point -> glyph entries are dropped) into the largest copy."""
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
        # cross-document pool (same font build = same name, glyph count, revision): fill code points this PDF lacks
        for cp, gid in POOL.get(build_key(name, tt), {}).items():
            if cp not in merged and gid < len(order):
                merged[cp] = order[gid]
        for cp in bad:
            merged.pop(cp)
        if len(merged) > len(tt.getBestCmap() or {}):
            for sub in tt["cmap"].tables:
                if sub.isUnicode():
                    sub.cmap = {cp: g for cp, g in merged.items() if sub.format != 4 or cp <= 0xFFFF}
            out = io.BytesIO(); tt.save(out); buf = out.getvalue()
        f = EmbFace(buf)
        if f.block in SCRIPT:
            faces[name] = f
    return faces


POOL = {}


def build_key(name, tt):
    return (name, len(tt.getGlyphOrder()), round(tt["head"].fontRevision, 3))


def build_pool(paths):
    """Pool cmap pairs over ALL given PDFs per font build; pairs that conflict anywhere are dropped."""
    pool, bad = collections.defaultdict(dict), collections.defaultdict(set)
    for p in paths:
        doc = pymupdf.open(p)
        for pno in range(len(doc)):
            for xref, ext, typ, base, *_ in doc.get_page_fonts(pno, full=True):
                if ext != "ttf":
                    continue
                try:
                    tt = TTFont(io.BytesIO(doc.extract_font(xref)[3]))
                except Exception:
                    continue
                if "GSUB" not in tt:
                    continue
                k = build_key(base.split("+", 1)[-1], tt)
                for cp, gname in (tt.getBestCmap() or {}).items():
                    gid = tt.getGlyphID(gname)
                    if pool[k].get(cp, gid) != gid:
                        bad[k].add(cp)
                    pool[k][cp] = gid
    for k in bad:
        for cp in bad[k]:
            pool[k].pop(cp, None)
    POOL.update(pool)
    return {k: len(v) for k, v in pool.items()}


def norm_name(s):
    return s.split("+", 1)[-1].replace("-", ",").replace(" ", "").lower()


def runs(doc, faces):
    fmap = {norm_name(k): (k, v) for k, v in faces.items()}
    chars = []
    for page in doc:
        for span in page.get_texttrace():
            hit = fmap.get(norm_name(span["font"]))
            for uni, gid, origin, bbox in span["chars"]:
                if gid >= 0:
                    chars.append((hit, span["size"], gid, uni, origin))
    out, cur, curf, cur_y = [], [], None, None
    for i, (hit, size, gid, uni, origin) in enumerate(chars):
        if hit is None:
            if cur:
                out.append((curf, cur)); cur = []
            curf = None
            continue
        fc = hit[1]
        if cur and fc is not curf:
            out.append((curf, cur)); cur = []
        if cur and abs(origin[1] - cur_y) > 1.2 * size:
            out.append((curf, cur)); cur = []
        curf, cur_y = fc, origin[1]
        if gid == fc.space:
            nxt = chars[i + 1] if i + 1 < len(chars) else None
            zero = (nxt is not None and nxt[0] is hit and abs(nxt[4][1] - origin[1]) < 0.5 * size
                    and abs(nxt[4][0] - origin[0]) < 0.05 * size)
            if zero and cur:
                cur.append((gid, 0)); continue
            if cur:
                out.append((curf, cur)); cur = []
            continue
        cur.append((gid, uni))
    if cur:
        out.append((curf, cur))
    return out


def decode(run, fc, st):
    lang = SCRIPT[fc.block][0]
    table, maxlen = U.inventory(lang, fc)
    lo = fc.block

    def trusted(g, u):
        if fc.cmap.get(u) != g:
            return False
        return not (lo <= u < lo + 0x80) or unicodedata.category(chr(u))[0] in "PNZ"
    out, i = [], 0
    while i < len(run):
        g, u = run[i]
        if trusted(g, u):
            out.append(chr(u)); i += 1; continue
        j = i
        while j < len(run) and not trusted(*run[j]):
            j += 1
        gl = tuple(x[0] for x in run[i:j])
        cands = U.kbest(fc.key(gl), table, maxlen)
        ver = []
        for _, s_ in cands:
            if fc.shape(s_) == gl:
                n = unicodedata.normalize("NFC", s_)
                if n not in ver:
                    ver.append(n)
        if ver:
            st["render_verified"] += 1
            st["render_ambiguous"] += len(ver) > 1
            pick = max(ver, key=U.lm(lang).score) if len(ver) > 1 else ver[0]
        else:
            st["unverified"] += 1
            pick = "".join(chr(x[1]) for x in run[i:j] if x[1] > 0)
        out.append(pick); i = j
    return unicodedata.normalize("NFC", "".join(out))


def main():
    rows = []
    if os.environ.get("UNSHAPE_POOL", "1") == "1":
        sizes = build_pool(sys.argv[1:])
        print(json.dumps({"pool": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in sizes.items()}}, ensure_ascii=False), flush=True)
    for p in sys.argv[1:]:
        t0 = time.time()
        doc = pymupdf.open(p)
        faces = embedded_faces(doc)
        if not faces:
            print(json.dumps({"pdf": Path(p).name, "skip": "no embedded complex-script font with GSUB"})); continue
        st = collections.Counter(); dec = collections.defaultdict(list)
        for fc, r in runs(doc, faces):
            if fc is not None:
                dec[SCRIPT[fc.block][1]].append(decode(r, fc, st))
        raw = "".join(pg.get_text() for pg in doc)
        rv = st["render_verified"]
        row = {"pdf": Path(p).name, "producer": (doc.metadata or {}).get("producer", "")[:30],
               "fonts": sorted(faces), "stretches": dict(st),
               "render_verified": round(rv / max(1, rv + st["unverified"]), 4), "sec": round(time.time() - t0, 1)}
        for script, parts in dec.items():
            w1, b1 = scan_text(raw, script); w2, b2 = scan_text(" ".join(parts), script)
            row[f"invalid_{script}"] = [round(b1 / max(1, w1), 4), round(b2 / max(1, w2), 4), w1, w2]
            Path(p).with_suffix(f".emb_{script}.txt").write_text(" ".join(parts), encoding="utf-8")
        rows.append(row); print(json.dumps(row, ensure_ascii=False), flush=True)
    Path(f"unshape_emb_{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
