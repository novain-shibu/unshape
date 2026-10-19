# Unshape

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23418945.svg)](https://doi.org/10.5281/zenodo.23418945)

**Exact text from Indian-language PDFs whose text layer is broken - without OCR, and without inventing words.**

Many PDFs in Hindi and other Indian scripts look right on screen, but the text that copy-paste, search, screen readers and
AI pipelines read is garbled (Word and older LibreOffice exports are common causes). Unshape recovers the text from the
glyphs in the file: it searches for the Unicode text whose OpenType shaping (HarfBuzz) reproduces the PDF's glyphs, and
accepts a stretch only if re-shaping it gives exactly those glyphs ("render-verified"). When the embedded font has lost
its shaping tables (as in Word exports), Unshape matches each glyph's outline to the **original font that you supply**
and uses that font's shaping rules. Stretches it cannot verify keep the PDF's own text and are counted as fallbacks.

## New in v0.3 (milestone): font-free mode - no font file needed
`python -m unshape.fontfree` recovers the text **without the original font and without OCR**, from the PDF alone plus a
plain word list. Observed in Word-made PDFs: Word keeps the font's own glyph numbers, and its text table labels each glyph
with text taken from the first syllable the glyph appeared in (often the neighbour's text). Each glyph therefore has one
fixed but often wrong label - a substitution code. The decoder chooses one meaning per glyph so that the most words become
real words (character model + word list), turns visual order into logical order (pre-base vowel signs, reph), and reads
nukta forms from the PDF's own glyph outlines. PDFs made with the same font can be decoded together.

| Pre-registered test (exact words, fresh files, one run) | Font-free | Tesseract 5.3.4 OCR | PyMuPDF text layer |
|---|---|---|---|
| Hindi, Word for Microsoft 365, **Mangal** (3 Wikipedia texts, 592 words) - fonts never seen while developing | **97.8%** | 95.8% | 70.6% |
| Same texts, **Kokila** | **97.3%** | 96.3% | 69.3% |
| Earlier decoder version, Hindi Word + Nirmala UI, 2 files (bar was 90%) | 84.8% (below bar) | - | 66.4% |
| Legacy 8-bit fonts (Kruti-style, SM-Tamil), first try | 0/18 words - does not work yet | - | - |

Development results only (not yet a fair test): Tamil 99.4%, Bengali 82%; Telugu, Malayalam, Sinhala not working yet
(35-55%). The released command reproduces the Mangal result exactly (579/592) with only the FrequencyWords Hindi list.

```
python -m unshape.fontfree file.pdf [more PDFs made with the same font ...] --wordlist hi_full.txt
```
Word list (not included; CC BY-SA 4.0, from FrequencyWords):
`https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/hi/hi_full.txt`

Also new in v0.3: crash fixes for embedded fonts without a character table and for non-Indic fonts with shaping tables;
when no supplied font matches, Unshape now looks for an installed font with the same name (for example Mangal) on your
own machine. Output on all earlier test files is byte-identical to v0.2.1 (23/23).

## Results (v0.2) - every test listed
| Test (exact words) | Unshape | PyMuPDF text layer |
|---|---|---|
| Word 365 PDFs, 10 Indian scripts + Sinhala (held-out bold/italic files, 18 documents), first frozen run | **99.43%** (7123/7164) | - |
| same set after later fixes (set was then used for diagnosis, so not blind any more) | 99.62% (7137/7164) | - |
| Blind real PDFs, one publisher (LibreOffice + Lohit Devanagari), random draw, 4 documents (set C) | **98.5%** (196/199) | 72.4% |
| Blind real PDFs, same publisher, earlier code, 4 documents (test 50) | **95.2%** (177/186) | 82.3% |
| Blind real PDFs, same publisher, 4 documents (set B) | 81.3% (217/267) | - |
| Blind real PDFs, same publisher, 6 documents (set D) | 90.3% (334/370) | - |
| The Hindi PDF attached to PyMuPDF issue #4805 (Word 2021 + Noto Sans Devanagari), page 1, re-run with v0.2 and Noto Sans Devanagari 2.002 | **100%** (129/129) | 87.6% |

Why sets B and D are lower: set B contained justified text without space characters and digits in a second font (both
fixed since; checked on set C). In set D, Word had embedded an **older Lohit version** (2.5.3) while only 2.95.4 was
supplied; supplying both versions fixes it, checked so far **only on these same files**.
Truth for every real-PDF test was read from the page images and written down before decoding.

Side-by-side with Nepali Reader (an NVDA add-on that reads broken Devanagari PDFs, see Related work), same files, same
scorer (development files): Word-made Hindi PDFs - both 100% / 99.4%, Nepali Reader much faster; Word-made PDFs in nine
other scripts - Unshape 97.7-100%, Nepali Reader 4-40% (it is designed for Devanagari); a LibreOffice + Lohit Hindi page -
Unshape 100%, Nepali Reader 55.6%.

## Use
```
pip install git+https://github.com/novain-shibu/unshape            # installs the `unshape` command (Python 3.10+)
unshape file.pdf --font Lohit-Devanagari.ttf --font NotoSansDevanagari-Regular.ttf
unshape file.pdf --font ... --json      # per-font glyph-match report, verified/fallback counts, text
```
Supply the original fonts of the document: free fonts (Noto, Lohit - if a document was made with an older font version,
supply that version too) or fonts installed on your own system under their own licences (for Word documents, often
Nirmala UI or Mangal). Check first whether a PDF is broken at all with
[indic-pdf-check](https://github.com/novain-shibu/indic-pdf-check); on an already-clean text layer Unshape can be slightly
worse than plain extraction (one word in 66 in a blind test).

## Limits (read before relying on it)
- **Font-based mode needs the original font** (or the embedded font with its shaping tables). Without it, use the
  font-free mode (v0.3), which so far is proven only on Hindi Word for Microsoft 365 files of a few hundred words or more;
  very short documents need other PDFs of the same font decoded together.
- **Real web Hindi PDFs with Mangal (font-based mode, v0.2.1, 10 documents, pre-registered):** 54.3% vs PyMuPDF 51.1% -
  below the bar. Three documents crashed (fixed in v0.3), three used another font or an older Mangal build; on the four
  whose embedded Mangal matched the installed one, 134/144 words (93.1%, worked out after the test). Not re-tested on
  fresh documents yet. In a random sample of 150 web Hindi PDFs (FinePDFs originals), none used Lohit, so the Lohit
  results above describe one publisher's production chain. Users supply their own licensed fonts.
- **No scans.** Pages without a text layer need OCR.
- **"Verified" means "renders identically".** Rarely two spellings render the same; then a character language model
  breaks the tie and the stretch is counted as ambiguous.
- **Speed:** about 2-60 seconds per document (lookup tables are built on first use of each font and script).
- Known issues: a few unverified stretches fall back to the PDF's garbled text; one Malayalam Word reordering case.

## Companion tool
**[SeenRead](https://github.com/novain-shibu/seenread)** (same author, AGPL-3.0 or commercial) proves, word by word and without OCR, whether a PDF's extracted text is the text a human sees - including font-remap lies and hidden text - and uses Unshape for Indian scripts.

## Related work
- **Nepali Reader** (github.com/SuyogDhungel/nepaliReader, NVDA add-on, since 2026-10-05): reads broken Devanagari PDFs
  inside the NVDA screen reader by matching glyph outlines to known Devanagari fonts; glyph-outline matching against
  known fonts appeared there before Unshape's reference-font path.
- **indic-legacy-pdf** (github.com/harinaralasetty/indic-legacy-pdf, since 2026-10-13): legacy-font conversion and Telugu
  glyph-ID recovery by HarfBuzz reverse shaping with a run-level reshape consistency check, for PDFs whose embedded fonts
  keep their shaping tables.
- **pdf-glyph-mapping** (shreevatsa): manual glyph-to-Unicode mapping for Indic PDFs, written back as /ActualText.
- **MuPDF bug 709791 / PyMuPDF #5155**: proposed fix that writes /ActualText for shaped Devanagari when PDFs are created.
- Legacy-font converters (e.g. LegacyLipi, Kruti Dev converters) address a different problem (8-bit legacy fonts).

**Correction to v0.1.0:** its report said "to our knowledge this is the first general, measured implementation with a
per-word re-shaping check". We have since found indic-legacy-pdf (published three days before v0.1.0), which reverse-shapes
Telugu with HarfBuzz and checks runs by re-shaping. We withdraw the "first" wording. What we claim instead: render-verified
recovery across ten Indian scripts and Sinhala, reference-font matching for fonts that lost their shaping tables, and
accuracy measured on blind tests with all inputs reported.

## Licence and commercial use
**AGPL-3.0-or-later**, or a **commercial licence** for closed-source products, internal pipelines and hosted services -
see [COMMERCIAL.md](COMMERCIAL.md). **Open for talks** about commercial licences, integration into PDF / OCR / document-AI
and training-data pipelines, custom work for further scripts and fonts, and research partnerships:
**Novain Shibu** · georgeshibu380@gmail.com · [github.com/novain-shibu](https://github.com/novain-shibu) ·
[linkedin.com/in/novain-shibu](https://www.linkedin.com/in/novain-shibu).
`unshape/data/` is Wikipedia text under CC BY-SA 4.0 ([attribution](unshape/data/ATTRIBUTION.md)). Version 0.1.0 (below) remains available
under Apache-2.0. Parts of this work were produced with AI assistance (Claude, Anthropic), directed and reviewed by the
author. Related audit: [finepdfs-audit](https://github.com/novain-shibu/finepdfs-audit). Cite via [CITATION.cff](CITATION.cff).

---

## v0.1.0 research note (Malayalam prototype, Apache-2.0)

Rendering-verified Malayalam text recovery from born-digital PDFs by **inverting OpenType shaping**: identify the exact font
build, search for the Unicode text whose HarfBuzz shaping reproduces the PDF's glyph IDs, and accept a word only if re-shaping
it gives exactly the glyphs in the file.

Research note + prototype. Read **[REPORT.md](REPORT.md)** (method, results, limits) and **[results/RESULTS.md](results/RESULTS.md)**
(generated from the raw logs).

| | Exact words (synthetic) | Real PDFs, known font build | Unseen source, frozen catalogue |
|---|---|---|---|
| Unshape | 99.1-99.5% | 91-95% rendering-verified | **0%** |
| Free OCR (Tesseract) | 92.9-93.9% | - | works (incl. scans) |
| PyMuPDF / pdfminer.six | 33.8-81.4% / 33.8-56.4% | 22-43% of our verified words present | - |

**Main limit:** it only works when the exact font build is available (in a catalogue, or embedded with its shaping tables).
On a new publisher's files it recovered nothing. "Verified" means the text renders identically, not that the author's exact code
points are known.

(v0.1.0 included no third-party documents, fonts or Wikipedia text; see SOURCES.md. v0.1.0 licence: Apache-2.0.)
