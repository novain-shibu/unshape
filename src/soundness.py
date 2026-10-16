"""Soundness of the answer key: among runs whose every Malayalam stretch VERIFIED (shape(decoded) == glyphs),
how many differ from ground truth? Uses Word PDFs (space glyph at every word end -> runs align with gt tokens)."""
import sys, unicodedata
from collections import Counter
from pathlib import Path

from baseline_extract import norm
from unshape import Shaper, build_inventory, decode_run, glyph_runs

pdf, gtf, held = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
shape = Shaper()
table, maxlen, _ = build_inventory(shape, held)
runs, soft = glyph_runs(pdf)
gt = norm(Path(gtf).read_text(encoding="utf-8")).split()
print("runs", len(runs), "gt tokens", len(gt), "soft", len(soft))
import difflib
dec, ver = [], []
for r in runs:
    st = Counter()
    dec.append(norm(decode_run(r, shape, table, maxlen, st)))
    ver.append(st["verified"] > 0 and st["unverified"] == 0 and st["nocover"] == 0)
pairs = []  # (decoded index, gt word) for 1:1 aligned positions
for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, dec, gt, autojunk=False).get_opcodes():
    if op in ("equal", "replace") and a2 - a1 == b2 - b1:
        pairs += [(a1 + k, gt[b1 + k]) for k in range(a2 - a1)]
print("aligned 1:1:", len(pairs), "of", len(dec))
c = Counter()
for i, g in pairs:
    w, allver = dec[i], ver[i]
    ok = w == g
    c[("verified" if allver else "other", ok)] += 1
    if allver and not ok:
        print("  VERIFIED BUT DIFFERENT:", g, "=>", w)
print(dict(c))
