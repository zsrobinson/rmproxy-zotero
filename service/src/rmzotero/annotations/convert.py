"""Converting between tablet marks and Zotero annotation data.

Tablet -> Zotero:
- a text highlight (GlyphRange) becomes a Zotero ``highlight`` carrying its text;
- a freehand highlighter stroke over text is snapped to the characters under
  it and becomes a ``highlight``; over no text it becomes ``ink``;
- a group of pen strokes becomes one ``ink`` annotation.

Zotero -> tablet (written on the Zotero layer):
- ``highlight`` -> text highlight; ``underline`` -> a thin stroke under each line;
- ``ink`` -> strokes; ``image`` (area) -> a rectangle outline;
- ``note`` and ``text`` have no tablet form (ADR 0008) and are not drawn.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import replace

from rmscene import scene_items as si

from ..pdf.geometry import S, PageBox
from ..pdf.text import TextLayer, line_rects, text_of
from ..tablet.scene import Mark

# Zotero's standard annotation colours
ZOTERO_COLORS = {
    "#ffd400": (255, 212, 0),  # yellow
    "#ff6666": (255, 102, 102),  # red
    "#5fb236": (95, 178, 54),  # green
    "#2ea8e5": (46, 168, 229),  # blue
    "#a28ae5": (162, 138, 229),  # purple
    "#e56eee": (229, 110, 238),  # magenta
    "#f19837": (241, 152, 55),  # orange
    "#aaaaaa": (170, 170, 170),  # gray
}

PEN_RGB = {
    si.PenColor.BLACK: (0, 0, 0),
    si.PenColor.GRAY: (125, 125, 125),
    si.PenColor.WHITE: (255, 255, 255),
    si.PenColor.YELLOW: (255, 237, 117),
    si.PenColor.GREEN: (166, 230, 120),
    si.PenColor.PINK: (247, 160, 207),
    si.PenColor.BLUE: (78, 105, 201),
    si.PenColor.RED: (179, 62, 57),
    si.PenColor.GRAY_OVERLAP: (125, 125, 125),
    si.PenColor.HIGHLIGHT: (255, 237, 117),
    si.PenColor.GREEN_2: (161, 216, 125),
    si.PenColor.CYAN: (139, 208, 229),
    si.PenColor.MAGENTA: (183, 130, 205),
    si.PenColor.YELLOW_2: (247, 232, 81),
}

# tablet highlighter colours and the Zotero colour each one is shown as
HIGHLIGHT_PALETTE = {
    "#ffd400": (si.PenColor.YELLOW, (255, 237, 117)),
    "#5fb236": (si.PenColor.GREEN, (166, 230, 120)),
    "#ff6666": (si.PenColor.PINK, (247, 160, 207)),
    "#e56eee": (si.PenColor.PINK, (247, 160, 207)),
    "#2ea8e5": (si.PenColor.CYAN, (139, 208, 229)),
    "#a28ae5": (si.PenColor.MAGENTA, (183, 130, 205)),
    "#f19837": (si.PenColor.YELLOW, (255, 237, 117)),
    "#aaaaaa": (si.PenColor.GRAY, (125, 125, 125)),
}

PEN_TOOLS = {si.Pen.ERASER, si.Pen.ERASER_AREA}
STROKE_GROUP_GAP_PX = 100.0  # ~11 mm


def hex_of(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb[:3])


def rgb_of_hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def nearest(rgb: tuple[int, int, int], palette: dict[str, tuple[int, int, int]]) -> str:
    return min(palette, key=lambda k: sum((a - b) ** 2 for a, b in zip(rgb, palette[k])))


def mark_rgb(value: si.Line | si.GlyphRange) -> tuple[int, int, int]:
    rgba = getattr(value, "color_rgba", None)
    if rgba:
        return tuple(rgba[:3])  # type: ignore[return-value]
    return PEN_RGB.get(value.color, (0, 0, 0))


def is_highlighter(m: Mark) -> bool:
    return m.is_stroke and si.Pen.is_highlighter(m.value.tool)


def is_drawable(m: Mark) -> bool:
    return m.is_highlight or (m.is_stroke and m.value.tool not in PEN_TOOLS and len(m.value.points) > 0)


def bbox(m: Mark) -> tuple[float, float, float, float]:
    if m.is_highlight:
        rs = m.value.rectangles
        return min(r.x for r in rs), min(r.y for r in rs), max(r.x + r.w for r in rs), max(r.y + r.h for r in rs)
    xs = [p.x for p in m.value.points]
    ys = [p.y for p in m.value.points]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_gap(a, b) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return (dx * dx + dy * dy) ** 0.5


def union(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def stroke_width_pt(line: si.Line) -> float:
    widths = [p.width for p in line.points] or [8]
    return max(0.3, statistics.median(widths) / 4.0 * S)


# -- sort index / position helpers ---------------------------------------------------


def sort_index(page_index: int, offset: int, top: float) -> str:
    return f"{page_index:05d}|{max(0, offset):06d}|{max(0, int(top)):05d}"


def _r(v: float) -> float:
    return round(float(v), 3)


# -- tablet -> Zotero --------------------------------------------------------------------


def marks_to_zotero(
    marks: list[Mark], box: PageBox, page_index: int, page_label: str, text: TextLayer | None
) -> dict:
    """Zotero annotation data (without key/parent/version) for a group of marks."""
    first = marks[0]
    color_hex = None
    if len(marks) == 1 and first.is_highlight:
        g: si.GlyphRange = first.value
        rects = [[_r(v) for v in box.rm_rect_to_user(r.x, r.y, r.w, r.h)] for r in g.rectangles]
        txt = g.text
        offset = 0
        if text is not None and page_index < len(text):
            chars = text.chars_in_rects(page_index, rects)
            if chars:
                offset = min(c.index for c in chars)
                txt = txt or text_of(chars)
        color_hex = nearest(mark_rgb(g), {k: v[1] for k, v in HIGHLIGHT_PALETTE.items()})
        return _highlight(page_index, page_label, rects, txt, color_hex, offset, box)
    if len(marks) == 1 and is_highlighter(first) and text is not None and page_index < len(text):
        line: si.Line = first.value
        pts = [box.rm_to_user(p.x, p.y) for p in line.points]
        half = stroke_width_pt(line) / 2
        chars = text.chars_near_polyline(page_index, pts, half)
        if chars:
            rects = [[_r(v) for v in r] for r in line_rects(chars)]
            color_hex = nearest(mark_rgb(line), {k: v[1] for k, v in HIGHLIGHT_PALETTE.items()})
            return _highlight(page_index, page_label, rects, text_of(chars), color_hex, min(c.index for c in chars), box)
    # ink
    paths = []
    widths = []
    for m in marks:
        if m.is_highlight:
            for r in m.value.rectangles:
                x1, y1, x2, y2 = box.rm_rect_to_user(r.x, r.y, r.w, r.h)
                paths.append([_r(v) for v in (x1, y1, x2, y1, x2, y2, x1, y2, x1, y1)])
            continue
        pts = []
        for p in m.value.points:
            pts.extend(_r(v) for v in box.rm_to_user(p.x, p.y))
        paths.append(pts)
        widths.append(stroke_width_pt(m.value))
    color_hex = hex_of(mark_rgb(first.value))
    ys = [paths[i][j] for i in range(len(paths)) for j in range(1, len(paths[i]), 2)]
    xs = [paths[i][j] for i in range(len(paths)) for j in range(0, len(paths[i]), 2)]
    top = box.top_of(ys, xs) if ys else 0
    return {
        "itemType": "annotation",
        "annotationType": "ink",
        "annotationComment": "",
        "annotationColor": color_hex,
        "annotationPageLabel": page_label,
        "annotationSortIndex": sort_index(page_index, 0, top),
        "annotationPosition": json.dumps({"pageIndex": page_index, "width": _r(statistics.median(widths) if widths else 1.0), "paths": paths}),
        "tags": [],
    }


def _highlight(page_index, page_label, rects, txt, color_hex, offset, box: PageBox) -> dict:
    top = box.top_of([r[3] for r in rects], [r[0] for r in rects]) if rects else 0
    return {
        "itemType": "annotation",
        "annotationType": "highlight",
        "annotationText": txt,
        "annotationComment": "",
        "annotationColor": color_hex,
        "annotationPageLabel": page_label,
        "annotationSortIndex": sort_index(page_index, offset, top),
        "annotationPosition": json.dumps({"pageIndex": page_index, "rects": rects}),
        "tags": [],
    }


# -- Zotero -> tablet ---------------------------------------------------------------------

DRAWABLE_TYPES = {"highlight", "underline", "ink", "image"}


def position_of(ann: dict) -> dict:
    pos = ann.get("annotationPosition") or "{}"
    return json.loads(pos) if isinstance(pos, str) else dict(pos)


def _point(x: float, y: float, width_px: float) -> si.Point:
    return si.Point(x=float(x), y=float(y), speed=0, direction=0, width=max(1, int(round(width_px * 4))), pressure=100)


def _pen_line(points_rm: list[tuple[float, float]], width_pt: float, rgb: tuple[int, int, int], tool=si.Pen.FINELINER_2) -> si.Line:
    color = nearest(rgb, {c: PEN_RGB[c] for c in (si.PenColor.BLACK, si.PenColor.GRAY, si.PenColor.BLUE, si.PenColor.RED, si.PenColor.GREEN, si.PenColor.YELLOW, si.PenColor.PINK)})
    width_px = width_pt / S
    return si.Line(
        color=color,
        tool=tool,
        points=[_point(x, y, width_px) for x, y in points_rm],
        thickness_scale=1.0,
        starting_length=0.0,
        color_rgba=(rgb[0], rgb[1], rgb[2], 255),
    )


def zotero_to_values(ann: dict, box: PageBox) -> list[si.Line | si.GlyphRange]:
    """Tablet items that draw a Zotero annotation on its page (empty if it has no tablet form)."""
    kind = ann.get("annotationType")
    pos = position_of(ann)
    color = ann.get("annotationColor") or "#ffd400"
    rgb = rgb_of_hex(color)
    if kind == "highlight":
        pen, tablet_rgb = HIGHLIGHT_PALETTE.get(color.lower(), HIGHLIGHT_PALETTE[nearest(rgb, ZOTERO_COLORS)])
        rects = [si.Rectangle(*box.user_rect_to_rm(r)) for r in pos.get("rects") or []]
        if not rects:
            return []
        txt = ann.get("annotationText") or ""
        return [si.GlyphRange(start=None, length=len(txt), text=txt, color=si.PenColor.HIGHLIGHT, rectangles=rects, color_rgba=(*tablet_rgb, 255))]
    if kind == "underline":
        out = []
        for r in pos.get("rects") or []:
            x1, y1, x2, _y2 = r
            a, b = box.user_to_rm(x1, y1), box.user_to_rm(x2, y1)
            out.append(_pen_line([a, b], 1.0, rgb))
        return out
    if kind == "ink":
        width = float(pos.get("width") or 1.0)
        out = []
        for path in pos.get("paths") or []:
            pts = [box.user_to_rm(path[i], path[i + 1]) for i in range(0, len(path) - 1, 2)]
            if pts:
                out.append(_pen_line(pts, width, rgb))
        return out
    if kind == "image":
        out = []
        for x1, y1, x2, y2 in pos.get("rects") or []:
            corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
            out.append(_pen_line([box.user_to_rm(x, y) for x, y in corners], 0.8, rgb))
        return out
    return []


# -- fingerprints ---------------------------------------------------------------------------


def zotero_fingerprint(ann: dict, page_id: str) -> str:
    """What the tablet cares about: type, page, geometry, colour, text. Not comments or tags."""
    pos = position_of(ann)
    pos.pop("pageIndex", None)

    def rnd(v):
        if isinstance(v, list):
            return [rnd(x) for x in v]
        if isinstance(v, float):
            return round(v, 1)
        return v

    key = [ann.get("annotationType"), page_id, rnd(pos), (ann.get("annotationColor") or "").lower(), ann.get("annotationText") or ""]
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def tablet_fingerprint(marks: list[Mark]) -> str:
    key = []
    for m in sorted(marks, key=lambda m: m.id):
        v = m.value
        if m.is_highlight:
            key.append(["h", m.id, [[round(r.x, 1), round(r.y, 1), round(r.w, 1), round(r.h, 1)] for r in v.rectangles], list(v.color_rgba or ()), int(v.color)])
        else:
            key.append(["s", m.id, int(v.tool), int(v.color), list(v.color_rgba or ()), [[round(p.x, 1), round(p.y, 1)] for p in v.points]])
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]


# -- applying Zotero edits to the user's own strokes (ADR 0013) ------------------------------


def recolor(value: si.Line | si.GlyphRange, color_hex: str):
    rgb = rgb_of_hex(color_hex)
    if isinstance(value, si.GlyphRange):
        _pen, tablet_rgb = HIGHLIGHT_PALETTE.get(color_hex.lower(), HIGHLIGHT_PALETTE[nearest(rgb, ZOTERO_COLORS)])
        return replace(value, color=si.PenColor.HIGHLIGHT, color_rgba=(*tablet_rgb, 255))
    return replace(value, color_rgba=(*rgb, 255))


def trim_to_paths(lines: list[tuple[str, si.Line]], paths_rm: list[list[tuple[float, float]]], tol: float = 6.0) -> dict[str, list[list[si.Point]]]:
    """Keep only the original points that survive in Zotero's (possibly erased) paths.

    Returns, for each original stroke id, the runs of original points still
    present (an erase in the middle splits a stroke in two). Pen, pressure and
    width of the surviving points are untouched.
    """
    survivors = [pt for path in paths_rm for pt in path]
    out: dict[str, list[list[si.Point]]] = {}
    for sid, line in lines:
        runs: list[list[si.Point]] = []
        cur: list[si.Point] = []
        for p in line.points:
            keep = any(abs(p.x - x) <= tol and abs(p.y - y) <= tol for x, y in survivors)
            if keep:
                cur.append(p)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        out[sid] = runs
    return out
