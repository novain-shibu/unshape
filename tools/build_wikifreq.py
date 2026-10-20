# Copyright (c) 2026 Novain Shibu. AGPL-3.0-or-later, or a commercial licence (see COMMERCIAL.md).
"""Build a word-frequency list from the official Wikipedia dump of one language, for unshape.fontfree.

usage: python tools/build_wikifreq.py LANG OUT.txt      (LANG: hi, bn, ta, ml, te, si, ...)
Downloads https://dumps.wikimedia.org/{LANG}wiki/latest/{LANG}wiki-latest-pages-articles.xml.bz2 (about 50 MB to
1 GB depending on the language; Wikipedia text is CC BY-SA 4.0) and writes "word count" lines (count >= 3), keeping only
words written entirely in the language's script. Takes minutes to about half an hour.
"""
import bz2, collections, os, re, sys, urllib.request

BLOCK = {"hi": (0x900, 0x97F), "mr": (0x900, 0x97F), "ne": (0x900, 0x97F), "bn": (0x980, 0x9FF), "as": (0x980, 0x9FF),
         "pa": (0xA00, 0xA7F), "gu": (0xA80, 0xAFF), "or": (0xB00, 0xB7F), "ta": (0xB80, 0xBFF), "te": (0xC00, 0xC7F),
         "kn": (0xC80, 0xCFF), "ml": (0xD00, 0xD7F), "si": (0xD80, 0xDFF)}
P = "।॥,.:;?!'\"“”‘’()[]{}<>|=*–—-/\\#&%$@^~`+_0123456789"


def clean(t):
    t = re.sub(r"<ref[^>]*?/>|<ref.*?</ref>", " ", t, flags=re.S)
    t = re.sub(r"\{\{[^{}]*\}\}", " ", t)
    t = re.sub(r"\{\{[^{}]*\}\}", " ", t)
    t = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"<[^>]+>|&[a-z]+;|==+", " ", t)
    return t.replace("'" * 3, " ").replace("'" * 2, " ")


def main():
    lang, out = sys.argv[1], sys.argv[2]
    lo, hi = BLOCK[lang]
    url = f"https://dumps.wikimedia.org/{lang}wiki/latest/{lang}wiki-latest-pages-articles.xml.bz2"
    tmp = out + ".xml.bz2"
    req = urllib.request.Request(url, headers={"User-Agent": "unshape (github.com/novain-shibu/unshape)"})
    with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    cnt = collections.Counter(); buf = None
    with bz2.open(tmp, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if buf is None:
                i = line.find("<text")
                if i < 0:
                    continue
                buf = [line[line.find(">", i) + 1:]]
                if "</text>" not in line:
                    continue
            else:
                buf.append(line)
                if "</text>" not in line:
                    continue
            for w in clean("".join(buf).split("</text>")[0]).split():
                w = w.strip(P)
                if w and all(lo <= ord(c) <= hi or c in "‌‍" for c in w):
                    cnt[w] += 1
            buf = None
    os.remove(tmp)
    with open(out, "w", encoding="utf-8") as o:
        for w, n in cnt.most_common():
            if n >= 3:
                o.write(f"{w} {n}\n")
    print(out, len(cnt), "word types")


if __name__ == "__main__":
    main()
