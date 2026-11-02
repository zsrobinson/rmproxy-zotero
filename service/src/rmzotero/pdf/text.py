"""Text geometry with pdfium: which pages have text, and which characters lie under a mark."""

from __future__ import annotations

from dataclasses import dataclass

import pypdfium2 as pdfium


@dataclass(frozen=True)
class Char:
    text: str
    left: float
    bottom: float
    right: float
    top: float
    index: int

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2

    @property
    def cy(self) -> float:
        return (self.bottom + self.top) / 2


class TextLayer:
    """Lazily extracted characters per page, in PDF user space."""

    def __init__(self, data: bytes):
        self.doc = pdfium.PdfDocument(data)
        self._cache: dict[int, list[Char]] = {}

    def __len__(self) -> int:
        return len(self.doc)

    def chars(self, page_index: int) -> list[Char]:
        if page_index in self._cache:
            return self._cache[page_index]
        page = self.doc[page_index]
        tp = page.get_textpage()
        out = []
        n = tp.count_chars()
        if n > 0:
            text = tp.get_text_range(0, n)
            for i in range(min(n, len(text))):
                ch = text[i]
                if ch in "\r\n":
                    continue
                left, bottom, right, top = tp.get_charbox(i)
                out.append(Char(ch, left, bottom, right, top, i))
        self._cache[page_index] = out
        return out

    def page_text(self, page_index: int) -> str:
        """The page's text in reading order, with line breaks kept (unlike ``chars``)."""
        tp = self.doc[page_index].get_textpage()
        n = tp.count_chars()
        text = tp.get_text_range(0, n) if n > 0 else ""
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def has_text(self, page_index: int) -> bool:
        return any(not c.text.isspace() for c in self.chars(page_index))

    def pages_without_text(self) -> list[int]:
        return [i for i in range(len(self)) if not self.has_text(i)]

    def chars_in_rects(self, page_index: int, rects: list[list[float]], min_overlap: float = 0.5) -> list[Char]:
        """Characters whose box is mostly inside any of ``rects`` ([x1, y1, x2, y2])."""
        out = []
        for c in self.chars(page_index):
            area = max(1e-6, (c.right - c.left) * (c.top - c.bottom))
            for x1, y1, x2, y2 in rects:
                ix = max(0.0, min(x2, c.right) - max(x1, c.left))
                iy = max(0.0, min(y2, c.top) - max(y1, c.bottom))
                if ix * iy / area >= min_overlap:
                    out.append(c)
                    break
        return out

    def chars_near_polyline(self, page_index: int, points: list[tuple[float, float]], half_width: float) -> list[Char]:
        """Characters whose centre lies within ``half_width`` of a polyline (for freehand highlighter strokes)."""
        out = []
        for c in self.chars(page_index):
            if c.text.isspace():
                continue
            if _dist_to_polyline(c.cx, c.cy, points) <= max(half_width, (c.top - c.bottom) * 0.5):
                out.append(c)
        return out


def _dist_to_polyline(px: float, py: float, pts: list[tuple[float, float]]) -> float:
    if len(pts) == 1:
        return ((px - pts[0][0]) ** 2 + (py - pts[0][1]) ** 2) ** 0.5
    best = float("inf")
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L))
        qx, qy = x1 + t * dx, y1 + t * dy
        best = min(best, ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5)
    return best


def line_rects(chars: list[Char]) -> list[list[float]]:
    """Merge characters into one rectangle per text line, in reading order."""
    lines: list[list[Char]] = []
    # whitespace often has a degenerate box; it carries no geometry worth keeping
    for c in sorted((c for c in chars if not c.text.isspace() and c.top - c.bottom > 0.5), key=lambda c: c.index):
        if lines:
            last = lines[-1][-1]
            same_line = abs(c.cy - last.cy) < max(2.0, (last.top - last.bottom) * 0.6)
            if same_line:
                lines[-1].append(c)
                continue
        lines.append([c])
    return [[min(c.left for c in ln), min(c.bottom for c in ln), max(c.right for c in ln), max(c.top for c in ln)] for ln in lines]


def text_of(chars: list[Char]) -> str:
    out = []
    prev = None
    for c in sorted(chars, key=lambda c: c.index):
        if prev is not None and c.index != prev.index + 1 and not out[-1].endswith(" "):
            out.append(" ")
        out.append(c.text)
        prev = c
    return "".join(out).strip()
