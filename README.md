# Unshape

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

No third-party documents, fonts or Wikipedia text are included; see SOURCES.md and NOTICE. Licence: Apache-2.0.
