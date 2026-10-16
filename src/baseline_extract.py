"""Baseline: how well do today's extractors recover Malayalam from born-digital PDFs?

Metric: character error rate (Levenshtein / len(gt)) after NFC + whitespace collapse, and the share of
ground-truth words recovered exactly (multiset match).
"""
import re, sys, unicodedata
from collections import Counter
from pathlib import Path

import pymupdf
from pdfminer.high_level import extract_text


def norm(s):
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\u200d", "").replace("\u200c", "")
    return re.sub(r"\s+", " ", s).strip()


def lev(a, b):
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def word_recall(gt, got):
    g, h = Counter(gt.split()), Counter(got.split())
    return sum((g & h).values()) / max(1, sum(g.values()))


def score(gt, got):
    gt, got = norm(gt), norm(got)
    return lev(gt, got) / max(1, len(gt)), word_recall(gt, got)


def main():
    pdf, gtf = sys.argv[1], sys.argv[2]
    gt = Path(gtf).read_text(encoding="utf-8")
    doc = pymupdf.open(pdf)
    mu = "\n".join(p.get_text() for p in doc)
    pm = extract_text(pdf)
    for name, txt in [("pymupdf", mu), ("pdfminer", pm)]:
        cer, wr = score(gt, txt)
        print(f"{name:9s} CER {cer:6.1%}  exact-word recall {wr:6.1%}")
    print("sample gt :", norm(gt)[:80])
    print("sample mu :", norm(mu)[:80])


if __name__ == "__main__":
    main()
