"""Tiny text-only PDFs (pointers, placeholders, test fixtures) built without a layout engine."""

from __future__ import annotations

import io
import textwrap

import pikepdf

from .geometry import NOTE_PAGE_SIZE


def _escape(s: str) -> str:
    s = s.encode("cp1252", "replace").decode("cp1252")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_pdf(pages: list[list[tuple[str, float]]], size: tuple[float, float] = NOTE_PAGE_SIZE, margin: float = 36.0) -> bytes:
    """Build a PDF; each page is a list of (text, font size) paragraphs, wrapped to the page width."""
    w, h = size
    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica, Encoding=pikepdf.Name.WinAnsiEncoding)
    )
    for paragraphs in pages:
        ops = ["BT"]
        y = h - margin
        for text, fs in paragraphs:
            chars_per_line = max(10, int((w - 2 * margin) / (fs * 0.5)))
            for line in textwrap.wrap(text, chars_per_line) or [""]:
                y -= fs * 1.35
                ops.append(f"/F1 {fs:.1f} Tf 1 0 0 1 {margin:.1f} {y:.1f} Tm ({_escape(line)}) Tj")
            y -= fs * 0.6
        ops.append("ET")
        page = pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, w, h],
            Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
            Contents=pdf.make_stream("\n".join(ops).encode("cp1252", "replace")),
        )
        pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def pointer_pdf(title: str, location: str) -> bytes:
    """The one-page Pointer left in another collection's folder (glossary: Pointer)."""
    return text_pdf(
        [[(title, 20.0), ("This item is stored on your tablet in:", 13.0), (location, 15.0),
          ("Its markup lives there; anything written on this page is not synced.", 10.0)]]
    )


def placeholder_pdf(title: str) -> bytes:
    """Stand-in for an archived document when the tablet's own Archive isn't available."""
    return text_pdf(
        [[(title, 20.0), ("This document is archived.", 14.0),
          ("Open or star this page and it will be downloaded with all of its markup within a few minutes.", 12.0)]]
    )
