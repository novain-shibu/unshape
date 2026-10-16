"""Spark: exact Malayalam text recovery from born-digital PDFs by INVERTING OpenType shaping.

Idea: the PDF stores glyph IDs (visual order, after GSUB ligatures / reordering). If we know the font, HarfBuzz
gives the forward map text -> glyphs. We search for the text whose shaping reproduces the observed glyph run
exactly. The forward shaper is the answer key: a decoded word is VERIFIED iff shape(word) == observed glyphs.

Inventory (no test-document text used): syllables from HELD-OUT Wikipedia articles + combinatorial syllables.
Decoding: DP segmentation of each glyph run into syllable glyph-sequences, k-best by syllable frequency,
then exact verification by re-shaping the whole word.
"""
import heapq, json, math, re, sys, time, unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf
import uharfbuzz as hb

from baseline_extract import lev, norm, word_recall
from fontTools.ttLib import TTCollection

from probe_gids import TTC, face_index

# reverse of the real font's cmap: glyph id -> code point (nominal glyphs only)
# v1 spark only (Windows Nirmala UI); the general pipeline (unshape3.py) does not need it
try:
    _f = TTCollection(TTC).fonts[face_index()]
    CMAP = {cp: _f.getGlyphID(name) for cp, name in _f.getBestCmap().items()}
    SPACE_GID = CMAP[32]
except (OSError, SystemExit):
    CMAP, SPACE_GID = {}, -99

CONS = [chr(c) for c in range(0x0D15, 0x0D3B)]
VOW = [chr(c) for c in list(range(0x0D05, 0x0D0D)) + list(range(0x0D0E, 0x0D11)) + list(range(0x0D12, 0x0D15)) + [0x0D60, 0x0D61]]
MATRA = [chr(c) for c in list(range(0x0D3E, 0x0D45)) + [0x0D46, 0x0D47, 0x0D48, 0x0D4A, 0x0D4B, 0x0D4C, 0x0D57, 0x0D62, 0x0D63]]
MODS = ["ം", "ഃ", "ഁ"]
VIRAMA = "്"
CHILLU = [chr(c) for c in list(range(0x0D7A, 0x0D80)) + [0x0D54, 0x0D55, 0x0D56]]
ML = re.compile(r"[ഀ-ൿ\u200c\u200d]+")
# orthographic syllable: (consonant (virama zwj? consonant)* | vowel | chillu) + dependent signs
SYL = re.compile(r"(?:[ൺ-ൿ]്[ക-ഺ]|[ക-ഺ](?:്[\u200c\u200d]?[ക-ഺ])*|[അ-ഔൠൡ]|[ൺ-ൿൔ-ൖ])"
                 r"[ാ-ൌൗൢൣംഃഁ്\u200c\u200d]*|.", re.S)


class Shaper:
    def __init__(self):
        self.font = hb.Font(hb.Face(hb.Blob.from_file_path(TTC), face_index()))
        self.cache = {}

    def __call__(self, text):
        r = self.cache.get(text)
        if r is None:
            buf = hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            hb.shape(self.font, buf, {})
            r = self.cache[text] = tuple(g.codepoint for g in buf.glyph_infos)
        return r


def build_inventory(shape, heldout_files):
    freq = Counter()
    for f in heldout_files:
        t = unicodedata.normalize("NFC", Path(f).read_text(encoding="utf-8"))
        for w in ML.findall(t):
            freq.update(SYL.findall(w))
    combo = set(CONS + VOW + CHILLU)
    combo |= {c + m for c in CONS for m in MATRA + MODS + [VIRAMA]}
    combo |= {v + m for v in VOW for m in MODS}
    combo |= {ch + VIRAMA + c + m for ch in CHILLU for c in CONS for m in [""] + MATRA}
    combo |= {a + VIRAMA + b + m for a in CONS for b in CONS for m in [""] + MATRA + [VIRAMA, "ം"]}
    for s in combo:
        freq[s] += 0  # present, zero corpus count
    table = defaultdict(list)
    for s, n in freq.items():
        g = shape(s)
        if g and 0 not in g:
            table[g].append((n, s))
    for g in table:
        table[g].sort(reverse=True)
    maxlen = max(map(len, table))
    return table, maxlen, len(freq)


def kbest(glyphs, table, maxlen, k=20):
    """k best segmentations by sum of -log(freq+1) penalties (lower = better)."""
    n = len(glyphs)
    best = [[] for _ in range(n + 1)]  # list of (cost, string)
    best[0] = [(0.0, "")]
    for i in range(n):
        if not best[i]:
            continue
        for L in range(1, min(maxlen, n - i) + 1):
            cands = table.get(tuple(glyphs[i:i + L]))
            if not cands:
                continue
            for cnt, s in cands[:4]:
                c = -math.log(cnt + 1) + 12.0  # per-syllable cost: fewer, commoner syllables win
                for bc, bs in best[i]:
                    best[i + L].append((bc + c, bs + s))
            best[i + L] = heapq.nsmallest(k, best[i + L])
    return best[n]


def glyph_runs(pdf):
    """Per page, the glyph stream in content order, split into runs at spaces / gaps / line changes."""
    doc = pymupdf.open(pdf)
    runs, soft = [], set()
    for page in doc:
        cur, last = [], None
        for span in page.get_texttrace():
            size = span["size"]
            for uni, gid, origin, bbox in span["chars"]:
                # split on the font's space GLYPH (ToUnicode can lie: Word maps the u-sign to ' '). A ZERO-WIDTH space
                # glyph is how shapers render default-ignorables (ZWNJ/ZWJ): keep it in the word as marker -2.
                zw = gid == SPACE_GID and (bbox[2] - bbox[0]) < 0.05 * size
                if zw:
                    gid = -2
                brk = gid == SPACE_GID
                # A line change without a space glyph is AMBIGUOUS: Chromium drops the space at wraps, and Chromium
                # and Word also wrap Malayalam inside words (after virama / ZWNJ). Mark it soft; decide later.
                if not brk and last is not None and cur and abs(origin[1] - last[1]) > 1.2 * size:
                    runs.append(cur)
                    soft.add(len(runs) - 1)
                    cur = []
                if brk and cur:
                    runs.append(cur)
                    cur = []
                # gid -1 = extra code points PyMuPDF attaches to a ligature glyph (ToUnicode 1->n); not a glyph
                if gid != SPACE_GID and (gid >= 0 or gid == -2):
                    cur.append((gid, uni, span["font"]))
                last = (origin[0], origin[1], bbox[2])
        if cur:
            runs.append(cur)
    return runs, soft


def is_text(gid, u):
    """Trust the producer's Unicode only for non-Malayalam glyphs whose code point the REAL font maps to this glyph."""
    return CMAP.get(u) == gid and not (0x0D00 <= u <= 0x0D7F or u in (0x200C, 0x200D))


def decode_run(run, shape, table, maxlen, stats):
    gids = tuple(g for g, u, f in run)
    if all(is_text(g, u) for g, u, f in run):
        stats["ascii"] += 1
        return "".join(chr(u) for g, u, f in run)  # Latin/digits: trust the font's own map
    # split into Malayalam glyph stretches vs ASCII stretches (ASCII glyphs keep their Unicode)
    out, i = [], 0
    while i < len(run):
        if run[i][0] == -2:
            out.append("\u200c")  # ZWNJ (explicit virama); ZWJ is indistinguishable here
            i += 1
            continue
        if is_text(run[i][0], run[i][1]):
            out.append(chr(run[i][1]))
            i += 1
            continue
        j = i
        while j < len(run) and run[j][0] != -2 and not is_text(run[j][0], run[j][1]):
            j += 1
        g = tuple(x[0] for x in run[i:j])
        cands = kbest(g, table, maxlen)
        pick = None
        for _, s in cands:
            if shape(s) == g:
                pick = s
                break
        if pick is not None:
            stats["verified"] += 1
        elif cands:
            pick = cands[0][1]
            stats["unverified"] += 1
        else:
            pick = "".join(chr(x[1]) for x in run[i:j] if x[1])
            stats["nocover"] += 1
        out.append(pick)
        i = j
    return "".join(out)


def main():
    pdf, gtf = sys.argv[1], sys.argv[2]
    heldout = sys.argv[3].split(",")
    t0 = time.time()
    shape = Shaper()
    table, maxlen, nsyl = build_inventory(shape, heldout)
    t1 = time.time()
    runs, soft = glyph_runs(pdf)
    stats = Counter()
    dec = [decode_run(r, shape, table, maxlen, stats) for r in runs]
    # resolve soft breaks with a HELD-OUT word lexicon (the only non-exact step)
    lex = Counter()
    for f in heldout:
        lex.update(unicodedata.normalize("NFC", Path(f).read_text(encoding="utf-8")).split())
    words, i = [], 0
    while i < len(dec):
        w = dec[i]
        while i in soft and i + 1 < len(dec):
            right = dec[i + 1]
            j_score = lex[w + right]
            s_score = min(lex[w], lex[right])
            join = j_score > s_score
            if not join:
                break
            w, i = w + right, i + 1
            stats["soft_joined"] += 1
        words.append(w)
        i += 1
    stats["soft_breaks"] = len(soft)
    t2 = time.time()
    got = " ".join(words)
    gt = Path(gtf).read_text(encoding="utf-8")
    g, h = norm(gt), norm(got)
    res = {
        "pdf": pdf, "inventory_syllables": nsyl, "glyph_keys": len(table), "maxlen": maxlen,
        "build_s": round(t1 - t0, 2), "decode_s": round(t2 - t1, 3), "runs": len(runs),
        "stretches": dict(stats), "CER": lev(g, h) / len(g), "word_recall": word_recall(g, h),
    }
    print(json.dumps(res, ensure_ascii=False, default=float))
    print("gt :", g[:120])
    print("got:", h[:120])
    Path(Path(pdf).stem + ".unshaped.txt").write_text(got, encoding="utf-8")


if __name__ == "__main__":
    main()
