"""Reading and editing v6 ``.rm`` page files.

A page is a list of rmscene blocks. Edits are made at block level so that
everything the service doesn't understand is written back untouched:

- deleting an item turns its block into a CRDT tombstone (value removed,
  ``deleted_length`` 1), the way the tablet records deletions;
- adding an item appends a block to the end of a layer's sequence, with ids
  under the service's own author id;
- items the service writes for Zotero annotations live on a dedicated layer
  labelled "Zotero" (ADR 0007).
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, replace

from rmscene import scene_items as si
from rmscene.crdt_sequence import CrdtSequenceItem
from rmscene.scene_stream import (
    AuthorIdsBlock,
    Block,
    MigrationInfoBlock,
    PageInfoBlock,
    SceneGlyphItemBlock,
    SceneGroupItemBlock,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
    read_blocks,
    read_tree,
    write_blocks,
)
from rmscene.tagged_block_common import CrdtId, LwwValue

ZOTERO_LAYER = "Zotero"
ROOT = CrdtId(0, 1)
END = CrdtId(0, 0)
SERVICE_AUTHOR = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/zsrobinson/rmproxy-zotero")


def id_str(c: CrdtId) -> str:
    return f"{c.part1}:{c.part2}"


def id_parse(s: str) -> CrdtId:
    a, b = s.split(":")
    return CrdtId(int(a), int(b))


@dataclass
class Mark:
    """One visible item on a page."""

    id: str
    layer: str  # layer label
    layer_id: str
    order: int  # global draw order on the page
    value: si.Line | si.GlyphRange

    @property
    def is_stroke(self) -> bool:
        return isinstance(self.value, si.Line)

    @property
    def is_highlight(self) -> bool:
        return isinstance(self.value, si.GlyphRange)

    @property
    def on_zotero_layer(self) -> bool:
        return self.layer == ZOTERO_LAYER


class Page:
    def __init__(self, blocks: list[Block]):
        self.blocks = blocks

    # -- construction ---------------------------------------------------------

    @classmethod
    def parse(cls, data: bytes) -> "Page":
        return cls(list(read_blocks(io.BytesIO(data))))

    @classmethod
    def empty(cls) -> "Page":
        layer = CrdtId(0, 11)
        blocks: list[Block] = [
            AuthorIdsBlock(author_uuids={1: uuid.uuid4()}),
            MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
            PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
            SceneTreeBlock(tree_id=layer, node_id=CrdtId(0, 0), is_update=True, parent_id=ROOT),
            TreeNodeBlock(si.Group(node_id=ROOT)),
            TreeNodeBlock(si.Group(node_id=layer, label=LwwValue(timestamp=CrdtId(0, 12), value="Layer 1"))),
            SceneGroupItemBlock(
                parent_id=ROOT,
                item=CrdtSequenceItem(item_id=CrdtId(0, 13), left_id=END, right_id=END, deleted_length=0, value=layer),
            ),
        ]
        return cls(blocks)

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        write_blocks(buf, self.blocks, options={"version": "3.6"})
        return buf.getvalue()

    # -- reading ----------------------------------------------------------------

    def layers(self) -> list[tuple[CrdtId, str]]:
        """(node id, label) for each layer, in order."""
        tree = read_tree(io.BytesIO(self.to_bytes()))
        out = []
        for _item_id, group in tree.root.children.items():
            if group is None:
                continue
            label = group.label.value if group.label is not None else ""
            out.append((group.node_id, label))
        return out

    def marks(self) -> list[Mark]:
        tree = read_tree(io.BytesIO(self.to_bytes()))
        out: list[Mark] = []
        order = 0
        for _gid, group in tree.root.children.items():
            if group is None:
                continue
            label = group.label.value if group.label is not None else ""
            for item_id, value in group.children.items():
                if isinstance(value, (si.Line, si.GlyphRange)):
                    out.append(Mark(id=id_str(item_id), layer=label, layer_id=id_str(group.node_id), order=order, value=value))
                    order += 1
        return out

    # -- editing ------------------------------------------------------------------

    def _author(self) -> int:
        ab = next((b for b in self.blocks if isinstance(b, AuthorIdsBlock)), None)
        if ab is None:
            ab = AuthorIdsBlock(author_uuids={})
            self.blocks.insert(0, ab)
        for k, v in ab.author_uuids.items():
            if v == SERVICE_AUTHOR:
                return k
        k = max(ab.author_uuids, default=1) + 1
        ab.author_uuids[k] = SERVICE_AUTHOR
        return k

    def _next_id(self) -> CrdtId:
        author = self._author()
        hi = 0
        for b in self.blocks:
            for attr in ("tree_id",):
                v = getattr(b, attr, None)
                if isinstance(v, CrdtId) and v.part1 == author:
                    hi = max(hi, v.part2)
            item = getattr(b, "item", None)
            if item is not None and item.item_id.part1 == author:
                hi = max(hi, item.item_id.part2)
        return CrdtId(author, hi + 1)

    def _insert_after_last(self, block: Block, kind: type) -> None:
        idx = max((i for i, b in enumerate(self.blocks) if isinstance(b, kind)), default=None)
        if idx is None:
            self.blocks.append(block)
        else:
            self.blocks.insert(idx + 1, block)

    def zotero_layer_id(self, create: bool = True) -> CrdtId | None:
        for node_id, label in self.layers():
            if label == ZOTERO_LAYER:
                return node_id
        if not create:
            return None
        node = self._next_id()
        self._insert_after_last(SceneTreeBlock(tree_id=node, node_id=CrdtId(0, 0), is_update=True, parent_id=ROOT), SceneTreeBlock)
        label_ts = CrdtId(node.part1, node.part2 + 1)
        self._insert_after_last(TreeNodeBlock(si.Group(node_id=node, label=LwwValue(timestamp=label_ts, value=ZOTERO_LAYER))), TreeNodeBlock)
        left = self._last_item(ROOT, SceneGroupItemBlock)
        group_item = CrdtSequenceItem(item_id=CrdtId(node.part1, node.part2 + 2), left_id=left, right_id=END, deleted_length=0, value=node)
        self._insert_after_last(SceneGroupItemBlock(parent_id=ROOT, item=group_item), SceneGroupItemBlock)
        return node

    def first_user_layer_id(self) -> CrdtId:
        for node_id, label in self.layers():
            if label != ZOTERO_LAYER:
                return node_id
        raise ValueError("page has no user layer")

    def _last_item(self, parent: CrdtId, kind: type | tuple = (SceneLineItemBlock, SceneGlyphItemBlock)) -> CrdtId:
        tree = read_tree(io.BytesIO(self.to_bytes()))
        if parent == ROOT:
            ids = tree.root.children.keys()
        else:
            try:
                ids = tree[parent].children.keys()
            except KeyError:
                ids = []
        return ids[-1] if ids else END

    def add(self, layer: CrdtId, values: list[si.Line | si.GlyphRange]) -> list[str]:
        """Append items to a layer; returns their ids."""
        ids = []
        left = self._last_item(layer)
        for v in values:
            new_id = self._next_id()
            item = CrdtSequenceItem(item_id=new_id, left_id=left, right_id=END, deleted_length=0, value=v)
            block_cls = SceneLineItemBlock if isinstance(v, si.Line) else SceneGlyphItemBlock
            self.blocks.append(block_cls(parent_id=layer, item=item))
            ids.append(id_str(new_id))
            left = new_id
        return ids

    def delete(self, ids: set[str]) -> int:
        n = 0
        for i, b in enumerate(self.blocks):
            item = getattr(b, "item", None)
            if isinstance(b, (SceneLineItemBlock, SceneGlyphItemBlock)) and item is not None and item.value is not None:
                if id_str(item.item_id) in ids:
                    self.blocks[i] = replace(b, item=replace(item, value=None, deleted_length=1))
                    n += 1
        return n

    def update_value(self, item_id: str, value: si.Line | si.GlyphRange) -> None:
        """Change an item in place (used for colour changes and trimmed strokes)."""
        for i, b in enumerate(self.blocks):
            item = getattr(b, "item", None)
            if isinstance(b, (SceneLineItemBlock, SceneGlyphItemBlock)) and item is not None and id_str(item.item_id) == item_id:
                self.blocks[i] = replace(b, item=replace(item, value=value))
                return
        raise KeyError(item_id)

    def transform_all(self, fn) -> None:
        """Apply ``fn(x, y) -> (x, y)`` to every stroke point and highlight rectangle."""
        for i, b in enumerate(self.blocks):
            item = getattr(b, "item", None)
            if item is None or item.value is None:
                continue
            v = item.value
            if isinstance(v, si.Line):
                pts = [replace(p, x=fn(p.x, p.y)[0], y=fn(p.x, p.y)[1]) for p in v.points]
                self.blocks[i] = replace(b, item=replace(item, value=replace(v, points=pts)))
            elif isinstance(v, si.GlyphRange):
                rects = []
                for r in v.rectangles:
                    xs, ys = zip(fn(r.x, r.y), fn(r.x + r.w, r.y + r.h))
                    rects.append(si.Rectangle(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
                self.blocks[i] = replace(b, item=replace(item, value=replace(v, rectangles=rects)))
