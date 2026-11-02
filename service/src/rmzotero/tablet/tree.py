"""A local mirror of the tablet's cloud tree, with staged writes and optimistic commits.

Writes follow the protocol the tablet itself uses: upload new file blobs and
document indexes first (content addressed, so harmless), then swap the root
index with the generation we last read. If someone else (normally the tablet)
committed in between, the swap fails with a generation conflict; the caller
re-mirrors, drops staged changes to documents that changed remotely, and
retries. Documents are never overwritten blindly.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .cloud import BlobStore, GenerationConflict
from .index import Entry, doc_index, parse_index, root_index, sha256_hex

log = logging.getLogger(__name__)

TRASH = "trash"
DOCUMENT = "DocumentType"
COLLECTION = "CollectionType"


class BlobCache:
    """Content-addressed on-disk cache of cloud blobs. Doubles as the exact-page archive."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, h: str) -> Path:
        return self.root / h[:2] / h

    def has(self, h: str) -> bool:
        return self._path(h).exists()

    def put(self, data: bytes) -> str:
        h = sha256_hex(data)
        p = self._path(h)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return h

    def get_local(self, h: str) -> bytes | None:
        p = self._path(h)
        return p.read_bytes() if p.exists() else None

    def get(self, store: BlobStore, h: str, filename: str = "") -> bytes:
        data = self.get_local(h)
        if data is None:
            data = store.get_blob(h, filename)
            self.put(data)
        return data


@dataclass
class TabletDoc:
    id: str
    hash: str
    entries: dict[str, Entry]
    metadata: dict
    content: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.metadata.get("visibleName", "")

    @property
    def parent(self) -> str:
        return self.metadata.get("parent", "")

    @property
    def type(self) -> str:
        return self.metadata.get("type", DOCUMENT)

    @property
    def is_folder(self) -> bool:
        return self.type == COLLECTION

    @property
    def size(self) -> int:
        return sum(e.size for e in self.entries.values())

    def file(self, suffix: str) -> Entry | None:
        """The entry ``<id><suffix>``, e.g. ``.pdf`` or ``.content``."""
        return self.entries.get(self.id + suffix)

    def page_entries(self) -> dict[str, Entry]:
        """Map page id -> entry for the document's ``.rm`` page files."""
        prefix = self.id + "/"
        out = {}
        for name, e in self.entries.items():
            if name.startswith(prefix) and name.endswith(".rm"):
                out[name[len(prefix) : -3]] = e
        return out

    def root_entry(self) -> Entry:
        return Entry(hash=self.hash, name=self.id, size=self.size, subfiles=len(self.entries))


def now_ms() -> str:
    return str(int(time.time() * 1000))


def new_metadata(name: str, parent: str, doc_type: str = DOCUMENT) -> dict:
    return {
        "visibleName": name,
        "type": doc_type,
        "parent": parent,
        "lastModified": now_ms(),
        "lastOpened": "",
        "lastOpenedPage": 0,
        "version": 0,
        "pinned": False,
        "synced": True,
        "modified": False,
        "deleted": False,
        "metadatamodified": False,
    }


class CloudTree:
    def __init__(self, store: BlobStore, cache: BlobCache):
        self.store = store
        self.cache = cache
        self.root_hash = ""
        self.generation = 0
        self.docs: dict[str, TabletDoc] = {}
        # staged: doc id -> (base hash or None for new, new doc or None for removal)
        self._staged: dict[str, tuple[str | None, TabletDoc | None]] = {}

    # -- reading --------------------------------------------------------------

    def mirror(self) -> bool:
        """Bring the local view up to date. Returns True if anything changed."""
        root_hash, generation = self.store.get_root()
        self.generation = generation
        if root_hash == self.root_hash:
            return False
        docs: dict[str, TabletDoc] = {}
        if root_hash:
            _schema, entries = parse_index(self.cache.get(self.store, root_hash, "root.docSchema"))
            for e in entries:
                old = self.docs.get(e.name)
                if old and old.hash == e.hash:
                    docs[e.name] = old
                    continue
                try:
                    docs[e.name] = self._load_doc(e.name, e.hash)
                except Exception:  # a broken document must not stop the sync
                    log.exception("cannot read tablet document %s", e.name)
        self.docs = docs
        self.root_hash = root_hash
        return True

    def _load_doc(self, doc_id: str, h: str) -> TabletDoc:
        _schema, entries = parse_index(self.cache.get(self.store, h, doc_id + ".docSchema"))
        by_name = {e.name: e for e in entries}
        doc = TabletDoc(id=doc_id, hash=h, entries=by_name, metadata={})
        meta = doc.file(".metadata")
        if meta:
            doc.metadata = json.loads(self.read(meta))
        content = doc.file(".content")
        if content:
            try:
                doc.content = json.loads(self.read(content) or b"{}")
            except ValueError:
                doc.content = {}
        return doc

    def read(self, entry: Entry) -> bytes:
        return self.cache.get(self.store, entry.hash, entry.name)

    def path_of(self, doc_id: str) -> list[str]:
        """Folder names from the tablet root down to (not including) the doc; ``['trash']`` if trashed."""
        names: list[str] = []
        seen = set()
        cur = self.docs.get(doc_id)
        parent = cur.parent if cur else ""
        while parent and parent not in seen:
            if parent == TRASH:
                return [TRASH] + names
            seen.add(parent)
            folder = self.docs.get(parent)
            if folder is None:
                break
            names.insert(0, folder.name)
            parent = folder.parent
        return names

    def is_trashed(self, doc_id: str) -> bool:
        return self.path_of(doc_id)[:1] == [TRASH]

    # -- staging --------------------------------------------------------------

    def stage(
        self,
        doc_id: str,
        *,
        files: dict[str, bytes] | None = None,
        remove_files: set[str] | None = None,
        metadata: dict | None = None,
        content: dict | None = None,
    ) -> TabletDoc:
        """Stage a new version of a document. File names are relative suffixes or full names.

        ``files`` maps names like ``".pdf"`` / ``"/<page>.rm"`` (suffixes appended to the id)
        or full names to bytes. ``metadata``/``content`` replace the JSON files.
        """
        base = self._current(doc_id)
        entries = dict(base.entries) if base else {}
        meta = dict(metadata if metadata is not None else (base.metadata if base else {}))
        cont = content if content is not None else (base.content if base else {})

        def full(name: str) -> str:
            return name if name.startswith(doc_id) else doc_id + name

        for name in remove_files or ():
            entries.pop(full(name), None)
        uploads = dict(files or {})
        if metadata is not None or base is None:
            meta["lastModified"] = now_ms()
            if base is not None:
                meta["version"] = int(meta.get("version", 0)) + 1
            uploads[".metadata"] = json.dumps(meta).encode()
        if content is not None:
            uploads[".content"] = json.dumps(cont).encode()
        for name, data in uploads.items():
            fname = full(name)
            h = self.cache.put(data)
            self.store.put_blob(h, fname, data)
            entries[fname] = Entry(hash=h, name=fname, size=len(data))
        h, body = doc_index(list(entries.values()))
        self.store.put_blob(h, doc_id + ".docSchema", body)
        self.cache.put(body)
        doc = TabletDoc(id=doc_id, hash=h, entries=entries, metadata=meta, content=dict(cont))
        base_hash = self._staged[doc_id][0] if doc_id in self._staged else (self.docs[doc_id].hash if doc_id in self.docs else None)
        self._staged[doc_id] = (base_hash, doc)
        return doc

    def stage_remove(self, doc_id: str) -> None:
        base_hash = self._staged[doc_id][0] if doc_id in self._staged else (self.docs[doc_id].hash if doc_id in self.docs else None)
        self._staged[doc_id] = (base_hash, None)

    def _current(self, doc_id: str) -> TabletDoc | None:
        if doc_id in self._staged:
            return self._staged[doc_id][1]
        return self.docs.get(doc_id)

    def get(self, doc_id: str) -> TabletDoc | None:
        """The document as it will be after staged changes."""
        return self._current(doc_id)

    def all_docs(self) -> dict[str, TabletDoc]:
        out = dict(self.docs)
        for doc_id, (_base, doc) in self._staged.items():
            if doc is None:
                out.pop(doc_id, None)
            else:
                out[doc_id] = doc
        return out

    @property
    def has_staged(self) -> bool:
        return bool(self._staged)

    def discard_staged(self) -> None:
        self._staged.clear()

    # -- committing ------------------------------------------------------------

    def commit(self, max_attempts: int = 5) -> list[str]:
        """Commit staged changes. Returns the ids of staged docs dropped because they changed remotely."""
        dropped: list[str] = []
        for _ in range(max_attempts):
            if not self._staged:
                return dropped
            docs = self.all_docs()
            h, body = root_index([d.root_entry() for d in docs.values()])
            self.store.put_blob(h, "root.docSchema", body)
            self.cache.put(body)
            try:
                self.generation = self.store.put_root(h, self.generation)
            except GenerationConflict:
                log.info("tablet tree changed while committing; re-reading")
                staged = self._staged
                self._staged = {}
                self.root_hash = ""  # force a full re-read of the root
                self.mirror()
                for doc_id, (base_hash, doc) in staged.items():
                    remote = self.docs.get(doc_id)
                    remote_hash = remote.hash if remote else None
                    if remote_hash != base_hash:
                        dropped.append(doc_id)
                        continue
                    self._staged[doc_id] = (remote_hash, doc)
                continue
            self.docs = docs
            self.root_hash = h
            self._staged = {}
            return dropped
        raise RuntimeError("could not commit to the tablet cloud after repeated conflicts")
