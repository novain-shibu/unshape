"""Probe: do glyph IDs in the PDF equal HarfBuzz shaping of the ground-truth words with the full font?"""
import sys, unicodedata
from pathlib import Path

import pymupdf
import uharfbuzz as hb
from fontTools.ttLib import TTCollection

TTC = r"C:\Windows\Fonts\Nirmala.ttc"


def face_index(family="Nirmala UI", sub="Regular"):
    for i, f in enumerate(TTCollection(TTC).fonts):
        n = f["name"]
        if n.getDebugName(1) == family and n.getDebugName(2) == sub:
            return i
    raise SystemExit("face not found")


def shaper():
    idx = face_index()
    blob = hb.Blob.from_file_path(TTC)
    font = hb.Font(hb.Face(blob, idx))

    def shape(text):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(font, buf, {})
        return [g.codepoint for g in buf.glyph_infos]
    return shape, idx


def pdf_words(pdf):
    """Yield (fontname, [gid,...]) per whitespace-delimited glyph run, in content-stream order."""
    doc = pymupdf.open(pdf)
    for page in doc:
        for span in page.get_texttrace():
            cur = []
            for ch in span["chars"]:
                uni, gid = ch[0], ch[1]
                if uni == 32 or gid in (3,):
                    if cur:
                        yield span["font"], cur
                    cur = []
                else:
                    cur.append(gid)
            if cur:
                yield span["font"], cur


def main():
    pdf, gtf = sys.argv[1], sys.argv[2]
    shape, idx = shaper()
    gt = unicodedata.normalize("NFC", Path(gtf).read_text(encoding="utf-8")).split()
    truth = {}
    for w in gt:
        truth.setdefault(tuple(shape(w)), w)
    runs = list(pdf_words(pdf))
    fonts = {}
    for f, g in runs:
        fonts.setdefault(f, [0, 0])
        fonts[f][0] += 1
        fonts[f][1] += tuple(g) in truth
    print("face index", idx, "| runs", len(runs))
    for f, (n, hit) in fonts.items():
        print(f"  font {f}: {hit}/{n} glyph runs equal HarfBuzz(gt word)")
    print("first runs:", runs[:3])
    print("first gt shaped:", [shape(w) for w in gt[:3]])


if __name__ == "__main__":
    main()
