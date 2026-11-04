import io
import shutil
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from rmzotero.config import EpubDefaults
from rmzotero.derive.ocr import make_searchable, needs_ocr, ocr_available
from rmzotero.derive.rendition import RenditionError, inspect_epub, render_epub
from rmzotero.pdf import edit
from rmzotero.pdf.simple import text_pdf
from rmzotero.pdf.text import TextLayer

sys.path.insert(0, str(Path(__file__).parent))
from make_epub import make_epub  # noqa: E402

OCRMYPDF = shutil.which("ocrmypdf") or str(Path(sys.executable).parent / "ocrmypdf")


def scanned(pdf: bytes) -> bytes:
    """Rasterise a PDF into an image-only PDF, like a photocopy."""
    doc = pdfium.PdfDocument(pdf)
    images = [doc[i].render(scale=300 / 72).to_pil().convert("L") for i in range(len(doc))]
    buf = io.BytesIO()
    images[0].save(buf, "PDF", resolution=300, save_all=True, append_images=images[1:])
    return buf.getvalue()


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_rendition_matches_tablet_page_and_has_cover_and_text():
    epub = make_epub()
    info = inspect_epub(epub)
    assert info.title == "A Test Novel" and info.cover and not info.drm and not info.fixed_layout
    pdf = render_epub(epub, EpubDefaults(font="Noto Serif", text_scale=1.2, line_height=150, margins=125, alignment="left"))
    box = edit.page_boxes(pdf)[0]
    assert abs(box.width - 447.28) < 0.5 and abs(box.height - 596.38) < 0.5
    tl = TextLayer(pdf)
    assert not tl.has_text(0)  # the cover
    assert "Chapter One" in "".join(c.text for c in tl.chars(1))


def test_drm_epub_refused():
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/rights.xml", "<rights/>")
    assert inspect_epub(buf.getvalue()).drm
    with pytest.raises(RenditionError):
        render_epub(buf.getvalue(), EpubDefaults())


def test_needs_ocr_detection():
    text = text_pdf([[("Already searchable", 14)]], size=(300, 300))
    assert not needs_ocr(text)
    assert needs_ocr(scanned(text))


def _join(*pdfs: bytes) -> bytes:
    import pikepdf

    out = pikepdf.new()
    for p in pdfs:
        out.pages.extend(pikepdf.open(io.BytesIO(p)).pages)
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


def test_a_typeset_book_with_a_cover_and_blank_pages_is_not_a_scan():
    # what a publisher's PDF looks like: a picture cover, a blank page, then text
    cover = scanned(text_pdf([[("COVER ART", 40)]], size=(300, 400)))
    blank = text_pdf([[]], size=(300, 400))
    body = text_pdf([[(f"Chapter text on page {i}", 12)] for i in range(10)], size=(300, 400))
    assert not needs_ocr(_join(cover, blank, body))
    # a class reading that's half photocopied still counts
    copied = scanned(text_pdf([[(f"Photocopied page {i}", 14)] for i in range(4)], size=(300, 400)))
    typed = text_pdf([[("Typed intro", 12)]] * 4, size=(300, 400))
    assert needs_ocr(_join(typed, copied))


@pytest.mark.skipif(not (shutil.which("tesseract") and Path(OCRMYPDF).exists()), reason="ocrmypdf/tesseract not installed")
def test_ocr_adds_text_only_where_missing():
    a = text_pdf([[("Original born-digital text stays exactly as it was", 14)]], size=(400, 300))
    b = scanned(text_pdf([[("Photocopied reading for class", 20)]], size=(400, 300)))
    import pikepdf

    mixed = pikepdf.open(io.BytesIO(a))
    mixed.pages.extend(pikepdf.open(io.BytesIO(b)).pages)
    out = io.BytesIO()
    mixed.save(out)
    src = out.getvalue()
    result = make_searchable(src, binary=OCRMYPDF)
    tl = TextLayer(result)
    assert "".join(c.text for c in tl.chars(0)).startswith("Original born-digital")
    assert "class" in "".join(c.text for c in tl.chars(1)).lower()
    assert edit.page_fingerprints(result)[0] == edit.page_fingerprints(src)[0]  # untouched page


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_rendition_with_internal_links():
    # real EPUBs link to their own anchors (contents pages, back-references); these broke typesetting
    pdf = render_epub(make_epub(internal_links=True), EpubDefaults())
    tl = TextLayer(pdf)
    assert "the first chapter" in "".join(c.text for i in range(len(tl)) for c in tl.chars(i))


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_rendition_survives_constructs_from_real_epubs():
    pdf = render_epub(make_epub(tricky=True), EpubDefaults())
    tl = TextLayer(pdf)
    pages = ["".join(c.text for c in tl.chars(i)) for i in range(len(tl))]
    text = "".join(pages)
    assert "either/or // both" in text and "[sic]" in text and "Quoted words" in text
    # every chapter starts on a page of its own
    for title in ("Chapter One", "Chapter Two", "Chapter Three"):
        page = next(p for p in pages if title in p)
        assert page.lstrip().startswith(title), page[:80]


def test_typst_errors_point_at_the_source():
    from rmzotero.derive.rendition import RenditionError, _typst_error
    import tempfile
    import typst

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "book.typ"
        src.write_text("Hello #emph[a // b]\n")
        with pytest.raises(Exception) as ei:
            typst.compile(str(src))
        msg = _typst_error(ei.value, src)
    assert "unclosed delimiter" in msg and "#emph[a // b]" in msg and tmp not in msg
