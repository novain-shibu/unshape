# Copyright (c) 2026 Novain Shibu. AGPL-3.0-or-later, or a commercial licence (see COMMERCIAL.md).
"""Font-free decoding of garbled Indic PDF text: no reference font, only the PDF itself and a word list.

Usage:  python -m unshape.fontfree file1.pdf [file2.pdf ...] --wordlist hi_full.txt [--json]
        (PDFs made with the same font are decoded together: more text, better result.)
Word list: one "word count" per line, e.g. FrequencyWords (CC BY-SA 4.0):
  https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/hi/hi_full.txt

The embedded font's glyph outlines tell which glyphs are real ink (a vowel sign mapped to SPACE is still ink). The PDF's
own text map gives a label per glyph, right for most glyphs. Glyphs whose label is missing or blank but which draw ink
are unknowns; so are labels that are often slightly wrong (missing nukta, half forms). Each glyph keeps ONE meaning in
the whole document (a substitution cipher), and the meanings are chosen to make the most words real words.
Visual order is turned into logical order with the script's two rules: the i-sign is drawn before its consonant
cluster, the reph after it.
"""
import collections, io, re, sys, unicodedata
import pymupdf
from fontTools.ttLib import TTFont

VIR, NUK = "्", "़"
CONS = set(chr(c) for c in range(0x915, 0x93A)) | set(chr(c) for c in range(0x958, 0x960))
MARKS = ["ा", "ि", "ी", "ु", "ू", "ृ", "े", "ै", "ो", "ौ",
         "ं", "ँ", "ः", VIR, NUK, VIR + "र", "र" + VIR, "ॅ", "ॉ",
         "ें", "ैं", "ीं", "ुं", "िं"]
DEVA = re.compile(r"[ऀ-෿]")  # any Indic script handled here (Devanagari .. Sinhala)
LETTER = re.compile(r"[ऀ-ॣ॰-෿]")  # letters and signs (not the danda, not Devanagari digits)


def _r(a, b):
    return [chr(c) for c in range(a, b + 1)]


SCRIPTS = {  # block: consonants, virama, nukta, signs drawn BEFORE their consonant cluster, reph text, digits
    0x900: dict(cons=_r(0x915, 0x939) + _r(0x958, 0x95f), vir="्", nuk="़", pre=["ि"], reph="र्", dig=(0x966, 0x96f)),
    0x980: dict(cons=_r(0x995, 0x9b9) + _r(0x9dc, 0x9df), vir="্", nuk="়", pre=["ি", "ে", "ৈ"], reph="র্", dig=(0x9e6, 0x9ef)),
    0xB80: dict(cons=_r(0xb95, 0xbb9), vir="்", nuk=None, pre=["ெ", "ே", "ை"], reph=None, dig=(0xbe6, 0xbef)),
    0xC00: dict(cons=_r(0xc15, 0xc39), vir="్", nuk=None, pre=[], reph=None, dig=(0xc66, 0xc6f)),
    0xD00: dict(cons=_r(0xd15, 0xd3a), vir="്", nuk=None, pre=["െ", "േ", "ൈ", "്ര"], reph=None, dig=(0xd66, 0xd6f)),
    0xD80: dict(cons=_r(0xd9a, 0xdc6), vir="්", nuk=None, pre=["ෙ", "ේ", "ෛ"], reph=None, dig=(0xde6, 0xdef)),
}
PRE, REPH = ["ि"], "र्"


def use_script(block):
    """Switch the module tables (consonants, signs, candidate meanings) to one script block."""
    global CONS, VIR, NUK, PRE, REPH, ALL
    S = SCRIPTS[block]
    CONS = set(S["cons"]); VIR = S["vir"]; NUK = S["nuk"] or ""; PRE = S["pre"]; REPH = S["reph"]
    lo = block
    letters = [chr(c) for c in range(lo + 1, lo + 0x80) if not (S["dig"][0] <= c <= S["dig"][1]) and unicodedata.category(chr(c))[0] in "LM"]
    if block == 0x900:  # exactly the Devanagari list used before (keeps PREREG_58 behaviour)
        letters = sorted(set([chr(c) for c in range(0x901, 0x904)] + [chr(c) for c in range(0x905, 0x915)] + sorted(CONS) +
                             [chr(c) for c in range(0x93c, 0x94e)] + [chr(c) for c in range(0x966, 0x970)]) - {"ऄ"})
        ALL = letters + [VIR + "र", "र" + VIR, NUK + VIR] + [c + VIR for c in sorted(CONS)]
    else:
        ALL = sorted(set(letters)) + [c + VIR for c in sorted(CONS)] + [VIR + c for c in sorted(CONS)] + list(PRE)
        if block == 0xC00:  # Telugu draws most vowel signs fused with the consonant: one glyph = consonant + sign
            signs = [chr(c) for c in range(0xc3e, 0xc4d) if unicodedata.category(chr(c)) in ("Mn", "Mc")]
            ALL += [c + v for c in sorted(CONS) for v in signs]
        if REPH:
            ALL.append(REPH)


def _ink(tt):
    """glyph id -> True if the glyph draws something (outline non-empty)."""
    gs = tt.getGlyphSet(); out = {}
    for i, g in enumerate(tt.getGlyphOrder()):
        try:
            from fontTools.pens.boundsPen import BoundsPen
            p = BoundsPen(gs); gs[g].draw(p); out[i] = p.bounds is not None
        except Exception:
            out[i] = True
    return out


FONTS = {}  # embedded font programs seen (font key -> TTFont), used only for the PDF's own glyph shapes


def _contours(tt, gid):
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    try:
        g = tt.getGlyphOrder()[gid]; gs = tt.getGlyphSet(); p = DecomposingRecordingPen(gs); gs[g].draw(p)
    except Exception:
        return None
    cs, cur = [], []
    for op, args in p.value:
        cur.append((op, tuple(args)))
        if op in ("closePath", "endPath"):
            cs.append(tuple(cur)); cur = []
    return cs


def nukta_pairs(keys):
    """(font, gidB) -> (font, gidA) when glyph B draws exactly glyph A's contours plus small contour(s) below the baseline
    region (a nukta dot) - read from the PDF's own embedded outlines, no outside font."""
    out = {}
    byfont = collections.defaultdict(list)
    for k in keys:
        byfont[k[0]].append(k[1])
    for f, gids in byfont.items():
        tt = FONTS.get(f)
        if tt is None:
            continue
        upm = tt["head"].unitsPerEm
        C = {g: _contours(tt, g) for g in gids}
        for b in gids:
            cb = C[b]
            if not cb:
                continue
            for a in gids:
                ca = C[a]
                if a == b or not ca or len(cb) <= len(ca):
                    continue
                rest = list(cb)
                ok = True
                for c in ca:
                    if c in rest:
                        rest.remove(c)
                    else:
                        ok = False; break
                if not ok:
                    continue
                ys = [pt[1] for c in rest for op, args in c for pt in args if isinstance(pt, tuple)]
                xs = [pt[0] for c in rest for op, args in c for pt in args if isinstance(pt, tuple)]
                if ys and max(ys) < 0.12 * upm and (max(xs) - min(xs)) < 0.25 * upm:
                    out[(f, b)] = (f, a)
    return out


def read_runs(pdf, max_pages=None):
    """Words as lists of (font_key, gid, label, x). Word breaks = real space glyphs (no ink) or gaps; never the label."""
    doc = pymupdf.open(pdf); ink = {}
    for p in range(len(doc)):
        for xref, ext, typ, base, *_ in doc.get_page_fonts(p, full=True):
            k = base.split("+")[-1]
            if k in ink or ext not in ("ttf", "otf"):
                continue
            try:
                tt = TTFont(io.BytesIO(doc.extract_font(xref)[3])); ink[k] = _ink(tt); FONTS.setdefault(k, tt)
            except Exception:
                ink[k] = None
    words, cur = [], []
    for pno, page in enumerate(doc):
        if max_pages and pno >= max_pages:
            break
        for s in page.get_texttrace():
            fk = s["font"].split("+")[-1]; inkmap = ink.get(fk); last_y = None
            for u, gid, origin, bbox in s["chars"]:
                ch = chr(u) if u > 0 else ""
                if gid < 0:  # extra characters of the previous glyph's label
                    if cur:
                        a, b, lab, x = cur[-1]; cur[-1] = (a, b, lab + ch, x)
                    continue
                inky = inkmap.get(gid, bool(ch.strip())) if inkmap else bool(ch.strip())
                if not inky:
                    if cur: words.append(cur); cur = []
                    continue
                if cur and last_y is not None and abs(origin[1] - last_y) > 0.6 * s["size"]:
                    words.append(cur); cur = []
                cur.append((fk, gid, ch, origin[0])); last_y = origin[1]
            if cur and not DEVA.search("".join(t[2] for t in cur)):
                words.append(cur); cur = []
    if cur:
        words.append(cur)
    return words


def logical(tokens):
    """Visual-order meanings -> logical Unicode: i-sign after its consonant cluster, reph before it."""
    t = list(tokens); out = []; i = 0
    # i-sign: move right past one consonant cluster (C (NUK)? (VIR C (NUK)?)*)
    j = 0; res = []
    while j < len(t):
        if t[j] in PRE and j + 1 < len(t) and t[j + 1] and t[j + 1][0] in CONS:
            k = j + 1; cl = [t[k]]
            while k + 1 < len(t) and (t[k].endswith(VIR) or t[k + 1] in (NUK, VIR) or t[k + 1].startswith(VIR)):
                k += 1; cl.append(t[k])
            res += cl + [t[j]]; j = k + 1; continue
        res.append(t[j]); j += 1
    # reph: a 'र्' meaning drawn after a cluster moves before it
    out = []
    for tok in res:
        if REPH and tok == REPH and out:
            k = len(out) - 1
            while k > 0 and out[k] and (out[k][0] not in CONS):
                k -= 1
            while k > 0 and out[k - 1].endswith(VIR):
                k -= 1
            out.insert(k, tok); continue
        out.append(tok)
    return unicodedata.normalize("NFC", "".join(out))


ALL = sorted(set([chr(c) for c in range(0x901, 0x904)] + [chr(c) for c in range(0x905, 0x915)] + sorted(CONS) +
             [chr(c) for c in range(0x93c, 0x94e)] + [chr(c) for c in range(0x966, 0x970)]) - {"ऄ"})
ALL = ALL + [VIR + "र", "र" + VIR, NUK + VIR] + [c + VIR for c in sorted(CONS)]


class LM:
    """Character trigram model + word list: smooth score for the cipher search."""
    def __init__(self, words_with_counts):
        import math
        self.lex = {w for w, _ in words_with_counts}
        tri = collections.Counter(); bi = collections.Counter()
        for w, n in words_with_counts:
            s = "^^" + w + "$"
            for i in range(2, len(s)):
                tri[s[i - 2:i + 1]] += n; bi[s[i - 2:i]] += n
        self.tri, self.bi, self.V = tri, bi, 120
        self.log = math.log
        self.cache = {}

    def word(self, w):
        r = self.cache.get(w)
        if r is None:
            s = "^^" + w + "$"; lp = 0.0
            for i in range(2, len(s)):
                lp += self.log((self.tri.get(s[i - 2:i + 1], 0) + 0.1) / (self.bi.get(s[i - 2:i], 0) + 0.1 * self.V))
            r = lp / max(1, len(s) - 2) + (3.0 if w in self.lex else 0.0)
            if len(self.cache) < 2_000_000:
                self.cache[w] = r
        return r


def decode(pdf, lm, iters=40000, seed=0):
    return decode_pool([pdf], lm, iters, seed)[0]


def decode_pool(pdfs, lm, iters=60000, seed=0, sweeps=6, keep_label=0.0):
    """One cipher for all PDFs: glyph identity = (font name, glyph id), shared by documents of the same font build."""
    import random
    rnd = random.Random(seed)
    per = [read_runs(p) for p in pdfs]
    words = [w for ws in per for w in ws]
    key = lambda t: (t[0], t[1])
    isdeva = lambda w: bool(DEVA.search("".join(t[2] for t in w)))
    dw = [w for w in words if isdeva(w)]
    blocks = collections.Counter(ord(c) & ~0x7F for w in dw for t in w for c in t[2] if LETTER.match(c))
    use_script(blocks.most_common(1)[0][0] if blocks else 0x900)
    votes = collections.defaultdict(collections.Counter)  # each PDF labels a glyph from its OWN first use: pool them
    for w in dw:
        for t in w:
            votes[key(t)][t[2].strip()] += 1
    lab = {g: c.most_common(1)[0][0] for g, c in votes.items()}
    extra = {g: [x for x in c if x and x != lab[g]] for g, c in votes.items()}
    # Word writes a syllable's characters onto its glyphs in logical order while the glyphs are in visual order, so a
    # glyph's true text is often its NEIGHBOUR's label: add neighbours' labels (within 2 positions) as candidates
    for w in dw:
        for j, t in enumerate(w):
            for d in (-2, -1, 1, 2):
                if 0 <= j + d < len(w):
                    x = w[j + d][2].strip()
                    if x and LETTER.search(x) and x != lab[key(t)] and x not in extra[key(t)]:
                        extra[key(t)].append(x)
    glyphs = sorted(lab)
    seqs = [[glyphs.index(key(t)) for t in w] for w in dw]
    # identical glyph sequences scored once, weighted by count
    cnt = collections.Counter(tuple(s) for s in seqs); useqs = list(cnt); weight = [cnt[s] for s in useqs]
    by = collections.defaultdict(list)
    for i, s in enumerate(useqs):
        for g in set(s):
            by[g].append(i)
    cur = [lab[g] if lab[g] else "्" for g in glyphs]
    # glyphs labelled as punctuation, digits or Latin are NOT part of the cipher (Word labels those correctly)
    fixed = {g for g in range(len(glyphs)) if lab[glyphs[g]] and not LETTER.search(lab[glyphs[g]])}
    def wtext(i):
        return logical([cur[g] for g in useqs[i]])
    PUN = "।॥,.:;?!'\"“”‘’()[]-–—"
    def wscore(i):
        return weight[i] * lm.word(wtext(i).strip(PUN))
    sc = [wscore(i) for i in range(len(useqs))]
    total = sum(sc)
    def swap_pass():
        """Swap the meanings of neighbouring glyphs (Word's label shift inside a syllable)."""
        nonlocal total
        changed = 0
        pairs = collections.Counter()
        for s_ in useqs:
            for x, y in zip(s_, s_[1:]):
                if x != y and x not in fixed and y not in fixed:
                    pairs[(min(x, y), max(x, y))] += 1
        for (x, y), _ in pairs.most_common(400):
            if cur[x] == cur[y]:
                continue
            ids = sorted(set(by[x]) | set(by[y])); base = sum(sc[i] for i in ids)
            old = {x: cur[x], y: cur[y]}
            cur[x], cur[y] = old[y], old[x]
            ns = [wscore(i) for i in ids]; d = sum(ns) - base
            for g in (x, y):  # same label prior as single moves
                lg = lab[glyphs[g]]
                if lg and old[g] == lg:
                    d -= keep_label * len(by[g]) ** 0.5
                elif lg and cur[g] == lg:
                    d += keep_label * len(by[g]) ** 0.5
            if d > 1e-9:
                for i, v in zip(ids, ns):
                    sc[i] = v
                total += d; changed += 1
            else:
                cur[x], cur[y] = old[x], old[y]
        return changed

    order = sorted(range(len(glyphs)), key=lambda g: -sum(weight[i] for i in by[g]))
    for sweep in range(sweeps):
        changed = swap_pass()
        for g in order:
            if g in fixed:
                continue
            ids = by[g]; old = cur[g]; base = sum(sc[i] for i in ids)
            best, bestd, bestns = old, 0.0, None
            for new in ([lab[glyphs[g]]] if lab[glyphs[g]] else []) + extra[glyphs[g]] + ALL:
                if new == old:
                    continue
                cur[g] = new; ns = [wscore(i) for i in ids]; d = sum(ns) - base
                if lab[glyphs[g]] and old == lab[glyphs[g]]:
                    d -= keep_label * len(ids) ** 0.5  # leaving the PDF's own label needs strong evidence
                elif lab[glyphs[g]] and new == lab[glyphs[g]]:
                    d += keep_label * len(ids) ** 0.5
                if d > bestd + 1e-9:
                    best, bestd, bestns = new, d, ns
            cur[g] = best
            if bestns is not None:
                for i, v in zip(ids, bestns):
                    sc[i] = v
                total += bestd; changed += 1
        if not changed:
            break
    meaning = {glyphs[g]: cur[g] for g in range(len(glyphs))}
    for b, a in (nukta_pairs(glyphs).items() if NUK else []):  # glyph = another glyph + a dot below -> letter + nukta
        if a in meaning and meaning[a] and meaning[a][-1] in CONS and NUK not in meaning[b]:
            meaning[b] = meaning[a] + NUK
    info = {"glyphs": len(glyphs), "changed": sum(meaning[g] != lab[g] for g in glyphs), "score": round(total, 1)}
    res = []
    for ws in per:
        out = [logical([meaning[key(t)] for t in w]) if isdeva(w) else "".join(t[2] for t in w) for w in ws]
        res.append((" ".join(out), info))
    return res


def load_wordlist(path, min_count=2):
    wc = collections.Counter()
    for line in open(path, encoding="utf-8"):
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].strip().isdigit() and int(parts[1]) >= min_count:
            wc[unicodedata.normalize("NFC", parts[0])] += int(parts[1])
    return LM(list(wc.items()))


def main(argv=None):
    import json as _json
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ("-h", "--help") or "--wordlist" not in a:
        print(__doc__); return 0
    i = a.index("--wordlist"); wl = a[i + 1]; del a[i:i + 2]
    as_json = "--json" in a; a = [x for x in a if x != "--json"]
    res = decode_pool(a, load_wordlist(wl), keep_label=4)
    if as_json:
        print(_json.dumps([{"pdf": f, "text": t, "info": inf} for f, (t, inf) in zip(a, res)], ensure_ascii=False, indent=1))
    else:
        for f, (t, inf) in zip(a, res):
            print(f"== {f}  {inf}"); print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
