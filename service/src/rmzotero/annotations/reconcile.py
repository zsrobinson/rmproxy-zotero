"""Three-way reconciliation of annotations for one document.

Each Zotero annotation is linked to the tablet items that represent it. For
every link we compare both sides with the fingerprints recorded at the last
sync; a side whose fingerprint changed was edited. One-sided edits are copied
across; two-sided edits go to the most recent change; deletions propagate
(ADR 0007). Unlinked marks on the tablet become new Zotero annotations (grouped
by drawing order and proximity); unlinked Zotero annotations are drawn on the
page's Zotero layer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable

from rmscene import scene_items as si

from ..pdf.geometry import PageBox
from ..pdf.text import TextLayer
from ..tablet.scene import Mark, Page
from . import convert as cv

log = logging.getLogger(__name__)


@dataclass
class PageCtx:
    page_id: str
    zidx: int  # index of this page in the Zotero PDF
    box: PageBox
    label: str
    page: Page
    modified_ms: int = 0
    dirty: bool = False
    _marks: dict[str, Mark] | None = None

    def marks(self) -> dict[str, Mark]:
        if self._marks is None:
            self._marks = {m.id: m for m in self.page.marks()}
        return self._marks

    def invalidate(self) -> None:
        self._marks = None
        self.dirty = True


@dataclass
class Link:
    zkey: str
    origin: str  # "tablet" | "zotero"
    page_id: str
    tablet_ids: list[str] = field(default_factory=list)
    zversion: int = 0
    zfp: str = ""
    tfp: str = ""
    unsynced: bool = False  # Zotero annotation with no tablet form (note/text)

    def to_row(self) -> dict:
        return {
            "zkey": self.zkey, "origin": self.origin, "page_id": self.page_id, "tablet_ids": json.dumps(self.tablet_ids),
            "zversion": self.zversion, "zfp": self.zfp, "tfp": self.tfp, "unsynced": int(self.unsynced),
        }

    @classmethod
    def from_row(cls, row) -> "Link":
        return cls(row["zkey"], row["origin"], row["page_id"], json.loads(row["tablet_ids"] or "[]"), row["zversion"], row["zfp"], row["tfp"], bool(row["unsynced"]))


@dataclass
class Result:
    creates: list[dict] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)
    deletes: list[tuple[str, int]] = field(default_factory=list)
    links: dict[str, Link] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _ms(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def _drawable_user_marks(ctx: PageCtx) -> list[Mark]:
    return sorted((m for m in ctx.marks().values() if cv.is_drawable(m)), key=lambda m: m.order)


def _group_new(ctx: PageCtx, links_on_page: list[Link], claimed: set[str]) -> tuple[dict[str, list[str]], list[list[Mark]]]:
    """Assign unclaimed marks: some join an existing tablet-origin group, the rest form new groups.

    A pen stroke continues a group when it was drawn right after the group's
    last stroke (in the same layer) and lies within ~11 mm of it. Text
    highlights and highlighter strokes always stand alone.
    """
    ordered = _drawable_user_marks(ctx)
    pos = {m.id: i for i, m in enumerate(ordered)}
    joins: dict[str, list[str]] = {}
    groups: list[list[Mark]] = []
    owner: dict[str, Link] = {}
    for link in links_on_page:
        if link.origin == "tablet":
            for tid in link.tablet_ids:
                owner[tid] = link
    marks_by_id = ctx.marks()
    current: list[Mark] | None = None
    current_link: Link | None = None
    current_box = None
    for m in ordered:
        if m.id in claimed:
            # a claimed stroke can be the anchor a following new stroke attaches to
            link = owner.get(m.id)
            if link and m.is_stroke and not cv.is_highlighter(m):
                current, current_link = None, link
                members = [marks_by_id[t] for t in link.tablet_ids if t in marks_by_id]
                current_box = None
                for mm in members:
                    current_box = cv.bbox(mm) if current_box is None else cv.union(current_box, cv.bbox(mm))
            else:
                current, current_link, current_box = None, None, None
            continue
        standalone = m.is_highlight or cv.is_highlighter(m)
        if standalone:
            groups.append([m])
            current, current_link, current_box = None, None, None
            continue
        b = cv.bbox(m)
        prev = ordered[pos[m.id] - 1] if pos[m.id] > 0 else None
        same_layer = prev is not None and prev.layer_id == m.layer_id
        if current_box is not None and same_layer and cv.bbox_gap(current_box, b) <= cv.STROKE_GROUP_GAP_PX:
            if current_link is not None:
                joins.setdefault(current_link.zkey, []).append(m.id)
            else:
                assert current is not None
                current.append(m)
            current_box = cv.union(current_box, b)
            continue
        current = [m]
        current_link = None
        current_box = b
        groups.append(current)
    return joins, groups


def reconcile(
    pages: dict[str, PageCtx],
    zanns: dict[str, dict],
    links: dict[str, Link],
    text: TextLayer | None,
    new_key: Callable[[], str],
) -> Result:
    res = Result()
    by_zidx = {ctx.zidx: ctx for ctx in pages.values()}

    # 0. claim marks already linked; group and assign the new ones
    claimed: set[str] = set()
    for link in links.values():
        claimed.update(link.tablet_ids)
    new_groups: list[tuple[PageCtx, list[Mark]]] = []
    for ctx in pages.values():
        on_page = [l for l in links.values() if l.page_id == ctx.page_id]
        joins, groups = _group_new(ctx, on_page, claimed)
        for zkey, ids in joins.items():
            links[zkey].tablet_ids = links[zkey].tablet_ids + ids
        new_groups.extend((ctx, g) for g in groups)

    # 1. existing links
    for zkey, link in list(links.items()):
        z = zanns.get(zkey)
        ctx = pages.get(link.page_id)
        if ctx is None:
            # the page is gone; so is its annotation
            if z is not None:
                res.deletes.append((zkey, z["version"]))
            continue
        if link.unsynced:
            if z is None:
                continue
            if z.get("annotationType") in cv.DRAWABLE_TYPES:
                link = Link(zkey, "zotero", link.page_id)  # became drawable (type changed); treat as new below
            else:
                res.links[zkey] = replace(link, zversion=z["version"])
                continue
        marks = ctx.marks()
        members = [marks[t] for t in link.tablet_ids if t in marks]
        tablet_gone = bool(link.tablet_ids) and not members
        tablet_changed = (cv.tablet_fingerprint(members) != link.tfp) if link.tablet_ids else False
        zotero_gone = z is None
        zotero_changed = (not zotero_gone) and z["version"] != link.zversion and cv.zotero_fingerprint(z, link.page_id) != link.zfp

        if zotero_gone and (tablet_gone or not link.tablet_ids):
            continue
        if zotero_gone:
            if tablet_changed:  # edited on the tablet since; the tablet's version wins
                data = _from_tablet(members, ctx, text)
                k = new_key()
                res.creates.append({**data, "key": k})
                res.links[k] = Link(k, "tablet", ctx.page_id, [m.id for m in members], 0, cv.zotero_fingerprint(data, ctx.page_id), cv.tablet_fingerprint(members))
            else:
                ctx.page.delete({m.id for m in members})
                ctx.invalidate()
            continue
        if tablet_gone:
            if zotero_changed and _ms(z.get("dateModified")) > ctx.modified_ms:
                _draw_zotero(z, ctx, res, zkey)
            else:
                res.deletes.append((zkey, z["version"]))
            continue
        if tablet_changed and zotero_changed:
            tablet_wins = ctx.modified_ms >= _ms(z.get("dateModified"))
            tablet_changed, zotero_changed = tablet_wins, not tablet_wins
        if tablet_changed:
            data = _from_tablet(members, ctx, text)
            merged = {**z, **data, "key": zkey, "version": z["version"]}
            for keep in ("annotationComment", "tags", "annotationAuthorName"):
                if keep in z:
                    merged[keep] = z[keep]
            res.updates.append(merged)
            res.links[zkey] = replace(link, zfp=cv.zotero_fingerprint(merged, ctx.page_id), tfp=cv.tablet_fingerprint(members))
            continue
        if zotero_changed:
            if link.origin == "zotero" or all(m.on_zotero_layer for m in members):
                ctx.page.delete({m.id for m in members})
                ctx.invalidate()
                _draw_zotero(z, ctx, res, zkey, origin=link.origin)
            else:
                _apply_to_user_marks(z, link, members, ctx, res)
            continue
        res.links[zkey] = replace(link, zversion=z["version"], tfp=cv.tablet_fingerprint(members))

    # 2. Zotero annotations not linked yet
    for zkey, z in zanns.items():
        if zkey in links or zkey in res.links:
            continue
        pos = cv.position_of(z)
        ctx = by_zidx.get(int(pos.get("pageIndex", -1)))
        if ctx is None:
            continue  # on a page the tablet doesn't have (yet)
        if z.get("annotationType") not in cv.DRAWABLE_TYPES:
            res.links[zkey] = Link(zkey, "zotero", ctx.page_id, [], z["version"], cv.zotero_fingerprint(z, ctx.page_id), "", unsynced=True)
            continue
        _draw_zotero(z, ctx, res, zkey)

    # 3. new tablet groups
    for ctx, group in new_groups:
        group = [ctx.marks()[m.id] for m in group if m.id in ctx.marks()]
        if not group:
            continue
        data = _from_tablet(group, ctx, text)
        k = new_key()
        res.creates.append({**data, "key": k})
        res.links[k] = Link(k, "tablet", ctx.page_id, [m.id for m in group], 0, cv.zotero_fingerprint(data, ctx.page_id), cv.tablet_fingerprint(group))
    return res


def _from_tablet(marks: list[Mark], ctx: PageCtx, text: TextLayer | None) -> dict:
    return cv.marks_to_zotero(marks, ctx.box, ctx.zidx, ctx.label, text)


def _draw_zotero(z: dict, ctx: PageCtx, res: Result, zkey: str, origin: str = "zotero") -> None:
    values = cv.zotero_to_values(z, ctx.box)
    ids = ctx.page.add(ctx.page.zotero_layer_id(), values) if values else []
    if values:
        ctx.invalidate()
    marks = ctx.marks()
    members = [marks[i] for i in ids if i in marks]
    res.links[zkey] = Link(zkey, origin, ctx.page_id, ids, z["version"], cv.zotero_fingerprint(z, ctx.page_id), cv.tablet_fingerprint(members), unsynced=not values)


def _apply_to_user_marks(z: dict, link: Link, members: list[Mark], ctx: PageCtx, res: Result) -> None:
    """Apply a Zotero-side edit to the user's original strokes, keeping their pen and pressure (ADR 0013)."""
    color = (z.get("annotationColor") or "").lower()
    pos = cv.position_of(z)
    new_ids: list[str] = []
    layer_ids = {m.id: m.layer_id for m in members}
    if z.get("annotationType") == "ink" and all(m.is_stroke for m in members):
        paths = [[ctx.box.user_to_rm(p[i], p[i + 1]) for i in range(0, len(p) - 1, 2)] for p in pos.get("paths") or []]
        runs = cv.trim_to_paths([(m.id, m.value) for m in members], paths)
        for m in members:
            r = runs.get(m.id) or []
            original = m.value
            recolored = cv.recolor(original, color) if color and color != cv.hex_of(cv.mark_rgb(original)) else original
            if len(r) == 1 and len(r[0]) == len(original.points):
                if recolored is not original:
                    ctx.page.update_value(m.id, recolored)
                new_ids.append(m.id)
                continue
            ctx.page.delete({m.id})
            if r:
                from rmscene.tagged_block_common import CrdtId

                layer = CrdtId(*map(int, layer_ids[m.id].split(":")))
                new_ids.extend(ctx.page.add(layer, [replace(recolored, points=run) for run in r if len(run) > 0]))
    else:
        for m in members:
            v = m.value
            if m.is_highlight and z.get("annotationType") == "highlight":
                rects = [si.Rectangle(*ctx.box.user_rect_to_rm(r)) for r in pos.get("rects") or []]
                v = replace(v, rectangles=rects or v.rectangles)
            if color:
                v = cv.recolor(v, color)
            ctx.page.update_value(m.id, v)
            new_ids.append(m.id)
    ctx.invalidate()
    marks = ctx.marks()
    new_members = [marks[i] for i in new_ids if i in marks]
    res.links[link.zkey] = replace(link, tablet_ids=new_ids, zversion=z["version"], zfp=cv.zotero_fingerprint(z, ctx.page_id), tfp=cv.tablet_fingerprint(new_members))
