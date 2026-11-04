"""Searchable copies of scanned PDFs (ADR 0014).

OCR runs only on pages without text (ocrmypdf ``--skip-text``), so existing
text is never replaced, and the result is stored as a new attachment.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ..pdf.text import TextLayer


class OcrError(Exception):
    pass


SCANNED_SHARE = 0.25  # at least this share of pages must look scanned
IMAGE_COVERAGE = 0.5  # a "scanned" page is mostly one picture


def scanned_pages(pdf: bytes) -> tuple[int, list[int]]:
    """(page count, pages that look scanned): no text at all, and mostly covered by a picture.

    Blank pages and pages with text don't count, so a typeset book with a cover, blank versos
    or a few photo pages isn't mistaken for a scan.
    """
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    tl = TextLayer(pdf)
    doc = pdfium.PdfDocument(pdf)
    out = []
    for i in tl.pages_without_text():
        page = doc[i]
        w, h = page.get_size()
        covered = 0.0
        for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=3):
            left, bottom, right, top = (obj.get_bounds if hasattr(obj, "get_bounds") else obj.get_pos)()
            covered = max(covered, max(0.0, min(right, w) - max(left, 0.0)) * max(0.0, min(top, h) - max(bottom, 0.0)))
        if w * h and covered / (w * h) >= IMAGE_COVERAGE:
            out.append(i)
    return len(tl), out


def needs_ocr(pdf: bytes, min_share: float = SCANNED_SHARE) -> bool:
    """True if the PDF is (largely) a scan: enough of its pages are pictures of text with no text layer."""
    n, scanned = scanned_pages(pdf)
    return n > 0 and len(scanned) / n >= min_share


def ocr_available(binary: str = "ocrmypdf") -> bool:
    return shutil.which(binary) is not None


def make_searchable(pdf: bytes, *, binary: str = "ocrmypdf", language: str = "eng", timeout: int = 1800) -> bytes:
    if not ocr_available(binary):
        raise OcrError("ocrmypdf is not installed")
    with tempfile.TemporaryDirectory(prefix="ocr-") as tmp:
        src, dst = Path(tmp) / "in.pdf", Path(tmp) / "out.pdf"
        src.write_bytes(pdf)
        cmd = [binary, "--skip-text", "--output-type", "pdf", "--optimize", "0", "-l", language, "--quiet", str(src), str(dst)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        # exit code 6 = "already has text" on some versions with --skip-text: nothing to do
        if p.returncode == 6:
            return pdf
        if p.returncode != 0 or not dst.exists():
            raise OcrError(f"ocrmypdf failed ({p.returncode}): {p.stderr.strip()[:500]}")
        return dst.read_bytes()
