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
