"""Coordinate transforms between tablet page space and PDF user space.

Tablet (v6) coordinates on a PDF-backed page are device pixels at 226 DPI with
x measured from the horizontal centre of the displayed page and y from its top
(as rmapi's exporter treats them). Zotero positions are in PDF user space: the
unrotated page, origin bottom-left, y up, in points.
"""

from __future__ import annotations

from dataclasses import dataclass

RM_DPI = 226.0
S = 72.0 / RM_DPI  # points per tablet pixel
RM_WIDTH_PX = 1404
RM_HEIGHT_PX = 1872
# Page size used for note pages inserted on the tablet (the screen at 226 DPI).
NOTE_PAGE_SIZE = (RM_WIDTH_PX * S, RM_HEIGHT_PX * S)


@dataclass(frozen=True)
class PageBox:
    """The visible box of a PDF page (crop box) and its /Rotate value."""

    x0: float
    y0: float
    width: float  # unrotated width
    height: float  # unrotated height
    rotation: int = 0  # 0, 90, 180, 270

    @property
    def display_size(self) -> tuple[float, float]:
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height

    # display space: points, origin top-left of the page as shown, y down
    def display_to_user(self, dx: float, dy: float) -> tuple[float, float]:
        W, H, r = self.width, self.height, self.rotation
        if r == 90:
            ux, uy = dy, dx
        elif r == 180:
            ux, uy = W - dx, dy
        elif r == 270:
            ux, uy = W - dy, H - dx
        else:
            ux, uy = dx, H - dy
        return self.x0 + ux, self.y0 + uy

    def user_to_display(self, ux: float, uy: float) -> tuple[float, float]:
        W, H, r = self.width, self.height, self.rotation
        ux -= self.x0
        uy -= self.y0
        if r == 90:
            return uy, ux
        if r == 180:
            return W - ux, uy
        if r == 270:
            return H - uy, W - ux
        return ux, H - uy

    def rm_to_user(self, x: float, y: float) -> tuple[float, float]:
        dw, _dh = self.display_size
        return self.display_to_user(dw / 2 + x * S, y * S)

    def user_to_rm(self, ux: float, uy: float) -> tuple[float, float]:
        dw, _dh = self.display_size
        dx, dy = self.user_to_display(ux, uy)
        return (dx - dw / 2) / S, dy / S

    def rm_rect_to_user(self, x: float, y: float, w: float, h: float) -> list[float]:
        xs, ys = zip(self.rm_to_user(x, y), self.rm_to_user(x + w, y + h))
        return [min(xs), min(ys), max(xs), max(ys)]

    def user_rect_to_rm(self, rect: list[float]) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = rect
        xs, ys = zip(self.user_to_rm(x1, y1), self.user_to_rm(x2, y2))
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)

    def top_of(self, uy_values: list[float], ux_values: list[float] | None = None) -> float:
        """Distance in points from the displayed top to the highest point (for Zotero sort indexes)."""
        ux_values = ux_values or [self.x0] * len(uy_values)
        return min(self.user_to_display(x, y)[1] for x, y in zip(ux_values, uy_values))


def rotate_rm_point(x: float, y: float, old: PageBox, new_rotation: int) -> tuple[float, float]:
    """Where a tablet point lands after the page's /Rotate changes, keeping it on the same spot of the content."""
    ux, uy = old.rm_to_user(x, y)
    new = PageBox(old.x0, old.y0, old.width, old.height, new_rotation % 360)
    return new.user_to_rm(ux, uy)
