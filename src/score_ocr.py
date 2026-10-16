"""Score the free OCR baseline (Tesseract mal+eng, Kaggle CPU) against ground truth, PyMuPDF and Unshape.

Synthetic PDFs (ground truth known): exact-word recall + CER for OCR, PyMuPDF, Unshape (same pages, same normalisation).
Real PDFs (no ground truth, first N pages OCR'd): share of Unshape's rendering-verified words that OCR / PyMuPDF also produce.
Fair normalisation (skeptic): NFC, ZWJ/ZWNJ removed, old ZWJ-chillus mapped to atomic chillus, punctuation/digits stripped.
"""
import json, re, sys, unicodedata
from collections import Counter
from pathlib import Path

import pymupdf

from baseline_extract import lev

CHILLU = {"ണ്\u200d": "ൺ", "ന്\u200d": "ൻ", "ര്\u200d": "ർ", "ല്\u200d": "ൽ", "ള്\u200d": "ൾ", "ക്\u200d": "ൿ"}


def fair(s):
    s = unicodedata.normalize("NFC", s)
    for a, b in CHILLU.items():
        s = s.replace(a, b)
    s = s.replace("\u200c", "").replace("\u200d", "")
    return [w for w in (re.sub(r"[^ഀ-ൿ]", "", t) for t in s.split()) if w]


def recall(gt, got):
    g, h = Counter(gt), Counter(got)
    return sum((g & h).values()) / max(1, sum(g.values()))


def main(ocr_json):
    d = json.load(open(ocr_json, encoding="utf-8"))
    rows = {}
    for name, r in d["files"].items():
        if "pages" not in r:
            continue
        n = len(r["pages"])
        ocr = "\n".join(r["pages"])
        pdf = Path("real" if not name.startswith("wiki") else ".") / name
        doc = pymupdf.open(pdf)
        mu = "\n".join(doc[i].get_text() for i in range(n))
        row = {"pages": n, "ocr_errors": len(r.get("errors", [])),
               "ocr_sec_per_page": round(sum(r["page_sec"]) / max(1, n), 2)}
        if name.startswith("wiki"):
            tag = name.split("_Nirmala")[0]
            gt = fair(Path(f"gt_{tag}.txt").read_text(encoding="utf-8"))
            ours = fair(Path(name.replace(".pdf", ".unshaped3.txt")).read_text(encoding="utf-8"))
            from pdfminer.high_level import extract_text
            pm = extract_text(str(pdf), page_numbers=list(range(n)))
            for k, txt in (("ocr", fair(ocr)), ("pymupdf", fair(mu)), ("pdfminer", fair(pm)), ("unshape", ours)):
                row[f"{k}_word_recall"] = round(recall(gt, txt), 4)
                row[f"{k}_cer"] = round(lev(" ".join(gt), " ".join(txt)) / max(1, len(" ".join(gt))), 4)
        else:
            pages_words = json.load(open(pdf.with_suffix(".v3words.json"), encoding="utf-8"))
            ver = [w for page in pages_words[:n] for text, ok, has_ml in page if has_ml and ok for w in fair(text)]
            o, m = Counter(fair(ocr)), Counter(fair(mu))
            row["verified_words"] = len(ver)
            row["ocr_has_ours"] = round(sum(1 for w in ver if o[w]) / max(1, len(ver)), 4)
            row["pymupdf_has_ours"] = round(sum(1 for w in ver if m[w]) / max(1, len(ver)), 4)
        rows[name] = row
        print(name, row)
    Path("logs").mkdir(exist_ok=True)
    Path("logs/ocr_scores.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
