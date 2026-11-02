"""The sync15 index format used by the tablet and rmfakecloud.

A *document index* lists the files of one document; the *root index* lists all
documents. Both are text files stored as blobs and addressed by hash:

- document index: schema line ``3`` then ``<hash>:0:<name>:0:<size>`` per file,
  sorted by name. Its hash is SHA-256 over the concatenated binary hashes of
  its entries (the v3 rule).
- root index: schema line ``4``, a summary line ``0:.:<count>:<total size>``,
  then ``<hash>:0:<docid>:<file count>:<size>`` per document, sorted by id. Its
  hash is SHA-256 of the serialised bytes. Older servers may hand back a v3
  root (type field ``80000000``, no summary line); both are parsed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

DOC_TYPE_V3 = "80000000"
FILE_TYPE = "0"


@dataclass(frozen=True)
class Entry:
    hash: str
    name: str  # file name (document index) or document id (root index)
    size: int
    subfiles: int = 0

    def line(self, type_field: str = FILE_TYPE) -> str:
        return f"{self.hash}:{type_field}:{self.name}:{self.subfiles}:{self.size}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_index(data: bytes) -> tuple[str, list[Entry]]:
    """Parse an index blob, returning ``(schema, entries)``."""
    lines = [line.strip() for line in data.decode("utf-8").splitlines()]
    if not lines:
        raise ValueError("empty index")
    schema = lines[0].strip()
    body = lines[1:]
    if schema == "4":
        if not body:
            raise ValueError("v4 index without summary line")
        body = body[1:]
    elif schema != "3":
        raise ValueError(f"unsupported index schema {schema!r}")
    entries = []
    for line in body:
        if not line.strip():
            continue
        parts = line.split(":")
        if len(parts) != 5:
            raise ValueError(f"bad index line {line!r}")
        h, _type, name, subfiles, size = parts
        entries.append(Entry(hash=h, name=name, size=int(size), subfiles=int(subfiles)))
    return schema, entries


def doc_index(entries: list[Entry]) -> tuple[str, bytes]:
    """Serialise a document index; returns ``(hash, bytes)``."""
    ordered = sorted(entries, key=lambda e: e.name)
    body = "3\n" + "".join(e.line() + "\n" for e in ordered)
    h = hashlib.sha256(b"".join(bytes.fromhex(e.hash) for e in ordered)).hexdigest()
    return h, body.encode()


def root_index(entries: list[Entry]) -> tuple[str, bytes]:
    """Serialise a v4 root index; returns ``(hash, bytes)``."""
    ordered = sorted(entries, key=lambda e: e.name)
    total = sum(e.size for e in ordered)
    body = f"4\n0:.:{len(ordered)}:{total}\n" + "".join(e.line() + "\n" for e in ordered)
    data = body.encode()
    return sha256_hex(data), data
