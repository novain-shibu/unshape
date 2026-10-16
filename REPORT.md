# Unshape: rendering-verified Malayalam text recovery from born-digital PDFs by inverting OpenType shaping

**Author:** Novain Shibu. **Status:** research note, version 0.1 (2026-10-16). Prototype code, not a product.
**AI assistance:** the code, experiments and this report were produced with the help of an AI assistant (Claude, Anthropic),
directed and reviewed by the author.

## Summary

Text in complex scripts such as Malayalam is often garbled when it is copied or extracted from PDFs. The PDF stores *glyph IDs*
(shapes drawn in visual order, after ligatures and reordering), plus an optional, often wrong, glyph-to-Unicode table. Common
extractors trust that table. We test a different route: identify the exact font build, then search for the Unicode text whose
OpenType shaping (HarfBuzz) reproduces the PDF's glyph IDs. Each recovered word is *rendering-verified*: re-shaping it with the
identified font gives exactly the glyph sequence in the file.

Main results (all numbers regenerated from raw logs by `src/make_results.py`; see `results/RESULTS.md`):

- **Synthetic PDFs with known text** (2 Malayalam Wikipedia excerpts, Nirmala UI, printed by Word 365 and Edge): exact-word recall
  **Unshape 99.1-99.5%**, free **Tesseract OCR 92.9-93.9%**, PyMuPDF 33.8-81.4%, pdfminer.six 33.8-56.4%.
- **Real public PDFs where the font build is in our catalogue:** 10 Kerala Gazette PDFs (Word 2019): **94.8%** of Malayalam words
  rendering-verified (20,950 / 22,099; 95% bootstrap CI over PDFs 93.2-95.3%); 5 Microsoft Print-to-PDF files: **91.2%** (2,061 /
  2,260). On the same pages, the free OCR output contains 93.3-99.4% of our verified words; PyMuPDF's output contains 22.3-43.1%.
- **Unseen source with a frozen font catalogue (pre-registered): 0%.** 6 born-digital orders from a different publisher used a
  font build we did not have; nothing could be verified. 4 more were scans (OCR only).

**Honest reading.** The method is strong when the exact font build is known (or the PDF embeds the font with its shaping tables):
it beats free OCR by about 5-6 points of exact words on synthetic data and attaches a per-word check. It is **not** a general
solution: on an unseen source it failed completely, and free OCR already recovers most words. Text-layer extractors that trust
the PDF's own tables (PyMuPDF, pdfminer) garble most Malayalam words in these files.

## 1. Problem

A born-digital PDF draws text as glyph IDs of an embedded (usually subset) font. For Malayalam, shaping reorders some vowel
signs before the consonant and merges consonant clusters into single conjunct glyphs, so glyph order and count differ from the
Unicode text. Extractors map each glyph back through the PDF's ToUnicode table. In our files those tables were often incomplete
or wrong; one producer (Word) even labelled vowel-sign glyphs as SPACE, which splits words. The MuPDF developers described this
case (Devanagari) as impossible to fix in the extractor (PyMuPDF issue #4805, closed as won't fix, 2025). Large Indic corpus
projects OCR PDFs instead (IndicLLMSuite / Sangraha, arXiv:2403.06350, reports OCR via a cloud service for about 507k PDFs).

## 2. Method

1. **Trace glyphs** with pdfminer.six: subset font name, character code, position, producer Unicode.
2. **Get the font.** (a) If the embedded program still has its GSUB/GPOS shaping tables (e.g. Microsoft Print to PDF), use it
   directly. (b) Otherwise *fingerprint* the subset: compare its glyph outlines with every build in a catalogue of historical
   font versions and map subset glyph IDs to the matching build's glyph IDs.
3. **Invert shaping.** Shape every syllable from a syllable inventory (held-out Wikipedia text + combinatorial C, CV, C+virama+C(V)
   and chillu forms) with HarfBuzz, giving a table from glyph sequence to syllables. Decode each word by k-best dynamic
   programming over this table.
4. **Verify.** Accept a decoding only if shaping the decoded word reproduces the observed glyph IDs exactly. Unverified words fall
   back to the producer's text and are counted as unverified.
5. **Word boundaries** come from the font's space glyph (blank glyph with width); a zero-width blank glyph is a shaper-rendered
   ZWNJ; a carriage return without a space glyph (Chromium) also ends a word.

## 3. What "verified" means (and does not mean)

`shape(text) == glyph IDs` proves the recovered text **renders identically** with the identified font build. It does not identify
the author's exact code points: spellings that draw the same glyphs are indistinguishable (atomic chillu vs ZWJ sequence such as
ൻ vs ന്‍; ന്റ vs ൻ്റ; ZWJ vs ZWNJ). We output one canonical spelling (NFC, atomic chillus). If the fingerprint picked a wrong but
outline-identical build, verification is only relative to that build.

## 4. Data and setup

- Synthetic: text from Malayalam Wikipedia (CC BY-SA 4.0; not redistributed here, `src/fetch_text.py` re-downloads it), printed
  to PDF with Microsoft Word 365 and Microsoft Edge (`src/make_pdfs.py`, Windows). Inventory built only from three *other* articles.
- Real: public documents from official Kerala sources, used for measurement only and **not redistributed**; links in `SOURCES.md`.
- Free OCR baseline: Tesseract 5.3.4 with its `mal+eng` models (Ubuntu packages), 300 dpi page images, on a free Kaggle CPU
  session (`kaggle/ocr_job.py`); first 15 pages per PDF.
- Scoring: NFC, joiners removed, old ZWJ chillus mapped to atomic chillus, punctuation and digits removed (`src/score_ocr.py`).
- Compute: laptop CPU + 2 free Kaggle CPU sessions. GPU: none.

## 5. Results

See `results/RESULTS.md` (generated). Key points:

| Setting | Unshape | Free OCR (Tesseract) | PyMuPDF | pdfminer.six |
|---|---|---|---|---|
| Synthetic, exact words (4 PDFs) | 99.1-99.5% | 92.9-93.9% | 33.8-81.4% | 33.8-56.4% |
| Synthetic, character error rate | 0.07-0.20% | 1.00-1.58% | 2.3-20.2% | 11.6-20.3% |

| Real PDFs | PDFs | Malayalam words | Rendering-verified |
|---|---|---|---|
| Kerala Gazette, Word 2019 (one source, one font build) | 10 | 22,099 | 94.8% (CI 93.2-95.3%) |
| Microsoft Print to PDF (embedded fonts with shaping tables) | 5 | 2,260 | 91.2% (CI 86.7-94.7%) |
| LibreOffice (only 3 Malayalam words in the file) | 1 | 3 | 3/3 |
| **Unseen source, frozen catalogue (pre-registered)** | 6 | 1,857 | **0%** |

On real files "in OCR output" is an overlap measure (does the OCR text contain the word anywhere), not OCR accuracy; OCR accuracy
was measured only on the synthetic files. Speed: OCR took 3.5-8.0 s per page. The unoptimised prototype took 0.3-58 s per PDF
end to end (`sec` in `results/logs/v3_*.json`), dominated by one-time font fingerprinting; the decoding step itself runs in
milliseconds per page.

## 6. Negative results and limits (read these first)

- **Font builds are the bottleneck.** The real-PDF successes needed the exact historical build (Noto Serif Malayalam v1.01 from
  2016; Rachana from 2013). These builds were added to the catalogue *while* inspecting the same PDFs. On a fresh source with the
  catalogue frozen beforehand, recovery was 0/6: the files used Meera "04, revision 0.200"; eight other Meera builds (2008-2020)
  matched 0-29 of 185 glyph outlines.
- **Narrow evidence.** The 10 gazette PDFs come from one website, one producer and one font build, and include near-duplicates
  (two promulgations of the same ordinance account for about 55% of the words); the confidence interval is therefore optimistic.
  Print-to-PDF: 5 files from 2 websites.
- **Free OCR is a strong baseline** (about 93-94% exact words on synthetic text) and also works on scans, which this method cannot
  read (4 of the 10 unseen files were scans).
- Not rendering-identical spellings cannot be told apart (section 3). Legacy ASCII-encoded Malayalam fonts (ML-TT family, seen in
  university notices) are not handled. Font fingerprinting compares outlines only and breaks ties by file order.
- Synthetic tests used the generating font directly; small sample (2 articles, about 1,270 words, 2 renderers).
- Bugs found by an independent review during the work and fixed before these numbers: words were split on a producer's
  mislabelled "space" (dropping vowel signs while both halves verified); an earlier raw log was overwritten. Both are fixed.

## 7. Prior art and novelty check (2026-10-16)

Searched: arXiv (API, newest first; terms: ToUnicode; shaping AND glyph AND PDF; OpenType AND PDF/extraction/recover; "text
extraction" AND glyph; "analysis-by-synthesis" AND text AND PDF; Malayalam AND PDF; born-digital AND Indic/Devanagari/Hindi/
Tamil/Bengali; "font subset"/"embedded font"; Indic AND PDF AND corpus/extraction; HarfBuzz; GSUB; "legacy font"); GitHub
repositories and issues ("harfbuzz pdf text extraction", "reverse shaping glyph", "unshape pdf", "indic pdf glyph unicode",
"malayalam pdf unicode", "tounicode repair indic", "pdf hindi text extraction fix"); issue trackers of pdf.js, PyMuPDF,
pdfminer.six and docling for Hindi/Devanagari/Malayalam/Bengali/Tamil; web search. We found no paper or general tool that
recovers Indic text from PDFs by inverting shaping with a re-shaping check. Related: manual glyph-mapping tools
(shreevatsa/pdf-glyph-mapping), legacy-font converters (Payyans, legacylipi), generator-side fixes (writing ActualText/ToUnicode
correctly, e.g. LuaTeX/HarfBuzz work), one project-specific decoder for a single Hindi font (repository no longer available).
The idea of reversing glyph substitutions is known in principle; to our knowledge this is the first general, measured
implementation with a per-word re-shaping check. We would welcome pointers to earlier work.

## 8. Reproduce

```
cd src
python fetch_text.py          # Malayalam Wikipedia ground truth (CC BY-SA)
python fetch_fonts.py         # font builds from official sources, hash-checked
python make_pdfs.py wiki_5545.txt 6000   # Windows only (Word/Edge); Nirmala UI is a local Windows font
python unshape3.py <pdfs...>  # decode + verify; writes logs/v3_*.json
python make_results.py        # regenerates results/RESULTS.md from results/logs
```
Requirements: Python 3.12, uharfbuzz, fontTools, pdfminer.six, PyMuPDF.
