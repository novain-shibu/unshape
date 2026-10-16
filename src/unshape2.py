"""Unshape v2: any-font decoder + real-PDF self-check (no ground truth needed).

For each PDF font we resolve a real font file (by base name, subset prefix stripped), build its syllable -> glyph table with
HarfBuzz, and decode every Malayalam glyph stretch. A stretch is VERIFIED iff shape(decoded) == observed glyph IDs.
Report per PDF: verified share of Malayalam stretches / words, and how often PyMuPDF's own text disagrees on verified words.
"""
import json, math, re, sys, time, unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf
import uharfbuzz as hb
from fontTools.ttLib import TTFont, TTCollection

from unshape import (CHILLU, CONS, MATRA, MODS, ML, SYL, VIRAMA, VOW, kbest)

HERE = Path(__file__).resolve().parent
FONT_FILES = {
    "NotoSerifMalayalam": (HERE / "fonts/v101/NotoSerifMalayalam-Regular.ttf", 0),
    "NotoSerifMalayalam-Bold": (HERE / "fonts/v101/NotoSerifMalayalam-Bold.ttf", 0),
    "Rachana": (HERE / "fonts/Rachana-Regular.ttf", 0),
    "NirmalaUI": (Path(r"C:\Windows\Fonts\Nirmala.ttc"), 0),
}
HELDOUT = [HERE / f for f in ("wiki_48.txt", "wiki_7116.txt", "wiki_4867.txt")]
_SYLFREQ = None


def syllable_freq():
    global _SYLFREQ
    if _SYLFREQ is None:
        freq = Counter()
        for f in HELDOUT:
            t = unicodedata.normalize("NFC", f.read_text(encoding="utf-8"))
            for w in ML.findall(t):
                freq.update(SYL.findall(w))
        combo = set(CONS + VOW + CHILLU)
        combo |= {c + m for c in CONS for m in MATRA + MODS + [VIRAMA]}
        combo |= {v + m for v in VOW for m in MODS}
        combo |= {a + VIRAMA + b + m for a in CONS for b in CONS for m in [""] + MATRA + [VIRAMA, "\u0D02"]}
        combo |= {ch + VIRAMA + c + m for ch in CHILLU for c in CONS for m in [""] + MATRA}
        for s in combo:
            freq[s] += 0
        _SYLFREQ = freq
    return _SYLFREQ


class FontModel:
    def __init__(self, path, index):
        self.hbfont = hb.Font(hb.Face(hb.Blob.from_file_path(str(path)), index))
        tt = TTCollection(str(path)).fonts[index] if str(path).lower().endswith(".ttc") else TTFont(str(path))
        self.cmap = {cp: tt.getGlyphID(n) for cp, n in tt.getBestCmap().items()}
        self.space = self.cmap.get(32, -99)
        self.cache = {}
        self.table = defaultdict(list)
        for s, n in syllable_freq().items():
            g = self.shape(s)
            if g and 0 not in g:
                self.table[g].append((n, s))
        for g in self.table:
            self.table[g].sort(reverse=True)
        self.maxlen = max(map(len, self.table))

    def shape(self, text):
        r = self.cache.get(text)
        if r is None:
            buf = hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            hb.shape(self.hbfont, buf, {})
            r = self.cache[text] = tuple(g.codepoint for g in buf.glyph_infos)
        return r


_MODELS = {}


def model_for(fontname):
    base = fontname.split("+", 1)[-1]
    if base not in _MODELS:
        spec = FONT_FILES.get(base)
        _MODELS[base] = FontModel(*spec) if spec and spec[0].exists() else None
    return _MODELS[base]


def is_ml(u):
    return 0x0D00 <= u <= 0x0D7F or u in (0x200C, 0x200D)


def runs_of(page):
    """Words = glyph runs split at space glyphs (per font) ; each item (gid, uni, fontname)."""
    runs, cur = [], []
    for span in page.get_texttrace():
        m = model_for(span["font"])
        for uni, gid, origin, bbox in span["chars"]:
            space = (gid == m.space) if m else (uni == 32)
            if space:
                if cur:
                    runs.append(cur)
                cur = []
            elif gid >= 0:
                cur.append((gid, uni, span["font"]))
    if cur:
        runs.append(cur)
    return runs


def decode_word(run, st):
    """Returns (text, all_ml_verified, has_ml)."""
    out, ok, has_ml, i = [], True, False, 0
    while i < len(run):
        gid, uni, fname = run[i]
        m = model_for(fname)
        trusted = m is not None and m.cmap.get(uni) == gid and not is_ml(uni)
        if m is None and not is_ml(uni):
            trusted = True  # non-Malayalam font we do not model (e.g. Times): keep producer text
        if trusted:
            out.append(chr(uni))
            i += 1
            continue
        has_ml = True
        if m is None:
            st["nofont"] += 1
            ok = False
            out.append(chr(uni) if uni else "\ufffd")
            i += 1
            continue
        j = i
        while j < len(run) and run[j][2] == fname and not (m.cmap.get(run[j][1]) == run[j][0] and not is_ml(run[j][1])):
            j += 1
        g = tuple(x[0] for x in run[i:j])
        pick = next((s for _, s in kbest(g, m.table, m.maxlen) if m.shape(s) == g), None)
        if pick is None:
            st["unverified"] += 1
            ok = False
            pick = "".join(chr(x[1]) for x in run[i:j] if x[1])
        else:
            st["verified"] += 1
        out.append(pick)
        i = j
    return "".join(out), ok, has_ml


def main():
    rows = []
    for pdf in sys.argv[1:]:
        t0 = time.time()
        doc = pymupdf.open(pdf)
        st, words, ver_words, disagree = Counter(), 0, 0, 0
        out_lines = []
        for page in doc:
            for run in runs_of(page):
                text, ok, has_ml = decode_word(run, st)
                out_lines.append(text)
                if not has_ml:
                    continue
                words += 1
                if ok:
                    ver_words += 1
                    producer = "".join(chr(u) for g, u, f in run if u)
                    if unicodedata.normalize("NFC", producer) != unicodedata.normalize("NFC", text):
                        disagree += 1
        dt = time.time() - t0
        Path(pdf).with_suffix(".unshaped.txt").write_text(" ".join(out_lines), encoding="utf-8")
        fonts = Counter(f.split("+")[-1] for p in doc for f in [x[3] for x in p.get_fonts()])
        row = {"pdf": Path(pdf).name, "pages": len(doc), "ml_words": words,
               "self_verified_words": round(ver_words / max(1, words), 4),
               "pymupdf_wrong_on_verified": round(disagree / max(1, ver_words), 4),
               "stretches": dict(st), "sec": round(dt, 2),
               "unmodelled_fonts": sorted(b for b in fonts if model_for(b) is None)}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    Path(HERE / "real_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
