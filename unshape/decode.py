# Copyright (c) 2026 Novain Shibu. AGPL-3.0-or-later, or a commercial licence (see COMMERCIAL.md).
"""unshape: recover the exact Unicode text of complex-script PDFs whose text layer is broken, without OCR.

For each embedded complex-script font: if the embedded program still has its shaping (GSUB) table, it is used directly;
otherwise the glyphs are matched by outline to a REFERENCE font you supply (the original font, e.g. Noto Sans Devanagari,
Lohit, or a Windows font installed on your own machine). The text is then recovered by searching for the Unicode string
whose shaping reproduces the PDF's glyphs; a stretch is accepted only if re-shaping it gives exactly those glyph IDs
("verified"), otherwise the PDF's own text is kept and the stretch is marked "fallback".

Usage:  python -m unshape.decode file.pdf [--font ref.ttf ...] [--json] [--max-seconds 300]
"""
import collections, io, json, sys, time
from pathlib import Path

import pymupdf
from fontTools.ttLib import TTFont, TTCollection
from fontTools.pens.recordingPen import DecomposingRecordingPen

_ARGV = sys.argv[1:]  # the research modules strip sys.argv on import; keep the real arguments
import unshape_emb4  # noqa: F401,E402  (the package __init__ puts the module folder on sys.path)
import unshape_emb as E  # noqa: E402  (same module object that unshape_emb4 patched)
sys.argv = sys.argv[:1] + _ARGV

MIN_MATCH = 0.8  # share of a subset's non-empty glyphs that must match a reference font exactly


def _sig(tt, g):
    gs = tt.getGlyphSet(); p = DecomposingRecordingPen(gs); gs[g].draw(p)
    return (tuple(p.value), tt["hmtx"][g][0])


def _ref_fonts(paths):
    """[(label, bytes, TTFont)] for every face in the given .ttf/.otf/.ttc files."""
    out = []
    for p in paths:
        p = Path(p)
        if p.suffix.lower() == ".ttc":
            for i, f in enumerate(TTCollection(str(p)).fonts):
                b = io.BytesIO(); f.save(b); data = b.getvalue()
                out.append((f"{p.name}#{i}:{f['name'].getDebugName(4)}", data, TTFont(io.BytesIO(data))))
        else:
            data = p.read_bytes(); out.append((p.name, data, TTFont(io.BytesIO(data))))
    return out


def _match(sub, ref):
    """Map subset glyph index -> reference glyph ID by outline+advance. Returns (remap, matched, total, ambiguous)."""
    rsig = collections.defaultdict(list)
    for g in ref.getGlyphOrder():
        try:
            rsig[_sig(ref, g)].append(g)
        except Exception:
            pass
    remap, matched, total, amb = {}, 0, 0, 0
    order = ref.getGlyphOrder()
    space = (ref.getBestCmap() or {}).get(32)
    for i, g in enumerate(sub.getGlyphOrder()):
        try:
            s = _sig(sub, g)
        except Exception:
            continue
        cands = rsig.get(s, [])
        if not s[0]:  # empty outline (space, no-break space...): map by advance width, not counted in the match rate
            if len(cands) == 1:
                remap[i] = ref.getGlyphID(cands[0])
            elif space in cands:
                remap[i] = ref.getGlyphID(space)
            continue
        total += 1
        if not cands:
            continue
        matched += 1
        if len(cands) == 1:
            remap[i] = ref.getGlyphID(cands[0])
        elif i < len(order) and order[i] in cands:  # same GID kept (Word keeps original glyph IDs)
            remap[i] = i
        else:
            amb += 1  # identical outlines, no way to tell: left unmapped (never guessed)
    return remap, matched, total, amb


def _spaces(fc):
    """Glyph IDs that any whitespace character maps to in this font (space, no-break space, thin spaces...)."""
    sp = fc.__dict__.get("_space_set")
    if sp is None:
        sp = fc._space_set = {g for cp, g in fc.cmap.items() if chr(cp).isspace() and cp not in (0x200B, 0xFEFF)}
    return sp


def _advance(fc, gid):
    """Advance width of a glyph in em units (from the face's font program)."""
    adv = fc.__dict__.get("_adv_em")
    if adv is None:
        from fontTools.ttLib import TTFont
        tt = TTFont(io.BytesIO(fc._buf))
        upm = tt["head"].unitsPerEm; order = tt.getGlyphOrder(); hm = tt["hmtx"].metrics
        adv = fc._adv_em = [hm[n][0] / upm for n in order]
    return adv[gid] if 0 <= gid < len(adv) else 0.0


def _runs(doc, faces, remaps):
    """Like unshape_emb.runs, but (1) glyph IDs are mapped to the reference font BEFORE spaces are detected (a subset's
    glyph number can equal the reference font's space glyph), and (2) a glyph drawn again at the same position (overprint,
    e.g. simulated bold) is counted once."""
    fmap = {E.norm_name(k): (k, v) for k, v in faces.items()}
    chars, recent = [], []
    for page in doc:
        trace = page.get_texttrace()
        lines = collections.Counter(); spaces = collections.Counter()  # per text line, across all spans
        for span in trace:
            for u_, g_, o_, b_ in span["chars"]:
                ln = round(o_[1] / max(1.0, span["size"] * 0.5))
                lines[ln] += 1; spaces[ln] += u_ == 32
        for span in trace:
            hit = fmap.get(E.norm_name(span["font"]))
            ln0 = round(span["chars"][0][2][1] / max(1.0, span["size"] * 0.5)) if span["chars"] else 0
            spaced = spaces[ln0] > 0.04 * max(1, lines[ln0])  # normal text: a space every few glyphs on the line
            rm = remaps.get(hit[0]) if hit else None
            for uni, gid, origin, bbox in span["chars"]:
                if hit is None:
                    chars.append((None, span["size"], gid, uni, origin, bbox, spaced)); continue
                if gid < 0:
                    if chars and uni > 0 and hit is not None and chars[-1][0] is hit:  # extra char of a multi-char glyph
                        h, sz, g0, u0, o0, b0, sp0 = chars[-1]
                        chars[-1] = (h, sz, g0, (u0 if isinstance(u0, str) else (chr(u0) if u0 > 0 else "")) + chr(uni), o0, b0, sp0)
                    continue
                if rm is not None:
                    gid = rm.get(gid, -1)
                    if gid < 0:
                        continue
                key = (hit[0] if hit else None, gid, round(origin[0], 1), round(origin[1], 1))
                if key in recent:
                    continue
                recent = (recent + [key])[-6:]
                chars.append((hit, span["size"], gid, uni, origin, bbox, spaced))
    out, cur, curf, cur_y, right = [], [], None, None, None
    for i, (hit, size, gid, uni, origin, bbox, spaced) in enumerate(chars):
        if hit is None:  # text in a font Unshape does not decode (digits, Latin...): passed through as the PDF has it
            if cur and curf is not None:
                out.append((curf, cur)); cur = []
            u_txt = uni if isinstance(uni, str) else (chr(uni) if uni > 0 else "")
            gap = right is not None and origin[0] - right > 0.15 * size
            if cur and (u_txt.isspace() or gap or abs(origin[1] - (cur_y or origin[1])) > 1.2 * size):
                out.append((None, cur)); cur = []
            if u_txt and not u_txt.isspace():
                cur.append((-1, u_txt))
            curf, cur_y, right = None, origin[1], bbox[2]
            continue
        if cur and curf is None:
            out.append((None, cur)); cur = []
        fc = hit[1]
        if cur and fc is not curf:
            out.append((curf, cur)); cur = []
        if cur and abs(origin[1] - cur_y) > 1.2 * size:
            out.append((curf, cur)); cur = []
        # word gap without a space glyph (justified text positioned with offsets): a visible horizontal gap ends the word
        # word gap without a space glyph (word spacing by positioning, e.g. justified text): the glyph starts clearly
        # after where the previous glyph's advance ended (advance widths from the font; marks have zero advance)
        adv = _advance(fc, gid) * size
        # only where the text has (almost) no space characters, i.e. word spacing done by positioning (justified text);
        # in normal text with spaces, glyph positioning inside words can look like gaps and must not split words
        if cur and not spaced and adv > 0 and right is not None and origin[0] - right > 0.15 * size:
            out.append((curf, cur)); cur = []
        end = origin[0] + adv
        right = end if (right is None or not cur) else (max(right, end) if adv > 0 else right)
        curf, cur_y = fc, origin[1]
        if gid == fc.space or gid in _spaces(fc):
            nxt = chars[i + 1] if i + 1 < len(chars) else None
            zero = (nxt is not None and nxt[0] is hit and abs(nxt[4][1] - origin[1]) < 0.5 * size
                    and abs(nxt[4][0] - origin[0]) < 0.05 * size)  # zero-advance space glyph (ZWNJ/ZWJ) stays inside
            if zero and cur:
                cur.append((gid, 0)); continue
            if cur:
                out.append((curf, cur)); cur = []
            continue
        cur.append((gid, uni))
    if cur:
        out.append((curf, cur))
    return out


class _Merged:
    """Read-only union of two {glyph-key: [(count, text)]} tables."""
    def __init__(self, a, b):
        self.a, self.b = a, b

    def get(self, key, default=()):
        r = list(self.a.get(key, ())) + list(self.b.get(key, ()))
        return r or default


def _unit_shape(fc, u_, lang):
    """Shape a search unit. Units that start with a combining mark or virama (e.g. '्रू', 'ों') are shaped without the
    dotted-circle placeholder HarfBuzz would insert, so they match the glyphs they produce inside a real word."""
    import unicodedata
    import uharfbuzz as hb
    if not u_ or unicodedata.category(u_[0])[0] != "M":
        return fc.wshape(u_, lang)
    cache = fc.__dict__.setdefault("_nodc_cache", {})
    r = cache.get(u_)
    if r is None:
        b = hb.Buffer(); b.add_str(u_); b.guess_segment_properties()
        b.flags = hb.BufferFlags.DO_NOT_INSERT_DOTTED_CIRCLE
        hb.shape(fc.hbf, b, {})
        r = tuple(g.codepoint for g in b.glyph_infos)
        rules = getattr(fc, "rules", {}).get(lang)
        r = cache[u_] = E.U.rewrite(r, rules) if rules else r
    return r


def _font_extra(fc, lang):
    """Units missing from the base syllable list, built once per font+script and cached: mark-only units (vowel sign,
    vowel sign + nasal mark, e.g. 'ों') and conjunct + vowel sign + nasal mark (e.g. 'न्थों')."""
    import unicodedata
    cache = fc.__dict__.setdefault("_extra_units", {})
    if lang in cache:
        return cache[lang]
    U = E.U
    L, M, V = U.classes(lang)
    nas = [m for m in M if unicodedata.name(m, "").split()[-1] in ("ANUSVARA", "VISARGA", "CANDRABINDU")]
    vs = [m for m in M if m not in nas]
    units = set(M) | {m + n_ for m in vs for n_ in nas}
    units |= {a + v + b + m + n_ for a in L for b in L for v in V for m in vs for n_ in nas}
    # attached consonant pieces for 3-consonant clusters (e.g. ত্র + ্যে, ল্ + ল্র), optionally with vowel sign / nasal
    units |= {v + c for v in V for c in L}
    units |= {v + c + m for v in V for c in L for m in M}
    units |= {v + c + m + n_ for v in V for c in L for m in vs for n_ in nas}
    # punctuation, digits and symbols outside the script block (producers sometimes label them wrongly, e.g. ',' as ' ')
    lo = fc.block or 0
    units |= {chr(cp) for cp in fc.cmap if not (lo <= cp < lo + 0x80) and cp > 32
              and unicodedata.category(chr(cp))[0] in "PNS"}
    table = collections.defaultdict(list)
    for u_ in units:
        try:
            g = _unit_shape(fc, u_, lang)
        except Exception:
            continue
        if g and 0 not in g:
            table[fc.key(g)].append((0, u_))
    cache[lang] = dict(table)
    return cache[lang]


def _glyph_letters(fc):
    """glyph ID -> set of characters it is built from: cmap, plus the components of GSUB single and ligature
    substitutions that produce it (followed recursively), so letters fused into conjunct glyphs are found."""
    gl = fc.__dict__.get("_glyph_letters")
    if gl is not None:
        return gl
    from fontTools.ttLib import TTFont
    tt = TTFont(io.BytesIO(fc._buf)) if hasattr(fc, "_buf") else None
    letters = collections.defaultdict(set)
    for cp, g in fc.cmap.items():
        letters[g].add(chr(cp))
    edges = collections.defaultdict(set)  # produced glyph id -> source glyph ids
    if tt is not None and "GSUB" in tt:
        gid = tt.getGlyphID
        for lk in tt["GSUB"].table.LookupList.Lookup:
            for st in lk.SubTable:
                if lk.LookupType == 7:
                    st = st.ExtSubTable
                t = getattr(st, "LookupType", lk.LookupType)
                if t == 1:
                    for a, b in st.mapping.items():
                        edges[gid(b)].add(gid(a))
                elif t == 4:
                    for first, ligs in st.ligatures.items():
                        for lg in ligs:
                            edges[gid(lg.LigGlyph)] |= {gid(first)} | {gid(c) for c in lg.Component}
    for _ in range(4):  # follow chains of substitutions
        changed = False
        for out_g, srcs in edges.items():
            add = set().union(*(letters.get(s_, set()) for s_ in srcs)) - letters[out_g]
            if add:
                letters[out_g] |= add; changed = True
        if not changed:
            break
    fc._glyph_letters = dict(letters)
    return fc._glyph_letters


def _own_syllables(own, fc, lang, gl=()):
    """Candidate units from the file's own text of a stretch: syllables, syllable pairs/triples and the whole stretch,
    each shaped with the font (only exact re-shaping is ever accepted later, so wrong guesses cannot get through)."""
    import unicodedata
    U = E.U
    rx = U.syl_regex(lang)
    sy = rx.findall(unicodedata.normalize("NFC", own))
    units = set(sy)
    for k_ in (2, 3):
        units |= {"".join(sy[i:i + k_]) for i in range(len(sy) - k_ + 1)}
    # conjunct + vowel sign + nasal mark (e.g. न्थों, न्हों), built from the stretch's own letters
    L, M, V = U.classes(lang)
    nas = [m for m in M if unicodedata.name(m, "").split()[-1] in ("ANUSVARA", "VISARGA", "CANDRABINDU")]
    # whole clusters from the letters present in this stretch: from the file's text and read from the glyphs themselves
    # (context-dependent glyph forms only appear when the whole cluster is shaped)
    L, M, V = U.classes(lang)
    vs = [m for m in M if unicodedata.name(m, "").split()[-1] not in ("ANUSVARA", "VISARGA", "CANDRABINDU")]
    gmap = _glyph_letters(fc)
    letters = {c for c in own if c in L} | {c for g in set(gl) for c in gmap.get(g, ()) if c in L}
    if len(letters) <= 14:
        for a in letters:
            for b in letters:
                for v in V:
                    units.add(a + v + b)
                    for c in letters:
                        units.add(a + v + b + v + c)
                        for m in vs:
                            units.add(a + v + b + v + c + m)
    base, _ml = (U.inventory(lang, fc) if lang in fc.tables else (None, 0))
    if base and gl:
        key = fc.key(gl)
        nas = [m for m in M if m not in vs]
        for i in range(len(key)):
            for L_ in range(1, min(_ml, len(key) - i) + 1):
                for _, t_ in base.get(tuple(key[i:i + L_]), ())[:4]:
                    if t_ and t_[-1] in L:  # a cluster ending in a consonant: add vowel signs / nasal marks
                        units |= {t_ + m for m in M} | {t_ + m + n_ for m in vs for n_ in nas}
    table = collections.defaultdict(list)
    for k_, v_ in _font_extra(fc, lang).items():  # font-wide extra units, built once per font and script
        table[k_].extend(v_)
    for u_ in units:
        try:
            g = _unit_shape(fc, u_, lang)
        except Exception:
            continue
        if g and 0 not in g:
            table[fc.key(g)].append((0, u_))
    return dict(table)


def _ok(fc, text, gl, lang):
    """Verified = shaping the text reproduces exactly the PDF's glyphs (HarfBuzz, or HarfBuzz + the producer's learned
    glyph delta when one is known for this font)."""
    g = fc.shape(text)
    if g == gl:
        return True
    rules = getattr(fc, "rules", {}).get(lang)
    if rules and E.U.rewrite(g, rules) == gl:
        return True
    # same glyphs up to variant forms of the same character (single-substitution classes, e.g. a contextual vowel-sign
    # form vs its plain form): the producer chose another form of the same letter; the text is identical
    return len(g) == len(gl) and fc.key(g) == fc.key(gl)


def _decode(run, fc, st):
    """unshape_emb.decode with two additions: the file's own text for a stretch is tried first and accepted only if it
    re-shapes to exactly the same glyphs; the fallback keeps the file's complete text (multi-character glyphs)."""
    import unicodedata
    U = E.U
    lang = E.SCRIPT[fc.block][0]
    try:
        table, maxlen = U.inventory(lang, fc)
    except ValueError:
        table, maxlen = None, 0
    lo = fc.block
    txt = lambda u: u if isinstance(u, str) else (chr(u) if u > 0 else "")

    def trusted(g, u):
        if not isinstance(u, int) or fc.cmap.get(u) != g:
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
        own = unicodedata.normalize("NFC", "".join(txt(x[1]) for x in run[i:j]))
        if own and _ok(fc, own, gl, lang):
            st["render_verified"] += 1; st["verified_own_text"] += 1
            out.append(own); i = j; continue
        ver = []
        if table:
            for _, s_ in U.kbest(fc.key(gl), table, maxlen):
                if _ok(fc, s_, gl, lang):
                    nrm = unicodedata.normalize("NFC", s_)
                    if nrm not in ver:
                        ver.append(nrm)
        if not ver:  # second chance: syllables found in this stretch's own (possibly garbled) text, wider search
            extra = _own_syllables(own, fc, lang, gl)
            if extra:
                merged = _Merged(table or {}, extra)
                ml = max([maxlen] + [len(k_) for k_ in extra])
                for _, s_ in U.kbest(fc.key(gl), merged, ml, k=256):
                    if _ok(fc, s_, gl, lang):
                        nrm = unicodedata.normalize("NFC", s_)
                        if nrm not in ver:
                            ver.append(nrm)
                if ver:
                    st["verified_second_pass"] += 1
        if ver:
            st["render_verified"] += 1
            st["render_ambiguous"] += len(ver) > 1
            out.append(max(ver, key=U.lm(lang).score) if len(ver) > 1 else ver[0])
        else:
            st["unverified"] += 1
            out.append(own)
        i = j
    return unicodedata.normalize("NFC", "".join(out))


def decode_pdf(path, ref_paths=(), max_seconds=300):
    t0 = time.time()
    doc = pymupdf.open(path)
    refs = _ref_fonts(ref_paths)
    faces, remaps, report = {}, {}, []
    seen = set()
    for pno in range(len(doc)):
        for xref, ext, typ, base, *_ in doc.get_page_fonts(pno, full=True):
            if ext not in ("ttf", "otf") or xref in seen:
                continue
            seen.add(xref)
            name = base.split("+")[-1]
            try:
                buf = doc.extract_font(xref)[3]; sub = TTFont(io.BytesIO(buf))
            except Exception:
                continue
            cps = [cp for cp in (sub.getBestCmap() or {}) if 0x0900 <= cp < 0x0E00]
            if "GSUB" in sub and name not in faces:
                try:
                    faces[name] = E.EmbFace(buf); remaps[name] = None
                    report.append({"font": name, "source": "embedded"}); continue
                except Exception:
                    pass
            if name in faces or not refs:
                continue
            best = None
            for label, data, ref in refs:
                remap, m, tot, amb = _match(sub, ref)
                if tot and (best is None or m / tot > best[2]):
                    best = (label, data, m / tot, remap, amb, tot)
            if best and best[2] >= MIN_MATCH:
                fc = E.EmbFace(best[1])
                own = collections.Counter(cp & ~0x7F for cp in cps)  # script from the PDF's OWN font, not the reference
                if own:                                             # (a multi-script reference would flip the script)
                    fc.block = own.most_common(1)[0][0]
                else:  # no usable cmap in the subset: use the scripts of the reference glyphs actually mapped
                    rc = {g: cp for cp, g in fc.cmap.items() if 0x0900 <= cp < 0x0E00}
                    blk = collections.Counter(rc[g] & ~0x7F for g in best[3].values() if g in rc)
                    fc.block = blk.most_common(1)[0][0] if blk else fc.block
                lbl = best[0]
                if "Nirmala" in lbl:  # learned Word-vs-HarfBuzz glyph deltas exist for Word + Nirmala UI
                    style = "Bold" if "Bold" in lbl else "Regular"
                    fc.rules = dict(E.U.RULESETS.get(style, {}))
                    fc.wshape = (lambda f: (lambda t, lang: E.U.rewrite(f.shape(t), f.rules.get(lang, ()))))(fc)
                if fc.block is not None and fc.block in E.SCRIPT:
                    faces[name] = fc; remaps[name] = best[3]
                    report.append({"font": name, "source": "reference", "reference": best[0],
                                   "glyph_match": round(best[2], 4), "ambiguous_left_unmapped": best[4]})
                    continue
            if best:
                report.append({"font": name, "source": "none", "best_reference": best[0], "glyph_match": round(best[2], 4)})
    st = collections.Counter(); parts = []
    timed_out = False
    for f, run in _runs(doc, faces, remaps):
        if timed_out or time.time() - t0 > max_seconds:  # out of time: keep the PDF's own text for the rest
            timed_out = True; st["timeout_fallback"] += 1
            parts.append("".join(u if isinstance(u, str) else (chr(u) if u > 0 else "") for _, u in run)); continue
        if f is None:
            parts.append("".join(u for _, u in run)); continue
        parts.append(_decode(run, f, st))
    return {"pdf": Path(path).name, "fonts": report, "stretches": dict(st),
            "text": " ".join(parts), "seconds": round(time.time() - t0, 2)}


def main(argv=None):
    argv = _ARGV if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    refs, files, as_json, maxs, i = [], [], False, 300, 0
    while i < len(argv):
        a = argv[i]
        if a == "--font":
            refs.append(argv[i + 1]); i += 2; continue
        if a == "--max-seconds":
            maxs = float(argv[i + 1]); i += 2; continue
        if a == "--json":
            as_json = True; i += 1; continue
        files.append(a); i += 1
    rows = [decode_pdf(f, refs, maxs) for f in files]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for r in rows:
            print(f"== {r['pdf']}  fonts={r['fonts']}  stretches={r['stretches']}  {r['seconds']}s")
            print(r["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
