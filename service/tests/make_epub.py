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


# Constructs from real commercial EPUBs that broke typesetting: chapters wrapped in divs (page
# breaks inside containers), pixel image sizes (bare numbers in Typst), "//" in text (a Typst
# comment that swallowed the closing bracket), and a chapter heading inside a block quote. Also
# a URL the publisher split with <wbr/> (so "//" spans two pieces of text) and a "/" at the start
# of a paragraph (a Typst term list).
TRICKY = """
<div class="chapter"><div class="inner">
<h1 id="ch3">Chapter Three</h1>
<p>Her notes said <em>see either/or // both at https://example.org/a//b for more</em> [sic].</p>
<p><img src="fig.png" width="600" height="400" alt="wide"/></p>
<blockquote><h1>An epigraph heading</h1><p>Quoted words.</p></blockquote>
<p>See <a href="http://schools.example.org//site/Default.aspx?PageID=24788">http://schools.example.org/<wbr/>/site/Default.aspx?PageID=24788</a>; then <span>a/</span>/b.</p>
<p>/ Inner Harbor East.</p>
</div></div>
"""


def make_epub(internal_links: bool = False, tricky: bool = False) -> bytes:
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
{TRICKY if tricky else ""}

[^1]: A footnote that should sit at the bottom of the page.
"""
        (d / "book.md").write_text(md)
        subprocess.run(["pandoc", "book.md", "-o", "book.epub", "--epub-cover-image=cover.png"], cwd=d, check=True)
        return (d / "book.epub").read_bytes()
