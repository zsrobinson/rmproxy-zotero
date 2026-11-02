"""Two-way page-structure sync for one document (ADR 0012).

Pages have an identity: the tablet's page id. After every sync the service
records the Zotero PDF's page order as a list of tablet page ids (the
*mapping*). On the next sync:

- Zotero-side edits are found by matching page fingerprints between the last
  synced Zotero file and the current one: missing pages were deleted, pages
  whose /Rotate changed were rotated.
- Tablet-side edits are found by comparing the tablet's page list with the
  mapping: new pages were inserted (note pages), missing pages were deleted.

Each side's edits are replayed on the other. A page deleted on one side but
written on by the other since the last sync is kept, with a warning.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from .pdf import edit
from .pdf.geometry import NOTE_PAGE_SIZE
from .tablet.content import PageRef


class StructureError(Exception):
    pass


@dataclass
class StructureResult:
    final_pdf: bytes | None  # new Zotero file to upload, or None if unchanged
    mapping: list[str]  # tablet page ids in final Zotero page order
    current_zidx_to_page: dict[int, str]  # Zotero page index in the *current* file -> page id
    tablet_delete: set[str] = field(default_factory=set)
    tablet_undelete: set[str] = field(default_factory=set)
    tablet_rotations: dict[str, int] = field(default_factory=dict)  # page id -> new /Rotate for the tablet's PDF page
    deleted_pages: set[str] = field(default_factory=set)  # page ids gone from both sides after this sync
    warnings: list[str] = field(default_factory=list)


def reconcile_structure(
    *,
    tablet_pages: list[PageRef],  # visible tablet pages, in order
    tablet_all: list[PageRef],  # including deleted
    last_mapping: list[str] | None,
    old_zotero: bytes | None,  # Zotero file at the last sync
    new_zotero: bytes,  # Zotero file now
    tablet_dirty_pages: set[str],  # pages written on at the tablet since the last sync
    zotero_dirty_zidx: set[int],  # current Zotero page indices annotated in Zotero since the last sync
) -> StructureResult:
    new_count = edit.page_count(new_zotero)
    visible_ids = [p.id for p in tablet_pages]

    if last_mapping is None:
        # first sync: the tablet's PDF pages correspond one to one with the Zotero file
        by_redir = {p.redir: p.id for p in tablet_pages if p.redir is not None}
        cur = {i: by_redir[i] for i in range(new_count) if i in by_redir}
        if len(cur) != new_count:
            raise StructureError("the tablet's copy doesn't match the Zotero file page for page")
        last_mapping = [cur[i] for i in range(new_count)]
        old_zotero = new_zotero

    # -- Zotero-side changes ------------------------------------------------------------
    old_fps = edit.page_fingerprints(old_zotero) if old_zotero is not None else []
    new_fps = edit.page_fingerprints(new_zotero)
    if len(old_fps) != len(last_mapping):
        raise StructureError("internal: mapping and last Zotero file disagree")
    matcher = difflib.SequenceMatcher(a=old_fps, b=new_fps, autojunk=False)
    old_to_new: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            old_to_new[block.a + k] = block.b + k
    unmatched_new = [j for j in range(len(new_fps)) if j not in old_to_new.values()]
    if unmatched_new:
        raise StructureError(
            "the Zotero file gained pages or was replaced; add the new version as its own attachment instead (ADR 0011)"
        )
    current_zidx_to_page = {old_to_new[i]: pid for i, pid in enumerate(last_mapping) if i in old_to_new}
    zotero_deleted = {pid for i, pid in enumerate(last_mapping) if i not in old_to_new}

    old_rot = edit.rotations(old_zotero) if old_zotero is not None else []
    new_rot = edit.rotations(new_zotero)
    res = StructureResult(final_pdf=None, mapping=[], current_zidx_to_page=current_zidx_to_page)
    for i, pid in enumerate(last_mapping):
        j = old_to_new.get(i)
        if j is not None and old_rot[i] != new_rot[j]:
            res.tablet_rotations[pid] = new_rot[j]

    # -- tablet-side changes -------------------------------------------------------------
    known = set(last_mapping)
    tablet_deleted = {pid for pid in last_mapping if pid not in visible_ids and pid not in zotero_deleted}
    page_to_cur = {pid: z for z, pid in current_zidx_to_page.items()}

    keep_on_tablet: set[str] = set()  # Zotero deleted it, but the tablet wrote on it
    for pid in sorted(zotero_deleted):
        if pid in tablet_dirty_pages:
            keep_on_tablet.add(pid)
            res.warnings.append("A page deleted in Zotero had new tablet markup, so it was kept.")
        elif pid in visible_ids:
            res.tablet_delete.add(pid)
        if pid not in keep_on_tablet:
            res.deleted_pages.add(pid)
    keep_in_zotero: set[str] = set()  # the tablet deleted it, but Zotero annotated it
    for pid in sorted(tablet_deleted):
        if page_to_cur.get(pid) in zotero_dirty_zidx:
            keep_in_zotero.add(pid)
            res.tablet_undelete.add(pid)
            res.warnings.append("A page deleted on the tablet had new Zotero annotations, so it was kept.")
        else:
            res.deleted_pages.add(pid)

    # final page order: the tablet's order, with pages kept by conflict put back in their old place
    order: list[str] = []
    all_ids_in_order = [p.id for p in tablet_all]
    for pid in all_ids_in_order:
        if (pid in visible_ids and pid not in res.tablet_delete) or pid in keep_in_zotero:
            order.append(pid)
    # pages the Zotero side deleted but the tablet kept are already visible on the tablet
    res.mapping = order

    # -- build the final Zotero file -------------------------------------------------------
    changed = False
    data = new_zotero
    # 1. restore pages Zotero deleted but the tablet kept
    restores = [(pid, last_mapping.index(pid)) for pid in keep_on_tablet]
    # 2. remove pages the tablet deleted
    removals = {page_to_cur[pid] for pid in tablet_deleted if pid not in keep_in_zotero and pid in page_to_cur}
    if removals:
        data = edit.delete_pages(data, removals)
        changed = True
    # positions after removal
    surviving = [pid for z, pid in sorted(current_zidx_to_page.items()) if z not in removals]
    # 3. insert blank pages (tablet note pages) and restored pages so the file follows `order`
    present = list(surviving)
    for target, pid in enumerate(order):
        if target < len(present) and present[target] == pid:
            continue
        if pid in present:
            raise StructureError("page order differs between Zotero and the tablet; reordering isn't supported")
        if pid in dict(restores) and old_zotero is not None:
            data = edit.copy_page_into(old_zotero, dict(restores)[pid], data, target)
        else:
            if pid in known:
                raise StructureError("internal: known page missing from the Zotero file")
            data = edit.insert_blank_pages(data, [(target, NOTE_PAGE_SIZE)])
        present.insert(target, pid)
        changed = True
    if present != order:
        raise StructureError("internal: could not align page order")
    res.final_pdf = data if changed else None
    return res
