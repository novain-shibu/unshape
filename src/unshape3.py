"""Unshape v3: subset-aware, version-fingerprinting pipeline.

1. Trace every glyph with pdfminer: (subset font name, char code, position, producer Unicode).
2. For each embedded subset font: read its font program, map char code -> subset glyph ID.
3. FINGERPRINT: match the subset's glyph outlines against every candidate full font in fonts/ (all versions); pick the build
   with the most exact outline matches; map subset glyph -> full-font glyph ID.
4. Decode Malayalam glyph stretches with that full font's HarfBuzz model; VERIFY by re-shaping (exact).
"""
import io, json, sys, time, unicodedata
from collections import Counter
from pathlib import Path

import pymupdf
from fontTools.ttLib import TTCollection, TTFont
from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LAParams
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import resolve1

from remap import _points, build_index, remap
from unshape import kbest
from unshape2 import FontModel, is_ml

HERE = Path(__file__).resolve().parent
CANDIDATES = sorted(p for p in (HERE / "fonts").rglob("*.ttf")) + [Path(r"C:\Windows\Fonts\Nirmala.ttc")]
_IDX, _MODEL = {}, {}
FALLBACK = {}  # basefont (with subset prefix) -> font program bytes, read with PyMuPDF


def load_full(path):
    return TTCollection(str(path)).fonts[0] if str(path).lower().endswith(".ttc") else TTFont(str(path))


def full_index(path):
    if path not in _IDX:
        f = load_full(path)
        _IDX[path] = (f, build_index(f))
    return _IDX[path]


class EmbeddedModel(FontModel):
    """HarfBuzz model built from a font program embedded in the PDF that still has its GSUB (shaping) table."""

    def __init__(self, buf):
        import uharfbuzz as hb
        from collections import defaultdict
        from unshape2 import syllable_freq
        self.hbfont = hb.Font(hb.Face(hb.Blob(buf), 0))
        tt = TTFont(io.BytesIO(buf))
        self.cmap = {cp: tt.getGlyphID(n) for cp, n in (tt.getBestCmap() or {}).items()}
        self.space = self.cmap.get(32, -99)
        self.cache = {}
        self.table = defaultdict(list)
        for s, n in syllable_freq().items():
            g = self.shape(s)
            if g and 0 not in g:
                self.table[g].append((n, s))
        for g in self.table:
            self.table[g].sort(reverse=True)
        self.maxlen = max(map(len, self.table)) if self.table else 1


class EmbeddedRef:
    def __init__(self, name, buf):
        self.name, self.buf, self.parent = name, buf, type("P", (), {"name": "embedded"})()


def model(path):
    key = id(path) if isinstance(path, EmbeddedRef) else path
    if key not in _MODEL:
        _MODEL[key] = EmbeddedModel(path.buf) if isinstance(path, EmbeddedRef) else FontModel(path, 0)
    return _MODEL[key]


class Tracer(PDFPageAggregator):
    def __init__(self, rsrc):
        super().__init__(rsrc, laparams=LAParams())
        self.glyphs = []

    def render_char(self, matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate):
        try:
            text = font.to_unichr(cid)
        except Exception:
            text = ""
        try:
            width = font.char_width(cid)
        except Exception:
            width = 1.0
        self.glyphs.append((font, cid, matrix[4], matrix[5], width, text))
        return super().render_char(matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate)


def font_program(font):
    d = getattr(font, "descriptor", None) or {}
    for k in ("FontFile2", "FontFile3", "FontFile"):
        if k in d:
            try:
                return resolve1(d[k]).get_data()
            except Exception:
                break
    return FALLBACK.get(getattr(font, "fontname", None))


class SubsetInfo:
    """code -> full-font glyph ID for one embedded subset, plus the fingerprinted full font."""

    def __init__(self, font):
        self.name = getattr(font, "fontname", "?")
        self.ok = False
        self.match = "no program"
        buf = font_program(font)
        if not buf:
            return
        try:
            sub = TTFont(io.BytesIO(buf))
        except Exception:
            return
        self.cid_font = type(font).__name__ == "PDFCIDFont"
        self._sub, self._blank = sub, {}
        self.code2gid = {}
        if not self.cid_font and "cmap" in sub:
            for t in sub["cmap"].tables:
                for code, gname in t.cmap.items():
                    c = code - 0xF000 if (t.platformID, t.platEncID) == (3, 0) and code >= 0xF000 else code
                    self.code2gid.setdefault(c, sub.getGlyphID(gname))
        # Best case: the embedded program kept its shaping table (e.g. Microsoft Print to PDF) -> decode with it directly
        cm = (sub.getBestCmap() if "cmap" in sub else None) or {}
        if "GSUB" in sub and any(0x0D00 <= c <= 0x0D7F for c in cm):
            self.ok, self.full, self.match = True, EmbeddedRef(self.name, buf), "embedded GSUB"
            self.sub2full = {i: i for i in range(len(sub.getGlyphOrder()))}
            return
        if cm and not any(0x0D00 <= c <= 0x0D7F for c in cm):
            self.match = "non-Malayalam font"  # e.g. Calibri / Times; legacy ASCII fonts are the week-3 path
            return
        # fingerprint against all candidate builds whose family name matches
        fam = self.name.split("+")[-1].split("-")[0].lower()
        best = None
        # names can be stripped (e.g. "CIDFont+F3" from Microsoft Print to PDF): then try EVERY candidate build
        named = [c for c in CANDIDATES if fam[:6] in c.name.lower().replace("-", "")
                 and self.name.lower().endswith("bold") == ("bold" in c.name.lower())]
        for cand in named or CANDIDATES:
            full, idx = full_index(cand)
            m = remap(sub, full, idx, tol=0.5)
            score = len(m)
            if best is None or score > best[0]:
                best = (score, cand, m)
        nonempty = sum(1 for g in sub.getGlyphOrder() if _points(sub, g)[0])
        if best and best[0] >= 0.9 * max(1, nonempty):
            self.ok = True
            self.full = best[1]
            self.sub2full = {sg: fg for sg, (fg, d) in best[2].items()}
            self.match = f"{best[0]}/{nonempty}"
        else:
            self.match = f"{best[0] if best else 0}/{nonempty}"

    def sub_gid(self, code):
        return code if self.cid_font else self.code2gid.get(code, code)

    def is_blank(self, code):
        """Glyph with no outline (space / ZWNJ). Blank glyphs are never matched by outline remapping."""
        g = self.sub_gid(code)
        if g not in self._blank:
            order = self._sub.getGlyphOrder()
            self._blank[g] = 0 < g < len(order) and not _points(self._sub, order[g])[0]
        return self._blank[g]

    def gid(self, code):
        g = code if self.cid_font else self.code2gid.get(code, code)
        return self.sub2full.get(g)


def load_fallback(pdf):
    FALLBACK.clear()
    doc = pymupdf.open(pdf)
    for page in doc:
        for xref, ext, typ, base, *_ in page.get_fonts(full=True):
            if base not in FALLBACK:
                try:
                    FALLBACK[base] = doc.extract_font(xref)[3] or None
                except Exception:
                    FALLBACK[base] = None


def trace(pdf):
    load_fallback(pdf)
    rsrc = PDFResourceManager()
    dev = Tracer(rsrc)
    interp = PDFPageInterpreter(rsrc, dev)
    pages = []
    with open(pdf, "rb") as fh:
        for page in PDFPage.get_pages(fh):
            dev.glyphs = []
            interp.process_page(page)
            pages.append(dev.glyphs)
    return pages


def main():
    rows = []
    for pdf in sys.argv[1:]:
        t0 = time.time()
        subsets, st = {}, Counter()
        words = ver = 0
        out, pages_words = [], []
        for glyphs in trace(pdf):
            run = []
            page_words = []
            last = None

            def flush():
                nonlocal words, ver, run
                if not run:
                    return
                text, ok, has_ml = decode(run, st)
                out.append(text)
                page_words.append([text, bool(ok), bool(has_ml)])
                if has_ml:
                    words += 1
                    ver += ok
                run = []

            for font, code, x, y, width, text in glyphs:
                # line wrap without a space glyph (Chromium drops it): a carriage return ends the word
                if last is not None and x < last[0] - 20 and abs(y - last[1]) > 8:
                    flush()
                last = (x, y)
                key = id(font)
                if key not in subsets:
                    subsets[key] = SubsetInfo(font)
                si = subsets[key]
                # Split ONLY on the font's space GLYPH (skeptic fix: Word's ToUnicode labels vowel signs as " ";
                # splitting on that text dropped glyphs while both halves still "verified"). Producer text is used
                # only for fonts we do not model. A ZERO-width space glyph is a shaper-rendered ZWNJ/ZWJ: keep in word.
                if si.ok:
                    # space = the font's space glyph, or any BLANK glyph with width (blank glyphs carry no outline,
                    # so the outline remap cannot map them); vowel signs always have outlines
                    is_space = (si.gid(code) is not None and si.gid(code) == model(si.full).space) or si.is_blank(code)
                    if is_space and width < 0.05:
                        run.append((si, -2, "\u200c"))
                        continue
                else:
                    is_space = text == " "
                if is_space:
                    flush()
                    continue
                run.append((si, code, text))
            flush()
            pages_words.append(page_words)
        Path(pdf).with_suffix(".v3words.json").write_text(json.dumps(pages_words, ensure_ascii=False), encoding="utf-8")
        row = {"pdf": Path(pdf).name, "ml_words": words, "self_verified": round(ver / max(1, words), 4),
               "stretches": dict(st), "sec": round(time.time() - t0, 2),
               "subsets": {s.name: (s.match, s.full.parent.name + "/" + s.full.name if s.ok else None)
                           for s in subsets.values() if any(k in s.name for k in ("Malayalam", "Rachana", "Nirmala", "Meera", "Manjari", "Anjali", "Kartika"))}}
        Path(pdf).with_suffix(".unshaped3.txt").write_text(" ".join(out), encoding="utf-8")
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    # never overwrite earlier raw logs (skeptic fix): one file per invocation + an append-only log
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (HERE / "logs").mkdir(exist_ok=True)
    (HERE / "logs" / f"v3_{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(HERE / "logs" / "v3_all.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"run": stamp, **r}, ensure_ascii=False) + "\n")


def decode(run, st):
    """run: list of (SubsetInfo, code, producer_text). Returns (text, all_ml_verified, has_ml)."""
    out, ok, has_ml, i = [], True, False, 0

    def trusted(item):
        si, code, text = item
        if not text or any(is_ml(ord(c)) for c in text):
            return False
        if not si.ok:
            return True  # a font we do not model (e.g. Times): keep producer text
        m = model(si.full)
        return len(text) == 1 and m.cmap.get(ord(text)) == si.gid(code)

    while i < len(run):
        if run[i][1] == -2:  # zero-width space glyph = ZWNJ (ZWJ indistinguishable by rendering)
            out.append("\u200c")
            i += 1
            continue
        if trusted(run[i]):
            out.append(run[i][2])
            i += 1
            continue
        has_ml = True
        si = run[i][0]
        if not si.ok:
            st["nofont"] += 1
            ok = False
            out.append(run[i][2] or "�")
            i += 1
            continue
        j = i
        while j < len(run) and run[j][0] is si and run[j][1] != -2 and not trusted(run[j]):
            j += 1
        g = tuple(si.gid(c) for _, c, _ in run[i:j])
        m = model(si.full)
        pick = None
        if None not in g:
            pick = next((s for _, s in kbest(g, m.table, m.maxlen) if m.shape(s) == g), None)
        if pick is None:
            st["unverified"] += 1
            ok = False
            pick = "".join(t for _, _, t in run[i:j])
        else:
            st["verified"] += 1
        out.append(pick)
        i = j
    return "".join(out), ok, has_ml


if __name__ == "__main__":
    main()
