"""Extract only .ttf files from Debian .deb archives (ar + data.tar.*). No package scripts are run.
Usage: python -I extract_deb_fonts.py <deb> <outdir> <prefix>"""
import io, sys, tarfile
from pathlib import Path

deb, out, prefix = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
data = deb.read_bytes()
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
                    dst = out / f"{prefix}_{Path(m.name).name}"
                    dst.write_bytes(tf.extractfile(m).read())
                    print(dst.name, m.size)
