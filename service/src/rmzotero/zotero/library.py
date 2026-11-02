"""A local mirror of the Zotero library, refreshed incrementally by library version."""

from __future__ import annotations

import json
import logging
from collections import defaultdict

from ..state import State
from .client import ZoteroAPI

log = logging.getLogger(__name__)

SYNCABLE_TYPES = {"application/pdf", "application/epub+zip"}
FILE_LINK_MODES = {"imported_file", "imported_url"}


class Library:
    def __init__(self, state: State):
        self.state = state
        self.version: int = state.get("zotero_version", 0)
        self.collections: dict[str, dict] = {}
        self.items: dict[str, dict] = {}
        for row in state.zobjects():
            (self.collections if row["kind"] == "collection" else self.items)[row["key"]] = json.loads(row["data"])
        self._index()

    def _index(self) -> None:
        self.children: dict[str, list[str]] = defaultdict(list)
        for key, it in self.items.items():
            parent = it.get("parentItem")
            if parent:
                self.children[parent].append(key)
        self.subcollections: dict[str, list[str]] = defaultdict(list)
        for key, c in self.collections.items():
            parent = c.get("parentCollection") or None
            self.subcollections[parent or ""].append(key)

    def refresh(self, api: ZoteroAPI) -> set[str]:
        """Pull changes since the last version. Returns keys of changed or deleted objects."""
        cols, items, deleted, version = api.changes_since(self.version)
        if version == self.version and not cols and not items:
            return set()
        changed: set[str] = set()
        rows = []
        for c in cols:
            self.collections[c["key"]] = c
            rows.append((c["key"], "collection", c["version"], c))
            changed.add(c["key"])
        for it in items:
            self.items[it["key"]] = it
            rows.append((it["key"], "item", it["version"], it))
            changed.add(it["key"])
        gone = list(deleted.get("collections") or []) + list(deleted.get("items") or [])
        for k in gone:
            self.collections.pop(k, None)
            self.items.pop(k, None)
            changed.add(k)
        self.state.save_zobjects(rows, gone)
        self.version = version
        self.state.set("zotero_version", version)
        self._index()
        return changed

    def apply_local_write(self, objects: list[dict]) -> None:
        """Record objects we just wrote (with their new versions) without waiting for the next pull."""
        rows = []
        for o in objects:
            self.items[o["key"]] = o
            rows.append((o["key"], "item", o.get("version", 0), o))
        for k in [o["key"] for o in objects if o.get("_deleted")]:
            self.items.pop(k, None)
        self.state.save_zobjects([r for r in rows if not r[3].get("_deleted")], [o["key"] for o in objects if o.get("_deleted")])
        self._index()

    def forget(self, keys: list[str]) -> None:
        for k in keys:
            self.items.pop(k, None)
        self.state.save_zobjects([], keys)
        self._index()

    # -- queries --------------------------------------------------------------------------

    def is_trashed(self, key: str) -> bool:
        it = self.items.get(key)
        seen = set()
        while it is not None and it["key"] not in seen:
            if it.get("deleted"):
                return True
            seen.add(it["key"])
            parent = it.get("parentItem")
            it = self.items.get(parent) if parent else None
        return False

    def collection_trashed(self, ckey: str) -> bool:
        c = self.collections.get(ckey)
        while c is not None:
            if c.get("deleted"):
                return True
            c = self.collections.get(c.get("parentCollection") or "")
        return False

    def collection_ancestors(self, ckey: str) -> list[str]:
        out = []
        c = self.collections.get(ckey)
        while c is not None and c.get("parentCollection"):
            p = c["parentCollection"]
            if p in out:
                break
            out.append(p)
            c = self.collections.get(p)
        return out

    def collection_path(self, ckey: str) -> list[str]:
        keys = [ckey] + self.collection_ancestors(ckey)
        return [self.collections[k]["name"] for k in reversed(keys) if k in self.collections]

    def collection_descendants(self, ckey: str) -> list[str]:
        out, stack = [], list(self.subcollections.get(ckey, []))
        while stack:
            k = stack.pop()
            out.append(k)
            stack.extend(self.subcollections.get(k, []))
        return out

    def attachments(self, item_key: str) -> list[dict]:
        return [self.items[k] for k in self.children.get(item_key, []) if self.items[k].get("itemType") == "attachment"]

    def annotations(self, att_key: str) -> dict[str, dict]:
        return {k: self.items[k] for k in self.children.get(att_key, [])
                if self.items[k].get("itemType") == "annotation" and not self.items[k].get("deleted")}

    def is_syncable_file(self, att: dict) -> bool:
        return (att.get("itemType") == "attachment" and att.get("linkMode") in FILE_LINK_MODES
                and att.get("contentType") in SYNCABLE_TYPES)

    def regular_items(self) -> list[dict]:
        return [it for it in self.items.values() if it.get("itemType") not in ("attachment", "note", "annotation")]

    def standalone_attachments(self) -> list[dict]:
        return [it for it in self.items.values() if it.get("itemType") == "attachment" and not it.get("parentItem")]
