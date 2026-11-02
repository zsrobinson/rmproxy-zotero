"""EPUB -> PDF renditions typeset with Typst (ADR 0010).

pandoc turns the EPUB into Typst markup (structure, emphasis, lists, images,
footnotes, tables); our ``conf`` function sets the page to the reMarkable 2
screen and applies the chosen typography with whole-paragraph line breaking
and hyphenation. The cover image, when the EPUB has one, becomes page one so
the tablet uses it as the thumbnail.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from ..config import EpubDefaults

PAGE_W_MM = 1404 / 226 * 25.4  # 157.8 mm
PAGE_H_MM = 1872 / 226 * 25.4  # 210.4 mm
BASE_PT = 10.0  # text size at text_scale 1

FONT_FALLBACKS = {
    "EB Garamond": ["EB Garamond", "Noto Serif", "DejaVu Serif"],
    "Noto Serif": ["Noto Serif", "DejaVu Serif"],
    "Noto Sans": ["Noto Sans", "DejaVu Sans"],
    "Noto Sans UI": ["Noto Sans UI", "Noto Sans", "DejaVu Sans"],
    "Noto Mono": ["Noto Mono", "Noto Sans Mono", "DejaVu Sans Mono"],
}
LINE_HEIGHT = {100: 1.2, 150: 1.5, 200: 2.0}


class RenditionError(Exception):
    pass


@dataclass
class EpubInfo:
    drm: bool
    fixed_layout: bool
    title: str
    cover: tuple[str, bytes] | None


NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/", "c": "urn:oasis:names:tc:opendocument:xmlns:container"}


def inspect_epub(data: bytes) -> EpubInfo:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise RenditionError("not a valid EPUB") from e
    names = set(z.namelist())
    drm = False
    if "META-INF/encryption.xml" in names:
        enc = z.read("META-INF/encryption.xml").decode("utf-8", "replace")
        # font obfuscation (IDPF/Adobe) is not DRM; anything else encrypting content is
        algos = re.findall(r'Algorithm="([^"]+)"', enc)
        drm = any(a not in ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC") and "xmlenc" in a for a in algos)
    if "META-INF/rights.xml" in names:
        drm = True
    opf_path = None
    if "META-INF/container.xml" in names:
        root = ET.fromstring(z.read("META-INF/container.xml"))
        rf = root.find(".//c:rootfile", NS)
        opf_path = rf.get("full-path") if rf is not None else None
    title, fixed, cover = "", False, None
    if opf_path and opf_path in names:
        opf = ET.fromstring(z.read(opf_path))
        t = opf.find(".//dc:title", NS)
        title = (t.text or "").strip() if t is not None else ""
        for meta in opf.iter("{%s}meta" % NS["opf"]):
            if meta.get("property") == "rendition:layout" and (meta.text or "").strip() == "pre-paginated":
                fixed = True
        base = str(Path(opf_path).parent)
        manifest = {i.get("id"): i for i in opf.iter("{%s}item" % NS["opf"])}
        cover_item = next((i for i in manifest.values() if "cover-image" in (i.get("properties") or "")), None)
        if cover_item is None:
            meta = next((m for m in opf.iter("{%s}meta" % NS["opf"]) if m.get("name") == "cover"), None)
            if meta is not None:
                cover_item = manifest.get(meta.get("content"))
        if cover_item is not None and (cover_item.get("media-type") or "").startswith("image/"):
            href = cover_item.get("href")
            path = str(Path(base) / href) if base not in ("", ".") else href
            if path in names:
                cover = (Path(href).name, z.read(path))
    return EpubInfo(drm=drm, fixed_layout=fixed, title=title, cover=cover)


def conf_typst(t: EpubDefaults, cover_file: str | None) -> str:
    fonts = FONT_FALLBACKS.get(t.font, [t.font, "DejaVu Serif"])
    size = BASE_PT * float(t.text_scale)
    mult = LINE_HEIGHT.get(int(t.line_height), 1.5)
    leading = max(0.15, mult - 1.0 + 0.25)  # space between lines, in em
    margin_mm = int(t.margins) / 226 * 25.4
    justify = "true" if t.alignment != "left" else "false"
    cover = (f'page(margin: 0pt, numbering: none, align(center + horizon, image("{cover_file}", width: 100%, height: 100%, fit: "contain")))'
             if cover_file else "")
    return f"""
#let conf(title: none, authors: (), date: none, abstract: none, cols: 1, margin: none, paper: none, lang: "en",
          region: none, font: (), fontsize: none, sectionnumbering: none, doc) = {{
  set page(width: {PAGE_W_MM:.2f}mm, height: {PAGE_H_MM:.2f}mm, margin: (x: {margin_mm:.2f}mm, top: {margin_mm + 4:.2f}mm, bottom: {margin_mm + 6:.2f}mm),
           numbering: "1", number-align: center + bottom)
  set text(font: ({", ".join(json.dumps(f) for f in fonts)},), size: {size:.2f}pt, lang: lang, hyphenate: {justify})
  set par(justify: {justify}, linebreaks: "optimized", leading: {leading:.2f}em, first-line-indent: 1.2em)
  show heading: it => {{ set par(first-line-indent: 0em); v(0.8em); it; v(0.4em) }}
  show heading.where(level: 1): it => {{ pagebreak(weak: true); set text(size: {size * 1.6:.2f}pt); v(2em); it }}
  set image(fit: "contain")
  {cover}
  doc
}}
"""


def render_epub(data: bytes, typography: EpubDefaults, *, pandoc: str = "pandoc", font_paths: list[str] | None = None) -> bytes:
    info = inspect_epub(data)
    if info.drm:
        raise RenditionError("DRM-protected EPUB")
    if info.fixed_layout:
        raise RenditionError("fixed-layout EPUB")
    import typst

    with tempfile.TemporaryDirectory(prefix="rendition-") as tmp:
        d = Path(tmp)
        (d / "book.epub").write_bytes(data)
        cover_name = None
        if info.cover:
            name, img = info.cover
            cover_name = "cover-" + hashlib.sha1(img).hexdigest()[:8] + Path(name).suffix
            (d / cover_name).write_bytes(img)
        (d / "conf.typ").write_text(conf_typst(typography, cover_name))
        cmd = [pandoc, "book.epub", "-t", "typst", "-s", "-V", "template=conf.typ", "--extract-media=media", "-o", "book.typ"]
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            raise RenditionError(f"pandoc failed: {p.stderr.strip()[:500]}")
        body = (d / "book.typ").read_text()
        body = _clean_typst(body, Path(info.cover[0]).name if info.cover else None)
        (d / "book.typ").write_text(body)
        try:
            return typst.compile(str(d / "book.typ"), root=str(d), font_paths=font_paths or [])
        except Exception as e:  # typst raises its own error type
            raise RenditionError(f"typesetting failed: {str(e)[:500]}") from e


def _clean_typst(body: str, cover_name: str | None = None) -> str:
    """Make pandoc's EPUB output compile: EPUB anchors aren't valid Typst labels."""
    # standalone anchor labels like <ch001.xhtml#intro> (we don't keep cross-references)
    body = re.sub(r"(?m)^[ \t]*<[^<>\n]*>[ \t]*$", "", body)
    # inline labels after headings etc.
    body = re.sub(r"<[^<>\s]*[#/.][^<>\s]*>", "", body)
    # internal links to those anchors: keep the link text
    body = re.sub(r"#link\(<[^>]*>\)", "#box", body)
    # EPUB footnotes often start with the back-reference's leftover punctuation
    body = re.sub(r"#footnote\[\.\s*", "#footnote[", body)
    if cover_name:
        body = re.sub(r'(?m)^#image\("[^"]*' + re.escape(cover_name) + r'"\)\s*$', "", body, count=1)
    return body


def params_hash(t: EpubDefaults) -> str:
    return hashlib.sha256(json.dumps(t.as_dict(), sort_keys=True).encode()).hexdigest()[:12]


def pandoc_available(pandoc: str = "pandoc") -> bool:
    return shutil.which(pandoc) is not None
