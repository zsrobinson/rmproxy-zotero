"""Structural PDF edits with pikepdf: boxes, page insertion/deletion, rotation, fingerprints."""

from __future__ import annotations

import hashlib
import io

import pikepdf

from .geometry import PageBox


def _open(data: bytes) -> pikepdf.Pdf:
    return pikepdf.open(io.BytesIO(data))


def _save(pdf: pikepdf.Pdf) -> bytes:
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def _box(page: pikepdf.Page) -> PageBox:
    box = [float(v) for v in (page.obj.get("/CropBox") or page.mediabox)]
    x0, y0, x1, y1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
    rot = int(page.obj.get("/Rotate", 0)) % 360
    return PageBox(x0, y0, x1 - x0, y1 - y0, rot)


def page_boxes(data: bytes) -> list[PageBox]:
    with _open(data) as pdf:
        return [_box(p) for p in pdf.pages]


def page_count(data: bytes) -> int:
    with _open(data) as pdf:
        return len(pdf.pages)


def page_fingerprints(data: bytes) -> list[str]:
    """A per-page hash of content and size that ignores /Rotate, for matching pages across file versions."""
    out = []
    with _open(data) as pdf:
        for p in pdf.pages:
            h = hashlib.sha256()
            try:
                for stream in p.obj.get("/Contents") if isinstance(p.obj.get("/Contents"), pikepdf.Array) else [p.obj.get("/Contents")]:
                    if stream is not None:
                        h.update(stream.read_bytes())
            except Exception:
                h.update(repr(p.obj.get("/Contents")).encode())
            b = _box(p)
            h.update(f"{b.x0:.2f},{b.y0:.2f},{b.width:.2f},{b.height:.2f}".encode())
            out.append(h.hexdigest())
    return out


def rotations(data: bytes) -> list[int]:
    return [b.rotation for b in page_boxes(data)]


def insert_blank_pages(data: bytes, inserts: list[tuple[int, tuple[float, float]]]) -> bytes:
    """Insert blank pages. ``inserts`` are (final index, (width, height)), applied in ascending order."""
    with _open(data) as pdf:
        for index, (w, h) in sorted(inserts):
            blank = pikepdf.Dictionary(
                Type=pikepdf.Name.Page,
                MediaBox=[0, 0, w, h],
                Resources=pikepdf.Dictionary(),
                Contents=pdf.make_stream(b""),
            )
            page = pikepdf.Page(pdf.make_indirect(blank))
            if index >= len(pdf.pages):
                pdf.pages.append(page)
            else:
                pdf.pages.insert(index, page)
        return _save(pdf)


def delete_pages(data: bytes, indices: set[int]) -> bytes:
    with _open(data) as pdf:
        for i in sorted(indices, reverse=True):
            del pdf.pages[i]
        return _save(pdf)


def set_rotation(data: bytes, rotations_by_index: dict[int, int]) -> bytes:
    with _open(data) as pdf:
        for i, rot in rotations_by_index.items():
            pdf.pages[i].obj[pikepdf.Name.Rotate] = int(rot) % 360
        return _save(pdf)


def copy_page_into(src: bytes, src_index: int, dst: bytes, dst_index: int) -> bytes:
    """Insert page ``src_index`` of ``src`` into ``dst`` at ``dst_index``."""
    with _open(src) as s, _open(dst) as d:
        page = s.pages[src_index]
        if dst_index >= len(d.pages):
            d.pages.append(page)
        else:
            d.pages.insert(dst_index, page)
        return _save(d)


def page_labels(data: bytes) -> list[str]:
    """Page labels as shown in viewers, falling back to 1-based numbers."""
    with _open(data) as pdf:
        out = []
        for i, p in enumerate(pdf.pages):
            try:
                label = p.label
            except Exception:
                label = None
            out.append(str(label) if label else str(i + 1))
        return out
