# Copyright (c) 2026 Novain Shibu. AGPL-3.0-or-later, or a commercial licence (see COMMERCIAL.md).
"""Unshape-multi v4 (accuracy push, FINDINGS sec 17). Changes vs the frozen v3:
E1 zero-advance space glyph (= ZWNJ/ZWJ, HarfBuzz and Word both draw it with the space glyph) stays inside the run;
E2 lookup keys normalised by the font's own single/alternate-substitution classes (contextual variants share a key) +
   more combinatorial syllables (conjunct + final virama, vowel sign + anusvara/visarga/candrabindu);
E3 Word-shaper delta: glyph rewrite rules learned from DEV pairs only (unshape_multi4.rules.json, learn_rules4.py);
E4 character 5-gram (training text) picks among render-verified candidates.
Verification stays exact: rewrite(HarfBuzz(s)) == the PDF's glyph IDs.
v3: per-face Regular/Bold, danda/digits trusted, NFC output, "render-verified" + render-ambiguity flag.
Syllable inventory and the 5-gram come ONLY from held-out training text (train_<lang>.txt) + combinatorial syllables.
Usage: python unshape_multi4.py <lang>:<pdf>:<gt> ...   (run in invent7/word_test)
"""
import collections, heapq, json, math, os, re, sys, time, unicodedata
from pathlib import Path

import pymupdf
import uharfbuzz as hb
from fontTools.ttLib import TTCollection

TTC = r"C:\Windows\Fonts\Nirmala.ttc"
BLOCK = {"hi": 0x0900, "bn": 0x0980, "pa": 0x0A00, "gu": 0x0A80, "or": 0x0B00, "ta": 0x0B80, "te": 0x0C00, "kn": 0x0C80,
         "ml": 0x0D00, "si": 0x0D80}
VIRAMAS = {0x094D, 0x09CD, 0x0A4D, 0x0ACD, 0x0B4D, 0x0BCD, 0x0C4D, 0x0CCD, 0x0D4D, 0x0D3B, 0x0D3C, 0x0DCA}
ZW = "\u200c\u200d"


def single_classes(ft):
    """Union-find over every single/alternate substitution in GSUB: contextual variants of one glyph share a class."""
    par = {}

    def f(x):
        while par.get(x, x) != x:
            x = par[x]
        return x
    order = {n: i for i, n in enumerate(ft.getGlyphOrder())}
    for lk in ft["GSUB"].table.LookupList.Lookup:
        for st in lk.SubTable:
            typ = lk.LookupType
            if typ == 7:
                st, typ = st.ExtSubTable, st.ExtensionLookupType
            if typ == 1:
                pairs = list(st.mapping.items())
            elif typ == 3:
                pairs = [(a, b) for a, bs in st.alternates.items() for b in bs]
            else:
                continue
            for a, b in pairs:
                ra, rb = f(order[a]), f(order[b])
                if ra != rb:
                    par[max(ra, rb)] = min(ra, rb)
    return {g: f(g) for g in par}


def rewrite(g, rules):
    if not rules:
        return g
    out, i = [], 0
    while i < len(g):
        for a, b in rules:
            if g[i:i + len(a)] == a:
                out.extend(b); i += len(a); break
        else:
            out.append(g[i]); i += 1
    return tuple(out)


class Face:
    def __init__(self, sub):
        tc = TTCollection(TTC)
        self.idx = next(i for i, f in enumerate(tc.fonts)
                        if f["name"].getDebugName(1) == "Nirmala UI" and f["name"].getDebugName(2) == sub)
        ft = tc.fonts[self.idx]
        self.sub = sub
        self.hbf = hb.Font(hb.Face(hb.Blob.from_file_path(TTC), self.idx))
        self.cmap = {cp: ft.getGlyphID(n) for cp, n in ft.getBestCmap().items()}
        self.space = self.cmap[32]
        self.norm = single_classes(ft)
        self.rules = {}
        self.cache = {}
        self.tables = {}

    def shape(self, t):
        r = self.cache.get(t)
        if r is None:
            b = hb.Buffer(); b.add_str(t); b.guess_segment_properties(); hb.shape(self.hbf, b, {})
            r = self.cache[t] = tuple(g.codepoint for g in b.glyph_infos)
        return r

    def wshape(self, t, lang):
        """HarfBuzz shaping followed by the learned Word-shaper delta for this language/face."""
        return rewrite(self.shape(t), self.rules.get(lang, ()))

    def key(self, glyphs):
        return tuple(self.norm.get(g, g) for g in glyphs)


try:  # research setup: Windows' Nirmala UI; absent on other systems (the release decoder does not need it)
    FACES = {"Regular": Face("Regular"), "Bold": Face("Bold")}
except Exception:
    FACES = {}
RULES = Path(__file__).with_name("unshape_multi4.rules.json")
DATA = Path(os.environ.get("UNSHAPE_DATA") or next((d for d in (Path(__file__).resolve().parent / "data",
                                                               Path(__file__).resolve().parent.parent / "data")
                                                     if (d / "train_hi.txt").exists()), Path(__file__).resolve().parent / "data"))
RULESETS = {}  # Word-vs-HarfBuzz glyph deltas for Nirmala UI, keyed by face style then language
if RULES.exists():
    for k, v in json.loads(RULES.read_text()).items():
        _lang, _sub = k.split("|")
        RULESETS.setdefault(_sub, {})[_lang] = sorted(((tuple(a), tuple(b)) for a, b in v), key=lambda r: -len(r[0]))
        if _sub in FACES:
            FACES[_sub].rules[_lang] = RULESETS[_sub][_lang]


def face_of(fontname):
    return FACES["Bold"] if "bold" in fontname.lower() else FACES["Regular"]


class CharLM:
    """Character 5-gram with stupid backoff, from the training text only."""
    def __init__(self, text, n=5):
        self.n, self.c = n, collections.Counter()
        t = " " + re.sub(r"\s+", " ", text) + " "
        for k in range(1, n + 1):
            self.c.update(t[i:i + k] for i in range(len(t) - k + 1))
        self.tot = sum(v for g, v in self.c.items() if len(g) == 1)

    def score(self, w):
        t, s = " " + w + " ", 0.0
        for i in range(1, len(t)):
            pen = 0.0
            for k in range(min(self.n, i + 1), 0, -1):
                g = t[i - k + 1:i + 1]
                if k == 1:
                    s += math.log((self.c[g] + 0.1) / (self.tot + 1)) + pen; break
                if self.c[g]:
                    s += math.log(self.c[g] / self.c[g[:-1]]) + pen; break
                pen += math.log(0.4)
        return s


LMS = {}


def lm(lang):
    if lang not in LMS:
        LMS[lang] = CharLM(unicodedata.normalize("NFC", (DATA / f"train_{lang}.txt").read_text(encoding="utf-8")))
    return LMS[lang]


def classes(lang):
    lo = BLOCK[lang]
    L, M = [], []
    for c in range(lo, lo + 0x80):
        cat = unicodedata.category(chr(c))
        if c in VIRAMAS:
            continue
        if cat == "Lo":
            L.append(chr(c))
        elif cat in ("Mn", "Mc"):
            M.append(chr(c))
    V = [chr(c) for c in VIRAMAS if lo <= c < lo + 0x80]
    return L, M, V


def syl_regex(lang):
    L, M, V = (re.escape("".join(x)) for x in classes(lang))
    # akshara: letter (virama ZW? letter)* marks*, optional final virama (+ZW)
    return re.compile(f"[{L}](?:[{V}][{ZW}]?[{L}])*[{M}{V}{ZW}]*|[{M}{V}{ZW}]+")


def inventory(lang, fc=None):
    fc = fc or FACES["Regular"]
    if lang in fc.tables:
        return fc.tables[lang]
    L, M, V = classes(lang)
    rx = syl_regex(lang)
    freq = collections.Counter()
    t = unicodedata.normalize("NFC", (DATA / f"train_{lang}.txt").read_text(encoding="utf-8"))
    lo = BLOCK[lang]
    for w in re.findall(f"[\\u{lo:04x}-\\u{lo + 0x7F:04x}{ZW}]+", t):
        sy = rx.findall(w)
        freq.update(sy)
        # context units (v2): syllable pairs/triples and whole words from the TRAINING text
        for n in (2, 3):
            freq.update("".join(sy[i:i + n]) for i in range(len(sy) - n + 1))
        if len(sy) > 3:
            freq[w] += 1
    combo = set(L) | {c + m for c in L for m in M + V}
    combo |= {a + v + b for a in L for b in L for v in V}
    combo |= {a + v + b + m for a in L for b in L for v in V for m in M}
    # v4 (E2): conjunct + final virama; consonant + vowel sign + anusvara/visarga/candrabindu; virama + ZWNJ/ZWJ
    combo |= {a + v + b + v for a in L for b in L for v in V}
    nas = [m for m in M if unicodedata.name(m, "").split()[-1] in ("ANUSVARA", "VISARGA", "CANDRABINDU")]
    combo |= {c + m + n_ for c in L for m in M for n_ in nas if m not in nas}
    combo |= {s_ + z for s_ in list(combo) if s_[-1:] in V for z in ZW}
    for s in combo:
        freq[s] += 0
    table = collections.defaultdict(list)
    for s, n in freq.items():
        g = fc.wshape(s, lang)
        if g and 0 not in g:
            table[fc.key(g)].append((n, s))
    for g in table:
        table[g].sort(reverse=True)
    fc.tables[lang] = (table, max(map(len, table)))
    return fc.tables[lang]


def kbest(glyphs, table, maxlen, k=32):
    n = len(glyphs)
    best = [[] for _ in range(n + 1)]
    best[0] = [(0.0, "")]
    for i in range(n):
        if not best[i]:
            continue
        for L_ in range(1, min(maxlen, n - i) + 1):
            for cnt, s in table.get(tuple(glyphs[i:i + L_]), ())[:8]:
                c = -math.log(cnt + 1) + 12.0
                for bc, bs in best[i]:
                    best[i + L_].append((bc + c, bs + s))
            best[i + L_] = heapq.nsmallest(k, best[i + L_])
    return best[n]


def runs(pdf, font_filter="Nirmala"):
    """Glyph runs split on the face's real space glyph, on line changes and on font changes; item = (gid, uni).
    v4 (E1): a space glyph with zero advance (ZWNJ/ZWJ) stays inside the run."""
    chars = []
    for page in pymupdf.open(pdf):
        for span in page.get_texttrace():
            if font_filter not in span["font"]:
                continue
            for uni, gid, origin, bbox in span["chars"]:
                if gid >= 0:
                    chars.append((span["font"], span["size"], gid, uni, origin))
    out, cur, curfont, cur_y = [], [], None, None
    for i, (font, size, gid, uni, origin) in enumerate(chars):
        fc = face_of(font)
        if cur and font != curfont:
            out.append((curfont, cur)); cur = []
        if cur and abs(origin[1] - cur_y) > 1.2 * size:
            out.append((curfont, cur)); cur = []
        curfont, cur_y = font, origin[1]
        if gid == fc.space:
            nxt = chars[i + 1] if i + 1 < len(chars) else None
            zero = (nxt is not None and nxt[0] == font and abs(nxt[4][1] - origin[1]) < 0.5 * size
                    and abs(nxt[4][0] - origin[0]) < 0.05 * size)
            if zero and cur:
                cur.append((gid, 0)); continue
            if cur:
                out.append((curfont, cur)); cur = []
            continue
        cur.append((gid, uni))
    if cur:
        out.append((curfont, cur))
    return out


def decode(run, lang, fontname, st):
    fc = face_of(fontname)
    table, maxlen = inventory(lang, fc)
    lo = BLOCK[lang]

    def trusted(g, u):
        # producer Unicode is trusted only if the real font maps it to this glyph AND it is not a script letter/mark
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
        cands = kbest(fc.key(gl), table, maxlen)
        ver = []
        for _, s_ in cands:
            if fc.wshape(s_, lang) == gl:
                n = unicodedata.normalize("NFC", s_)
                if n not in ver:
                    ver.append(n)
        if ver:
            st["render_verified"] += 1
            st["render_ambiguous"] += len(ver) > 1
            pick = max(ver, key=lm(lang).score) if len(ver) > 1 else ver[0]
        else:
            st["unverified"] += 1
            pick = cands[0][1] if cands else "".join(chr(x[1]) for x in run[i:j] if x[1] > 0)
        out.append(pick); i = j
    return unicodedata.normalize("NFC", "".join(out))


def words(t):
    return [w for w in re.split(r"[\s\u0964\u0965.,;:!?()\[\]\"'“”‘’\-–—/]+", unicodedata.normalize("NFC", t)) if w]


def evaluate(pdf, gtfile, lang):
    st = collections.Counter()
    dec = " ".join(decode(r, lang, f, st) for f, r in runs(pdf))
    g = collections.Counter(words(Path(gtfile).read_text(encoding="utf-8")))

    def recall(t):
        c = collections.Counter(words(t)); return sum(min(n, c[w]) for w, n in g.items()) / sum(g.values())
    mu = "".join(p.get_text() for p in pymupdf.open(pdf))
    rv = st["render_verified"]
    return {"pdf": Path(pdf).name, "lang": lang, "gt_words": sum(g.values()), "pymupdf_exact": round(recall(mu), 4),
            "unshape_exact": round(recall(dec), 4), "render_verified_share": round(rv / max(1, rv + st["unverified"]), 4),
            "render_ambiguous_share": round(st["render_ambiguous"] / max(1, rv), 4), "stretches": dict(st)}, dec


def main():
    """usage: python unshape_multi4.py <lang>:<pdf>:<gt> ..."""
    res = []
    for arg in sys.argv[1:]:
        lang, pdf, gt = arg.split(":", 2)
        r, dec = evaluate(pdf, gt, lang)
        Path(pdf).with_suffix(".unshaped4.txt").write_text(dec, encoding="utf-8")
        res.append(r); print(json.dumps(r), flush=True)
    Path(f"unshape_multi4_{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
