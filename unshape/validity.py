# Copyright (c) 2026 Novain Shibu. AGPL-3.0-or-later, or a commercial licence (see COMMERCIAL.md).
"""Orthographic validity checker for Brahmic-script text (Devanagari, Bengali, Tamil, Malayalam).
Scrambled PDF text layers (visual-order vowel signs, ToUnicode lies, dropped glyphs) create sequences that cannot occur in
correctly spelled Unicode text. Rules (Unicode block offsets are shared across these ISCII-derived blocks):
  R1 dependent vowel sign not preceded by a consonant / nukta (or a vowel sign for the length marks)
  R2 virama not preceded by a consonant / nukta
  R3 candrabindu / anusvara / visarga at the start of a word
A word = maximal run of characters from the script's block (+ ZWJ/ZWNJ). Metric: share of words with >= 1 violation.
Usage: python -I validity.py <finepdfs_sample.json> [...]   (also checks Wikipedia baselines via --wiki)
"""
import json, re, sys, statistics, unicodedata
BASE = {"Deva": 0x0900, "Beng": 0x0980, "Guru": 0x0A00, "Gujr": 0x0A80, "Orya": 0x0B00, "Taml": 0x0B80,
        "Telu": 0x0C00, "Knda": 0x0C80, "Mlym": 0x0D00}   # Sinhala (0D80) has a different layout: not covered

def classes(base):
    cons = set(range(base + 0x15, base + 0x3A)) | set(range(base + 0x58, base + 0x60))
    if base == 0x0D00:
        cons |= {0x0D3A}
    if base == 0x0B00:
        cons |= {0x0B71}
    if base == 0x0980:
        cons |= {0x09F0, 0x09F1}       # Assamese ra / wa (skeptic-style fix: Assamese Wikipedia scored 26% without these)
    dv = set(range(base + 0x3E, base + 0x4D)) | {base + 0x62, base + 0x63}
    length = {base + 0x55, base + 0x56, base + 0x57}
    signs = {base + 0x01, base + 0x02, base + 0x03}
    if base == 0x0B80:
        signs = {0x0B82}               # Tamil aytham (U+0B83) is a letter, legal at word start
    viramas, nukta = {base + 0x4D}, base + 0x3C
    if base == 0x0D00:                 # skeptic fixes: U+0D3B/0D3C are viramas (no nukta in Malayalam); chillus 0D7A-0D7F
        viramas, nukta = {0x0D4D, 0x0D3B, 0x0D3C}, None
    return cons, dv, length, viramas, nukta, signs

#   Non-ISCII complex scripts: (block range, base letters, marks that must follow a base or another mark, word-start-only bad)
OTHER = {
    "Thai": ((0x0E00, 0x0E7F), set(range(0x0E01, 0x0E2F)), {0x0E31} | set(range(0x0E34, 0x0E3B)) | set(range(0x0E47, 0x0E4F))),
    "Khmr": ((0x1780, 0x17FF), set(range(0x1780, 0x17B4)), set(range(0x17B6, 0x17D4))),
    "Sinh": ((0x0D80, 0x0DFF), set(range(0x0D85, 0x0DC7)), set(range(0x0DCA, 0x0DE0)) | {0x0DF2, 0x0DF3, 0x0D82, 0x0D83}),
    "Mymr": ((0x1000, 0x109F), set(range(0x1000, 0x102B)) | set(range(0x103F, 0x1056)), set(range(0x102B, 0x103F)) | set(range(0x1056, 0x105A))),
}
PUA_THAI = set(range(0xF700, 0xF71B))   # legacy Thai font glyph-variant code points leaking into text = broken text layer


def other_word_ok(w, script):
    (lo, hi), base, marks = OTHER[script]
    cps = [ord(c) for c in w if c not in "‌‍"]
    for i, c in enumerate(cps):
        if c in PUA_THAI:
            return False
        if c in marks:
            if i == 0:
                return False
            p = cps[i - 1]
            if p not in base and p not in marks:
                return False
    return True


def scan_other(text, script, collect=None):
    text = unicodedata.normalize("NFC", text)
    (lo, hi), base, marks = OTHER[script]
    words = re.findall(f"[\\u{lo:04x}-\\u{hi:04x}\\u200c\\u200d\\uf700-\\uf71a]+", text)
    words = [w for w in words if any(lo <= ord(c) <= hi for c in w)]
    badw = [w for w in words if not other_word_ok(w, script)]
    if collect is not None:
        collect.extend(badw)
    return len(words), len(badw)


def word_ok(w, cl):
    cons, dv, length, viramas, nukta, signs = cl
    cps = [ord(c) for c in w if c not in "‌‍"]
    mal = 0x0D4D in viramas
    for i, c in enumerate(cps):
        prev = cps[i - 1] if i else None
        if c in dv and prev not in cons and not (nukta is not None and prev == nukta):
            return False
        if c in length and prev not in dv and prev not in cons:
            return False
        if c in viramas and prev not in cons and not (nukta is not None and prev == nukta):
            # Malayalam: chillu + virama (nta form) and samvruthokaram (u-sign + virama) are standard spellings
            if not (mal and prev is not None and (0x0D7A <= prev <= 0x0D7F or prev == 0x0D41)):
                return False
        if c in signs and i == 0:
            return False
    return True

def scan_text(text, script, collect=None):
    if script in OTHER:
        return scan_other(text, script, collect)
    text = unicodedata.normalize("NFC", text)
    base = BASE[script]
    cl = classes(base)
    words = re.findall(f"[\\u{base:04x}-\\u{base + 0x7F:04x}\\u200c\\u200d]+", text)
    words = [w for w in words if any(base <= ord(c) <= base + 0x7F for c in w)]
    badw = [w for w in words if not word_ok(w, cl)]
    if collect is not None:
        collect.extend(badw)
    return len(words), len(badw)

def report(name, docs, script):
    W = B = 0; per = []
    for t in docs:
        w, b = scan_text(t, script)
        W += w; B += b
        if w >= 50:
            per.append(b / w)
    over = sum(p > 0.05 for p in per)
    print(f"{name:28s} docs {len(docs):4d} | words {W:8d} | invalid {B:7d} = {B / max(W, 1):6.2%} | "
          f"docs(>=50 words) with >5% invalid: {over}/{len(per)}"
          + (f" | median doc {statistics.median(per):.2%}" if per else ""))
    return per

if __name__ == "__main__":
    for path in sys.argv[1:]:
        script = re.search(r"_(" + "|".join(list(BASE) + list(OTHER)) + ")", path).group(1)
        data = json.load(open(path, encoding="utf-8"))
        docs = [d["text"] if isinstance(d, dict) else d for d in data]
        report(path.split("/")[-1].split("\\")[-1], docs, script)
