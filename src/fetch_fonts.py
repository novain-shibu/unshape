"""Download the frozen font catalogue from official sources and check each file against its recorded hash.

No font file is redistributed with this repository. Sources: Google's Noto repositories on GitHub, Swathanthra Malayalam
Computing (smc.org.in), and the official Debian archive (snapshot.debian.org; only the .ttf files are extracted from the
packages, nothing is executed). Nirmala UI is a Microsoft font: it is used from the local Windows installation only.
Run from src/:  python fetch_fonts.py
"""
import hashlib, io, json, tarfile, time, urllib.request
from pathlib import Path

ROOT = Path("fonts")
UA = {"User-Agent": "unshape-fetch-fonts/0.1"}
NOTO_V101 = "https://raw.githubusercontent.com/googlefonts/noto-fonts/beb8b6f50f02243f14759c0b0690e7d323d64412/hinted"
NOTO_2102 = "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSerifMalayalam"
NOTO_NOW = "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts/NotoSerifMalayalam/hinted/ttf"
DIRECT = {
    "v101/NotoSerifMalayalam-Regular.ttf": f"{NOTO_V101}/NotoSerifMalayalam-Regular.ttf",
    "v101/NotoSerifMalayalam-Bold.ttf": f"{NOTO_V101}/NotoSerifMalayalam-Bold.ttf",
    "old/NotoSerifMalayalam-Regular.ttf": f"{NOTO_2102}/NotoSerifMalayalam-Regular.ttf",
    "old/NotoSerifMalayalam-Bold.ttf": f"{NOTO_2102}/NotoSerifMalayalam-Bold.ttf",
    "NotoSerifMalayalam-Regular.ttf": f"{NOTO_NOW}/NotoSerifMalayalam-Regular.ttf",
    "NotoSerifMalayalam-Bold.ttf": f"{NOTO_NOW}/NotoSerifMalayalam-Bold.ttf",
    "Rachana-Regular.ttf": "https://smc.org.in/downloads/fonts/rachana/Rachana-Regular.ttf",
}
# Debian fonts-smc packages (snapshot.debian.org file hashes) -> prefix used for the extracted .ttf files
DEBS = {
    "6.0": "480df338b25b3ac7404d0b0651aff615a155e25b",
    "5.0.1": "9746bf879af9ec60d5f72b41f5609382931d41f0",
    "5.0": "eeb0669e214129f544e99f96f4741f520daeb744",
}


def get(url):
    time.sleep(2)  # be polite to the servers
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()


def deb_ttfs(data):
    assert data[:8] == b"!<arch>\n"
    pos = 8
    while pos < len(data):
        name = data[pos:pos + 16].decode().strip().rstrip("/")
        size = int(data[pos + 48:pos + 58].decode().strip())
        body = data[pos + 60:pos + 60 + size]
        pos += 60 + size + (size & 1)
        if name.startswith("data.tar"):
            with tarfile.open(fileobj=io.BytesIO(body)) as tf:
                for m in tf.getmembers():
                    if m.isfile() and m.name.lower().endswith(".ttf"):
                        yield Path(m.name).name, tf.extractfile(m).read()


def main():
    expected = json.load(open("../results/FROZEN_CATALOGUE.json"))["fonts"]
    expected = {k.replace("\\", "/").split("fonts/", 1)[-1]: v for k, v in expected.items() if "fonts" in k.replace("\\", "/")}
    files = {rel: get(url) for rel, url in DIRECT.items()}
    for ver, sha1 in DEBS.items():
        for name, data in deb_ttfs(get(f"https://snapshot.debian.org/file/{sha1}")):
            files[f"smc_old/{ver}_{name}"] = data
    bad = 0
    for rel, data in files.items():
        if rel not in expected:
            continue
        h = hashlib.sha256(data).hexdigest()[:16]
        ok = h == expected[rel]
        bad += not ok
        (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / rel).write_bytes(data)
        print(f"{'OK ' if ok else 'MISMATCH'} {rel}")
    print("missing:", sorted(set(expected) - set(files)) or "none", "| mismatches:", bad)


if __name__ == "__main__":
    main()
