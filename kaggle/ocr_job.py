"""Kaggle CPU job: free OCR baseline (Tesseract, Apache-2.0, Malayalam model) on our test PDFs.

Installs from Ubuntu's official apt repository (fallback: Tesseract's official tessdata_fast model on GitHub).
Renders every page at 300 dpi with PyMuPDF, OCRs with `tesseract -l mal+eng`, writes results incrementally to
/kaggle/working/ocr_results.json (a crash keeps earlier files). Time cap: 40 min total.
"""
import glob, json, os, shutil, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor

T0 = time.time()
CAP = 30 * 60
MAX_PAGES = 15
OUT = "/kaggle/working/ocr_results.json"
LOG = []


def log(msg):
    line = f"[{time.time() - T0:7.1f}s] {msg}"
    print(line, flush=True)
    LOG.append(line)


def sh(cmd, timeout=600):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)[-2000:]


def setup():
    code, out = sh("apt-get -qq update && DEBIAN_FRONTEND=noninteractive apt-get -qq install -y tesseract-ocr tesseract-ocr-mal", 900)
    log(f"apt install rc={code} {out[-300:]}")
    code, out = sh("tesseract --version")
    log(f"tesseract: {out.splitlines()[0] if out else '?'}")
    code, langs = sh("tesseract --list-langs")
    if "mal" not in langs.split():
        # fallback: official model file from the Tesseract project
        tdir = "/kaggle/working/tessdata"
        os.makedirs(tdir, exist_ok=True)
        for lang in ("mal", "eng"):
            sh(f"curl -sL -o {tdir}/{lang}.traineddata https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata", 300)
        os.environ["TESSDATA_PREFIX"] = tdir
        code, langs = sh("tesseract --list-langs")
    log(f"langs: {langs.split()[-6:]}")
    code, out = sh(f"{sys.executable} -m pip install -q pymupdf", 600)
    log(f"pip pymupdf rc={code}")
    return "mal" in langs.split()


def ocr_page(args):
    pdf, pno, workdir = args
    import pymupdf
    try:
        png = os.path.join(workdir, f"{os.path.basename(pdf)}_{pno}.png")
        pymupdf.open(pdf)[pno].get_pixmap(dpi=300).save(png)
        t = time.time()
        env = dict(os.environ, OMP_THREAD_LIMIT="1")  # run 1: 4 procs x N threads thrashed -> every page timed out
        r = subprocess.run(["tesseract", png, "stdout", "-l", "mal+eng", "--psm", "3"],
                           capture_output=True, text=True, timeout=120, env=env)
        os.remove(png)
        return pno, r.stdout, time.time() - t, r.stderr[-200:] if r.returncode else ""
    except Exception as e:  # one bad page never stops the run
        return pno, "", 0.0, f"ERR {e!r}"[:300]


def main():
    results = {"log": LOG, "files": {}}
    ok = setup()
    if not ok:
        log("NO Malayalam model - abort")
        json.dump(results, open(OUT, "w"), ensure_ascii=False, default=float)
        return
    import pymupdf
    pdfs = sorted(glob.glob("/kaggle/input/**/*.pdf", recursive=True), key=lambda p: (not os.path.basename(p).startswith("wiki"), p))
    log(f"{len(pdfs)} PDFs: {[os.path.basename(p) for p in pdfs]}")
    # preflight: time ONE page first
    workdir = "/kaggle/working/tmp"
    os.makedirs(workdir, exist_ok=True)
    if pdfs:
        pno, txt, sec, err = ocr_page((pdfs[0], 0, workdir))
        log(f"preflight page: {sec:.1f}s, {len(txt)} chars, err={err!r}")
    ncpu = os.cpu_count() or 2
    for pdf in pdfs:
        if time.time() - T0 > CAP:
            log("time cap reached; stopping")
            break
        name = os.path.basename(pdf)
        try:
            n = min(MAX_PAGES, len(pymupdf.open(pdf)))
            t = time.time()
            with ProcessPoolExecutor(max_workers=ncpu) as ex:
                futs = [ex.submit(ocr_page, (pdf, i, workdir)) for i in range(n)]
                pages = []
                for f in futs:  # per-page cap: stop collecting once over budget
                    left = CAP - (time.time() - T0)
                    if left <= 0:
                        break
                    try:
                        pages.append(f.result(timeout=left))
                    except Exception as e:
                        pages.append((len(pages), "", 0.0, f"ERR {e!r}"[:200]))
                for f in futs:
                    f.cancel()
            results["files"][name] = {"n_pages_total": len(pymupdf.open(pdf)), "pages": [p[1] for p in pages], "page_sec": [p[2] for p in pages],
                                      "errors": [p[3] for p in pages if p[3]], "wall_sec": time.time() - t,
                                      "workers": ncpu}
            log(f"{name}: {n} pages in {time.time() - t:.1f}s")
        except Exception as e:
            results["files"][name] = {"error": repr(e)}
            log(f"{name}: ERROR {e!r}")
        json.dump(results, open(OUT, "w"), ensure_ascii=False, default=float)
    shutil.rmtree(workdir, ignore_errors=True)
    json.dump(results, open(OUT, "w"), ensure_ascii=False, default=float)
    log("done")


if __name__ == "__main__":
    main()
