# Copyright (c) 2026 Novain Shibu. AGPL-3.0-or-later, or a commercial licence (see COMMERCIAL.md).
"""Route each page of a PDF: keep its own text, use the font-free decode, or send it to OCR.

Usage:  unshape-route file1.pdf [file2.pdf ...] --wordlist LIST [--wordlist LIST2 ...] [--jsonl] [--ocr-below 0.5]

Per page, one of:
  fine     the PDF's own text layer is kept (it has at least as many real words as the decode, or no Indic text)
  decoded  the text layer is garbled; the font-free decode has clearly more real words, and its text is returned
  ocr      no usable text: a scanned page (little or no text, mostly image), or both texts score below --ocr-below
  empty    no text and no image
"Real words" = share of the page's Indic words found in the word list. The decode is only chosen when it beats the PDF's
own text by --min-gain (default 0.05), so PDFs with a correct text layer (e.g. browser-printed) are left alone: the
font-free decoder is built for Word-style layers and can damage correct ones.
Output: one JSON line per page with route, reason, scores and text (use --text-only for plain text pages).
"""
import json, re, sys, unicodedata

import pymupdf

from unshape import fontfree as FF

PUN = "।॥,.:;?!'\"“”‘’()[]-–—@"
INDIC = re.compile(r"[ऀ-෿]")


def indic_words(text):
    out = []
    for w in unicodedata.normalize("NFC", text).split():
        w = w.strip(PUN)
        if w and INDIC.search(w):
            out.append(w)
    return out


def real_share(text, lex):
    ws = indic_words(text)
    return (sum(w in lex for w in ws) / len(ws)) if ws else None


def _image_cover(page):
    """Share of the page area covered by images (overlaps counted once per image, capped at 1)."""
    area = abs(page.rect) or 1.0
    tot = 0.0
    for im in page.get_image_info():
        r = pymupdf.Rect(im["bbox"]) & page.rect
        tot += abs(r)
    return min(1.0, tot / area)


def route_pdf(path, lm=None, min_gain=0.05, ocr_below=0.5, min_words=3):
    """[{page, route, reason, real_layer, real_decoded, indic_words, image_cover, text}] for every page."""
    doc = pymupdf.open(path)
    layers = [page.get_text() for page in doc]
    covers = [_image_cover(page) for page in doc]
    decoded = None
    if lm is not None and any(len(indic_words(t)) >= min_words for t in layers):
        try:
            _, _, decoded = FF.decode_pool([path], lm, keep_label=4, by_page=True)[0]
        except Exception as e:  # never crash a pipeline: report and keep the PDF's own text
            decoded = None
            sys.stderr.write(f"unshape-route: decode failed on {path}: {e!r}\n")
    rows = []
    for i, lay in enumerate(layers):
        nw = len(indic_words(lay)); chars = len(lay.strip())
        row = {"page": i, "indic_words": nw, "image_cover": round(covers[i], 3), "real_layer": None, "real_decoded": None}
        if nw < min_words:
            if chars < 20 and covers[i] >= 0.3:
                row.update(route="ocr", reason="scan: little text, mostly image", text=lay)
            elif chars == 0:
                row.update(route="empty", reason="no text, no image", text="")
            else:
                row.update(route="fine", reason="no Indic text", text=lay)
            rows.append(row); continue
        if lm is None:
            row.update(route="fine", reason="no word list given: not checked", text=lay)
            rows.append(row); continue
        a = real_share(lay, lm.lex)
        dec = decoded[i] if decoded is not None and i < len(decoded) else ""
        b = real_share(dec, lm.lex) if dec else None
        row["real_layer"] = round(a, 3); row["real_decoded"] = None if b is None else round(b, 3)
        if b is not None and b > a + min_gain:
            best, txt = b, dec; row.update(route="decoded", reason="decode has more real words")
        else:
            best, txt = a, lay; row.update(route="fine", reason="own text layer has as many real words")
        if best < ocr_below:
            row.update(route="ocr", reason=f"both texts below {ocr_below:.0%} real words")
        row["text"] = txt
        rows.append(row)
    return rows


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); return 0
    opts = {"--ocr-below": 0.5, "--min-gain": 0.05}
    wl = []
    while "--wordlist" in a:
        i = a.index("--wordlist"); wl.append(a[i + 1]); del a[i:i + 2]
    for k in list(opts):
        if k in a:
            i = a.index(k); opts[k] = float(a[i + 1]); del a[i:i + 2]
    text_only = "--text-only" in a
    a = [x for x in a if x not in ("--jsonl", "--text-only")]
    if not wl:
        sys.stderr.write("unshape-route: no --wordlist given; pages are only checked for scans, not decoded\n")
    lm = FF.load_wordlist(wl) if wl else None
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for f in a:
        for r in route_pdf(f, lm, min_gain=opts["--min-gain"], ocr_below=opts["--ocr-below"]):
            if text_only:
                print(f"== {f} page {r['page'] + 1}: {r['route']}")
                print(r["text"])
            else:
                print(json.dumps({"pdf": f, **r}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
