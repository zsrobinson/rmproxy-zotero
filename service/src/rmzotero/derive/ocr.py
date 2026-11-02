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


def needs_ocr(pdf: bytes, min_fraction: float = 0.0) -> bool:
    """True if some page has no text (a scan). ``min_fraction`` ignores documents with only a few such pages."""
    tl = TextLayer(pdf)
    n = len(tl)
    if n == 0:
        return False
    missing = len(tl.pages_without_text())
    return missing > 0 and missing / n > min_fraction


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
