"""Page images for Claude: the PDF page with the tablet's own strokes and highlights drawn on top.

The strokes come from the exact tablet pages the service archives (ADR 0013),
so Claude sees your handwriting as written. Pages that were never written on
at the tablet show the Zotero annotations instead.
"""

from __future__ import annotations

import io

import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from rmscene import scene_items as si

from ..annotations import convert as cv
from ..pdf.geometry import PageBox
from ..tablet.scene import Page

TARGET_WIDTH_PX = 1100


def _scale_for(box: PageBox) -> float:
    w, _h = box.display_size
    return TARGET_WIDTH_PX / max(1.0, w)


def render_page(pdf: bytes, page_index: int, box: PageBox, tablet_page: Page | None, zotero_annotations: list[dict]) -> bytes:
    doc = pdfium.PdfDocument(pdf)
    scale = _scale_for(box)
    base = doc[page_index].render(scale=scale).to_pil().convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def px(ux: float, uy: float) -> tuple[float, float]:
        dx, dy = box.user_to_display(ux, uy)
        return dx * scale, dy * scale

    if tablet_page is not None:
        for m in tablet_page.marks():
            rgb = cv.mark_rgb(m.value)
            if m.is_highlight:
                for r in m.value.rectangles:
                    x1, y1, x2, y2 = box.rm_rect_to_user(r.x, r.y, r.w, r.h)
                    (ax, ay), (bx, by) = px(x1, y1), px(x2, y2)
                    draw.rectangle([min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)], fill=(*rgb, 110))
                continue
            line: si.Line = m.value
            if line.tool in cv.PEN_TOOLS or not line.points:
                continue
            alpha = 110 if si.Pen.is_highlighter(line.tool) else 255
            width = max(1, int(cv.stroke_width_pt(line) * scale))
            pts = [px(*box.rm_to_user(p.x, p.y)) for p in line.points]
            if len(pts) == 1:
                x, y = pts[0]
                draw.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=(*rgb, alpha))
            else:
                draw.line(pts, fill=(*rgb, alpha), width=width, joint="curve")
    else:
        for a in zotero_annotations:
            pos = cv.position_of(a)
            rgb = cv.rgb_of_hex(a.get("annotationColor") or "#ffd400")
            kind = a.get("annotationType")
            if kind in ("highlight", "underline", "image", "note", "text"):
                for x1, y1, x2, y2 in pos.get("rects") or []:
                    (ax, ay), (bx, by) = px(x1, y1), px(x2, y2)
                    rect = [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]
                    if kind == "highlight":
                        draw.rectangle(rect, fill=(*rgb, 110))
                    elif kind == "underline":
                        draw.line([rect[0], rect[3], rect[2], rect[3]], fill=(*rgb, 255), width=2)
                    else:
                        draw.rectangle(rect, outline=(*rgb, 255), width=2)
            elif kind == "ink":
                width = max(1, int(float(pos.get("width") or 1) * scale))
                for path in pos.get("paths") or []:
                    pts = [px(path[i], path[i + 1]) for i in range(0, len(path) - 1, 2)]
                    if len(pts) > 1:
                        draw.line(pts, fill=(*rgb, 255), width=width, joint="curve")
    out = Image.alpha_composite(base, overlay).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def rm_pixel(box: PageBox, x: float, y: float) -> tuple[int, int]:
    """Where a tablet point lands in a rendered image (for tests)."""
    scale = _scale_for(box)
    dx, dy = box.user_to_display(*box.rm_to_user(x, y))
    return int(dx * scale), int(dy * scale)

