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
    "EB Garamond": ["EB Garamond", "EB Garamond 12", "Noto Serif", "DejaVu Serif"],
    "Noto Serif": ["Noto Serif", "DejaVu Serif"],
    "Noto Sans": ["Noto Sans", "DejaVu Sans"],
    "Noto Sans UI": ["Noto Sans UI", "Noto Sans", "DejaVu Sans"],
    "Noto Mono": ["Noto Mono", "Noto Sans Mono", "DejaVu Sans Mono"],
}
LINE_HEIGHT = {100: 1.2, 150: 1.5, 200: 2.0}

# Bump when the conversion changes in a way that could fix books that failed before: failed
# renditions are then retried right away instead of waiting out their backoff.
RENDERER_VERSION = 3

# A pandoc filter that turns the EPUB's document tree into something Typst can always compile,
# working on structure rather than patching pandoc's Typst text afterwards:
# - EPUB anchors and ids aren't valid Typst labels, and we don't keep cross-references, so ids
#   go, internal links keep only their text, and empty anchor spans disappear;
# - each EPUB file (chapter, title page...) starts on a new page, as it does in a reader, and so
#   does each top-level chapter heading (weak breaks, so doubles collapse);
# - wrapper divs are unwrapped: a page break inside a container is an error in Typst;
# - image sizes are kept only as percentages: EPUB pixel sizes come through as bare numbers;
# - "//" in text would start a Typst comment and swallow the rest of the line, and "/ " at a line
#   start would begin a term list; text at a paragraph start that reads as list markup is escaped.
LUA_FILTER = r"""
local function internal(target)
  return not target:match("^%a[%w+.-]*:")
end

local function file_marker(b)
  if (b.t ~= "Para" and b.t ~= "Plain") or #b.content ~= 1 then return false end
  local s = b.content[1]
  return s.t == "Span" and #s.content == 0 and s.identifier:match("%.x?html?$") ~= nil
end

local PAGEBREAK = pandoc.RawBlock("typst", "#pagebreak(weak: true)")

local function clear(el)
  el.identifier = ""
  return el
end

local unwrap = {
  Div = function(el) return el.content end,
}

local tidy = {
  Span = function(el)
    if #el.content == 0 then
      if el.identifier:match("%.x?html?$") then return nil end  -- file markers: handled below
      return {}
    end
    return clear(el)
  end,
  Link = function(el)
    if internal(el.target) then return el.content end
    return clear(el)
  end,
  Image = function(el)
    for _, k in ipairs({"width", "height"}) do
      local v = el.attributes[k]
      if v and not v:match("^%s*[%d.]+%%%s*$") then el.attributes[k] = nil end
    end
    return clear(el)
  end,
  Header = clear, CodeBlock = clear, Code = clear, Table = clear, Figure = clear,
  -- A "/" that follows another "/" would start a Typst comment, and one at the start of a line
  -- ("/ term") a term list. The previous character may sit in another element (publishers split
  -- URLs with <wbr> or spans), so a "/" that begins a piece of text is escaped too.
  Str = function(el)
    if not el.text:find("/", 1, true) then return nil end
    local out, buf, prev, first, changed = {}, "", false, true, false
    for ch in el.text:gmatch(utf8.charpattern) do
      if ch == "/" and (prev or first) then
        if buf ~= "" then out[#out + 1] = pandoc.Str(buf); buf = "" end
        out[#out + 1] = pandoc.RawInline("typst", "\\/")
        changed = true
      else
        buf = buf .. ch
      end
      prev, first = (ch == "/"), false
    end
    if not changed then return nil end
    if buf ~= "" then out[#out + 1] = pandoc.Str(buf) end
    return out
  end,
}

-- Text at the start of a paragraph that Typst would read as list markup ("- ", "+ ", "1. ").
-- pandoc is run with --wrap=none, so a paragraph's start is its only line start.
local function escape_start(b)
  local s = b.content[1]
  if not s or s.t ~= "Str" or b.content[2] == nil or b.content[2].t ~= "Space" then return nil end
  local lead
  if s.text == "-" or s.text == "+" then
    lead = "\\" .. s.text
  else
    local digits = s.text:match("^(%d+)%.$")
    if not digits then return nil end
    lead = digits .. "\\."
  end
  b.content[1] = pandoc.RawInline("typst", lead)
  return b
end

local starts = {Para = escape_start, Plain = escape_start}

local breaks = {
  Pandoc = function(doc)
    local out = {}
    for _, b in ipairs(doc.blocks) do
      if file_marker(b) then
        out[#out + 1] = PAGEBREAK
      else
        if b.t == "Header" and b.level == 1 then out[#out + 1] = PAGEBREAK end
        out[#out + 1] = b
      end
    end
    doc.blocks = out
    return doc
  end,
}

local last = {
  Span = function(el)
    if #el.content == 0 then return {} end
    return clear(el)
  end,
}

return {unwrap, tidy, starts, breaks, last}
"""


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
  show heading.where(level: 1): it => {{ set text(size: {size * 1.6:.2f}pt); v(2em); it }}
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
        (d / "epub.lua").write_text(LUA_FILTER)
        cmd = [pandoc, "book.epub", "-t", "typst", "-s", "-V", "template=conf.typ", "--lua-filter=epub.lua",
               "--extract-media=media", "--wrap=none", "-o", "book.typ"]
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            raise RenditionError(f"pandoc failed: {p.stderr.strip()[:500]}")
        body = (d / "book.typ").read_text()
        body = _clean_typst(body, Path(info.cover[0]).name if info.cover else None)
        (d / "book.typ").write_text(body)
        try:
            return typst.compile(str(d / "book.typ"), root=str(d), font_paths=font_paths or [])
        except Exception as e:  # typst raises its own error type
            raise RenditionError(f"typesetting failed: {_typst_error(e, d / 'book.typ')[:900]}") from e


# Web pages: images are dropped (they'd be fetched from the web mid-typesetting, and on news sites
# they're mostly lazy-loaded placeholders), and so are figures left empty by that.
WEB_LUA_FILTER = r"""
return {
  {Image = function() return {} end},
  {Figure = function(el)
     if #pandoc.utils.stringify(el.content) == 0 then return {} end
   end},
}
"""
MIN_ARTICLE_CHARS = 600


def render_webpage(html: str, url: str, typography: EpubDefaults, *, title: str = "", byline: str = "", site: str = "",
                   date: str = "", pandoc: str = "pandoc", font_paths: list[str] | None = None) -> bytes:
    """An article web page typeset like a tablet edition: its main text (found the way reader views
    find it), under a heading with the title, byline, site, date and address."""
    import html as htmlmod

    import typst
    from readability import Document

    doc = Document(html, url=url)
    article = doc.summary(html_partial=True)
    text = re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", article))).strip()
    if len(text) < MIN_ARTICLE_CHARS:
        raise RenditionError("couldn't find the article's text on that page (it may need JavaScript, a login or a subscription)")
    title = title or doc.short_title() or url
    details = " · ".join(htmlmod.escape(x) for x in (byline, site, date) if x)
    head = (f"<h1>{htmlmod.escape(title)}</h1><p>" + (f"<em>{details}</em><br/>" if details else "")
            + f"{htmlmod.escape(url)}</p><hr/>")
    with tempfile.TemporaryDirectory(prefix="webpage-") as tmp:
        d = Path(tmp)
        (d / "page.html").write_text(f"<html><body>{head}{article}</body></html>")
        (d / "conf.typ").write_text(conf_typst(typography, None))
        (d / "web.lua").write_text(WEB_LUA_FILTER)
        (d / "epub.lua").write_text(LUA_FILTER)
        cmd = [pandoc, "page.html", "-f", "html", "-t", "typst", "-s", "-V", "template=conf.typ", "--lua-filter=web.lua",
               "--lua-filter=epub.lua", "--wrap=none", "-o", "page.typ"]
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            raise RenditionError(f"pandoc failed: {p.stderr.strip()[:500]}")
        try:
            return typst.compile(str(d / "page.typ"), root=str(d), font_paths=font_paths or [])
        except Exception as e:
            raise RenditionError(f"typesetting failed: {_typst_error(e, d / 'page.typ')[:900]}") from e


def _clean_typst(body: str, cover_name: str | None = None) -> str:
    """Remove the cover image pandoc places inline: we give it a page of its own instead."""
    if cover_name:
        body = re.sub(r'(?m)^.*image\("[^"]*' + re.escape(cover_name) + r'"\).*$', "", body, count=1)
    return body


def _typst_error(e: Exception, src: Path) -> str:
    """The error with Typst's own pointer at the offending source line, for the diagnostics."""
    diag = getattr(e, "diagnostic", None) or str(e)
    diag = re.sub(r"\S*" + re.escape(src.name), src.name, diag)
    return diag.strip()


def params_hash(t: EpubDefaults) -> str:
    return hashlib.sha256(json.dumps(t.as_dict(), sort_keys=True).encode()).hexdigest()[:12]


def pandoc_available(pandoc: str = "pandoc") -> bool:
    return shutil.which(pandoc) is not None
