"""Build ground-truth Malayalam test documents and print them to PDF with real producers.

Producer 1: Microsoft Edge headless print-to-pdf (Chromium/Skia PDF backend).
Ground truth = the exact text written into the HTML, one paragraph per <p>.
"""
import html, re, subprocess, sys, unicodedata
from pathlib import Path

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
ML = re.compile(r"[\u0D00-\u0D7F]")


def paragraphs(path, n_chars):
    out, total = [], 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if len(line) < 40 or line.startswith("="):
            continue
        # keep mostly-Malayalam paragraphs (drop English-heavy lines)
        ml = len(ML.findall(line))
        if ml < 0.6 * len(line.replace(" ", "")):
            continue
        line = unicodedata.normalize("NFC", line)
        out.append(line)
        total += len(line)
        if total >= n_chars:
            break
    return out


def write_html(paras, font, path):
    body = "\n".join(f"<p>{html.escape(p)}</p>" for p in paras)
    path.write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<style>body{{font-family:'{font}';font-size:14pt;line-height:1.6;margin:2cm}}p{{margin:0 0 1em}}</style>
</head><body>{body}</body></html>""", encoding="utf-8")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "wiki_5545.txt"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 6000
    tag = Path(src).stem
    paras = paragraphs(src, n)
    Path(f"gt_{tag}.txt").write_text("\n".join(paras), encoding="utf-8")
    for font in ["Nirmala UI"]:
        h = Path(f"{tag}_{font.replace(' ', '')}.html").resolve()
        write_html(paras, font, h)
        pdf = h.with_suffix(".edge.pdf")
        subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", h.as_uri()], check=True, timeout=120,
                       capture_output=True)
        print(pdf.name, pdf.stat().st_size, "bytes,", sum(map(len, paras)), "chars")


if __name__ == "__main__":
    main()
