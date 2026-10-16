"""Survey real PDFs: producer, fonts, encoding, and how many glyphs are Malayalam per the producer's own map."""
import sys
from collections import Counter

import pymupdf

for pdf in sys.argv[1:]:
    doc = pymupdf.open(pdf)
    md = doc.metadata or {}
    fonts = Counter()
    ml = Counter()
    for page in doc:
        for f in page.get_fonts(full=True):
            fonts[(f[3], f[1], f[5])] += 1  # basefont, type, encoding
        for span in page.get_texttrace():
            n = sum(1 for c in span["chars"] if 0x0D00 <= c[0] <= 0x0D7F)
            ml[span["font"]] += n
    print(f"== {pdf} pages={len(doc)} producer={md.get('producer')!r} creator={md.get('creator')!r}")
    for (name, typ, enc), n in fonts.most_common(8):
        print(f"   {name:40s} {typ:10s} {enc:18s} pages={n}")
    print("   Malayalam-mapped glyphs by font:", dict(ml.most_common(5)))
