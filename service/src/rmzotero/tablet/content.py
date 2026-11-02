"""Helpers for a document's ``.content`` JSON, chiefly its page list.

Software 3.x keeps pages in ``cPages.pages``: each page has a stable ``id``, an
``idx`` string whose lexicographic order is the page order, an optional
``redir`` (the page index in the document's PDF; absent for note pages the
user inserted) and an optional ``deleted`` marker.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass

TS = "1:1"


@dataclass(frozen=True)
class PageRef:
    id: str
    idx: str
    redir: int | None  # PDF page index on the tablet's copy; None for a note page
    deleted: bool = False

    @property
    def is_note_page(self) -> bool:
        return self.redir is None


def _val(field):
    if isinstance(field, dict):
        return field.get("value")
    return field


def all_pages(content: dict) -> list[PageRef]:
    """All pages including deleted ones, in order."""
    cp = (content.get("cPages") or {}).get("pages")
    if cp:
        refs = []
        for p in cp:
            redir = _val(p.get("redir"))
            deleted = bool(_val(p.get("deleted"))) if p.get("deleted") is not None else False
            refs.append(PageRef(id=p["id"], idx=str(_val(p.get("idx")) or ""), redir=None if redir is None else int(redir), deleted=deleted))
        return sorted(refs, key=lambda r: r.idx)
    legacy = content.get("pages") or []
    redirs = content.get("redirectionPageMap") or []
    refs = []
    for i, pid in enumerate(legacy):
        r = redirs[i] if i < len(redirs) else i
        refs.append(PageRef(id=pid, idx=_idx(i, len(legacy)), redir=None if r is None or r < 0 else int(r)))
    return refs


def pages(content: dict) -> list[PageRef]:
    """Visible pages in order."""
    return [p for p in all_pages(content) if not p.deleted]


def _idx(i: int, n: int) -> str:
    width = 2
    while n + 26 ** (width - 1) >= 26**width:
        width += 1
    v = i + 26 ** (width - 1)
    out = ""
    for _ in range(width):
        v, r = divmod(v, 26)
        out = chr(ord("a") + r) + out
    return out


def new_pdf_content(page_count: int, file_type: str = "pdf", extra: dict | None = None) -> dict:
    """Content for a freshly uploaded PDF with an explicit page list.

    Giving pages ids up front lets the service write Zotero annotations onto a
    document before the tablet has ever opened it.
    """
    page_list = [
        {"id": str(uuid.uuid4()), "idx": {"timestamp": TS, "value": _idx(i, page_count)}, "redir": {"timestamp": TS, "value": i}}
        for i in range(page_count)
    ]
    content = {
        "dummyDocument": False,
        "extraMetadata": {},
        "fileType": file_type,
        "fontName": "",
        "lastOpenedPage": 0,
        "lineHeight": -1,
        "margins": 180,
        "orientation": "portrait",
        "pageCount": page_count,
        "originalPageCount": page_count,
        "pages": None,
        "redirectionPageMap": None,
        "textScale": 1,
        "tags": [],
        "pageTags": [],
        "cPages": {
            "lastOpened": {"timestamp": TS, "value": page_list[0]["id"] if page_list else ""},
            "original": {"timestamp": TS, "value": page_count},
            "pages": page_list,
            "uuids": [],
        },
    }
    if extra:
        content.update(extra)
    return content


def upload_content(page_count: int, file_type: str = "pdf") -> dict:
    """Content for a new upload, without a page list: the tablet creates that on first open."""
    content = new_pdf_content(page_count, file_type)
    content.pop("cPages")
    content["pages"] = []
    return content


def ensure_cpages(content: dict, pdf_page_count: int) -> dict:
    """Return content with a ``cPages`` list, creating one from legacy data or the PDF page count."""
    if (content.get("cPages") or {}).get("pages"):
        return content
    out = copy.deepcopy(content)
    refs = all_pages(content)
    if not refs:
        fresh = new_pdf_content(pdf_page_count, content.get("fileType") or "pdf")
        out["cPages"] = fresh["cPages"]
        out["pageCount"] = pdf_page_count
        return out
    out["cPages"] = {
        "pages": [
            {"id": r.id, "idx": {"timestamp": TS, "value": r.idx}, **({"redir": {"timestamp": TS, "value": r.redir}} if r.redir is not None else {})}
            for r in refs
        ]
    }
    return out


def set_deleted(content: dict, page_id: str, deleted: bool) -> dict:
    out = copy.deepcopy(content)
    for p in out["cPages"]["pages"]:
        if p["id"] == page_id:
            if deleted:
                p["deleted"] = {"timestamp": TS, "value": 1}
            else:
                p.pop("deleted", None)
    out["pageCount"] = len([p for p in out["cPages"]["pages"] if not p.get("deleted")])
    return out


def page_modified_ms(content: dict, page_id: str) -> int:
    """The tablet's per-page last-modified time (``modifed``, sic), or 0."""
    for p in (content.get("cPages") or {}).get("pages") or []:
        if p.get("id") == page_id:
            v = _val(p.get("modifed")) or _val(p.get("modified"))
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def register_author(content: dict, author_uuid: str) -> tuple[dict, int]:
    """Make sure the document lists ``author_uuid`` among its authors (``cPages.uuids``).

    Each device that edits a document's pages appears there with an index, and the
    pages' items carry that index. The tablet appears to ignore edits by authors the
    document doesn't list. Returns (content, index); content is unchanged if already listed.
    """
    cp = content.get("cPages") or {}
    uuids = list(cp.get("uuids") or [])
    for u in uuids:
        if str(u.get("first", "")).lower() == author_uuid.lower():
            return content, int(u.get("second"))
    index = max([int(u.get("second", 0)) for u in uuids] + [1]) + 1
    out = copy.deepcopy(content)
    out.setdefault("cPages", {}).setdefault("uuids", [])
    out["cPages"]["uuids"] = uuids + [{"first": author_uuid, "second": index}]
    return out, index
