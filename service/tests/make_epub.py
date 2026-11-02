"""Build a small EPUB with a cover, footnote, list and image for rendition tests."""
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path


def png(w=60, h=80, rgb=(200, 80, 40)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def make_epub(internal_links: bool = False) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "cover.png").write_bytes(png())
        (d / "fig.png").write_bytes(png(30, 20, (20, 120, 200)))
        extra = "\n\nSee [the first chapter](#chapter-one) and [this one](#chapter-two)." if internal_links else ""
        para = " ".join(["The paragraph composer weighs every possible break across the whole paragraph."] * 6)
        md = f"""---
title: A Test Novel
author: Ada Lovelace
lang: en
---

# Chapter One {{#chapter-one}}

{para}[^1]

- first point
- second point

![A figure](fig.png)

# Chapter Two {{#chapter-two}}

{para}{extra}

[^1]: A footnote that should sit at the bottom of the page.
"""
        (d / "book.md").write_text(md)
        subprocess.run(["pandoc", "book.md", "-o", "book.epub", "--epub-cover-image=cover.png"], cwd=d, check=True)
        return (d / "book.epub").read_bytes()
