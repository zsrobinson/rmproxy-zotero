"""What the tablet should contain, derived from the Zotero library (ADR 0006, 0011).

The planner is pure: it reads the library, settings and a few user choices
recorded in the state (home-collection overrides, derived attachments,
suppressed pointers) and returns the desired folders, documents and pointers.
The engine compares this with the tablet and acts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .state import State
from .zotero.library import Library
from .zotero.naming import document_names

UNFILED = "__unfiled__"
UNFILED_NAME = "Unfiled Items"
ROOT_PREFIX = "__root__:"


@dataclass
class DesiredDoc:
    att_key: str  # the attachment whose file is on the tablet and holds the annotations
    source_key: str  # the user's attachment it comes from (same key unless derived)
    kind: str  # pdf | rendition | searchable | epub
    item_key: str | None
    name: str
    folder: str  # folder key
    location: str  # human-readable tablet path of the folder


@dataclass
class DesiredPointer:
    att_key: str
    folder: str
    name: str
    title: str
    location: str  # where the real document lives


@dataclass
class Derivation:
    source_key: str
    kind: str  # "rendition" (EPUB) or "ocr-check" (PDF)


@dataclass
class Plan:
    folders: dict[str, tuple[str, str]] = field(default_factory=dict)  # fkey -> (name, parent fkey or "")
    docs: dict[str, DesiredDoc] = field(default_factory=dict)
    pointers: list[DesiredPointer] = field(default_factory=list)
    pending: list[Derivation] = field(default_factory=list)
    collection_items: dict[str, set[str]] = field(default_factory=dict)  # collection -> synced attachment keys (any home)


def excluded_set(lib: Library, settings: Settings) -> set[str]:
    out: set[str] = set()
    for c in settings.excluded_collections:
        if c in lib.collections:
            out.add(c)
            out.update(lib.collection_descendants(c))
    return out


def home_collection(lib: Library, candidates: list[str], override: str | None) -> str:
    if override and override in candidates:
        return override
    ancestors = {a for c in candidates for a in lib.collection_ancestors(c)}
    leaves = [c for c in candidates if c not in ancestors] or candidates
    return min(leaves, key=lambda c: [p.lower() for p in lib.collection_path(c)])


def _derived(state: State) -> tuple[dict[tuple[str, str], str | None], set[str]]:
    """((source, kind) -> derived key or None when nothing was needed, source keys to skip)."""
    import json

    out, skip = {}, set()
    for r in state.all("SELECT * FROM derived"):
        out[(r["source_key"], r["kind"])] = r["derived_key"]
        if (json.loads(r["params"] or "{}") or {}).get("skip"):
            skip.add(r["source_key"])
    return out, skip


def plan(lib: Library, settings: Settings, state: State) -> Plan:
    p = Plan()
    excluded = excluded_set(lib, settings)
    derived, skipped = _derived(state)
    derived_keys = {v for v in derived.values() if v}
    overrides = {r["item_key"]: r["collection_key"] for r in state.all("SELECT * FROM home_overrides")}
    suppressed = {(r["att_key"], r["collection_key"]) for r in state.all("SELECT * FROM pointers WHERE suppressed=1")}

    root_parts = [x for x in settings.sync_root.split("/") if x]
    parent = ""
    for i, part in enumerate(root_parts):
        fkey = ROOT_PREFIX + "/".join(root_parts[: i + 1])
        p.folders[fkey] = (part, parent)
        parent = fkey
    root_fkey = parent  # "" = the tablet's top level

    def location(fkey: str) -> str:
        parts = root_parts + (lib.collection_path(fkey) if fkey in lib.collections else [UNFILED_NAME])
        return "/".join(parts)

    def need_folder(fkey: str) -> None:
        if fkey in p.folders:
            return
        if fkey == UNFILED:
            p.folders[fkey] = (UNFILED_NAME, root_fkey)
            return
        c = lib.collections[fkey]
        par = c.get("parentCollection") or ""
        if par and par in lib.collections:
            need_folder(par)
            p.folders[fkey] = (c["name"], par)
        else:
            p.folders[fkey] = (c["name"], root_fkey)

    owners: list[tuple[dict | None, list[dict], list[str]]] = []
    for item in lib.regular_items():
        if lib.is_trashed(item["key"]):
            continue
        atts = [a for a in lib.attachments(item["key"]) if lib.is_syncable_file(a) and not a.get("deleted") and a["key"] not in derived_keys]
        if atts:
            owners.append((item, atts, list(item.get("collections") or [])))
    for att in lib.standalone_attachments():
        if lib.is_syncable_file(att) and not lib.is_trashed(att["key"]) and att["key"] not in derived_keys:
            owners.append((None, [att], list(att.get("collections") or [])))

    for item, atts, cols in owners:
        live = [c for c in cols if c in lib.collections and not lib.collection_trashed(c)]
        included = [c for c in live if c not in excluded]
        if live and not included:
            continue  # only in excluded collections
        # which attachment actually goes to the tablet
        synced: list[tuple[dict, str, str]] = []  # (source attachment, synced key, kind)
        for a in atts:
            if a["key"] in skipped:
                continue  # e.g. DRM-protected; the plugin shows why
            if a.get("contentType") == "application/epub+zip":
                key = derived.get((a["key"], "rendition"), "missing")
                if key == "missing":
                    p.pending.append(Derivation(a["key"], "rendition"))
                    continue
                synced.append((a, key or a["key"], "rendition" if key else "epub"))  # None = fixed layout/DRM-free original
            else:
                if settings.ocr:
                    key = derived.get((a["key"], "searchable"), "missing")
                    if key == "missing":
                        p.pending.append(Derivation(a["key"], "ocr-check"))
                        continue
                    synced.append((a, key or a["key"], "searchable" if key else "pdf"))
                else:
                    synced.append((a, a["key"], "pdf"))
        if not synced:
            continue
        item_key = item["key"] if item else None
        home = home_collection(lib, included, overrides.get(item_key or synced[0][0]["key"])) if included else UNFILED
        need_folder(home)
        names = document_names(item, [s[0] for s in synced], settings.filename_template or None)
        for src, key, kind in synced:
            name = names[src["key"]]
            p.docs[key] = DesiredDoc(key, src["key"], kind, item_key, name, home, location(home))
            for c in included:
                p.collection_items.setdefault(c, set()).add(key)
            if settings.pointers == "none":
                continue
            for c in included:
                if c == home:
                    continue
                related = c in lib.collection_ancestors(home) or home in lib.collection_ancestors(c)
                if settings.pointers == "unrelated" and related:
                    continue
                if (key, c) in suppressed:
                    continue
                need_folder(c)
                p.pointers.append(DesiredPointer(key, c, f"→ {name}", name, location(home)))
    return p
