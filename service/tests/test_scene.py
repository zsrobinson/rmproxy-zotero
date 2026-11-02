from rmscene import scene_items as si

from rmzotero.tablet.scene import ZOTERO_LAYER, Page


def line(points, tool=si.Pen.BALLPOINT_2):
    return si.Line(
        color=si.PenColor.BLACK,
        tool=tool,
        points=[si.Point(x, y, 0, 0, 8, 100) for x, y in points],
        thickness_scale=2.0,
        starting_length=0.0,
    )


def test_empty_page_roundtrip_and_zotero_layer():
    page = Page.empty()
    user = page.first_user_layer_id()
    [uid] = page.add(user, [line([(0, 0), (10, 10)])])
    z = page.zotero_layer_id()
    zids = page.add(z, [line([(5, 5), (6, 6)]), si.GlyphRange(None, 5, "hello", si.PenColor.HIGHLIGHT, [si.Rectangle(0, 0, 50, 10)])])
    data = page.to_bytes()
    again = Page.parse(data)
    marks = again.marks()
    assert [(m.layer, m.is_stroke) for m in marks] == [("Layer 1", True), (ZOTERO_LAYER, True), (ZOTERO_LAYER, False)]
    assert marks[0].id == uid and [m.id for m in marks[1:]] == zids
    # the Zotero layer is reused, not duplicated
    assert again.zotero_layer_id() == z
    assert [label for _, label in again.layers()] == ["Layer 1", ZOTERO_LAYER]


def test_delete_makes_tombstone_and_preserves_others():
    page = Page.empty()
    layer = page.first_user_layer_id()
    a, b, c = page.add(layer, [line([(0, 0), (1, 1)]), line([(2, 2), (3, 3)]), line([(4, 4), (5, 5)])])
    page.delete({b})
    again = Page.parse(page.to_bytes())
    assert [m.id for m in again.marks()] == [a, c]
    # adding after a deletion keeps order and unique ids
    [d] = again.add(layer, [line([(6, 6), (7, 7)])])
    assert [m.id for m in Page.parse(again.to_bytes()).marks()] == [a, c, d]


def test_transform_all():
    page = Page.empty()
    page.add(page.first_user_layer_id(), [line([(1, 2)])])
    page.transform_all(lambda x, y: (-y, x))
    [m] = Page.parse(page.to_bytes()).marks()
    assert (m.value.points[0].x, m.value.points[0].y) == (-2, 1)



def test_service_writes_as_the_pages_device_author_above_all_its_ids():
    from rmzotero.tablet.scene import SERVICE_AUTHOR

    page = Page.empty()
    [a] = page.add(page.first_user_layer_id(), [line([(0, 0), (1, 1)])])
    device = min(next(b for b in page.blocks if type(b).__name__ == "AuthorIdsBlock").author_uuids)
    z = page.zotero_layer_id()
    [b] = page.add(z, [line([(2, 2), (3, 3)])])
    authors = next(bl for bl in page.blocks if type(bl).__name__ == "AuthorIdsBlock").author_uuids
    assert SERVICE_AUTHOR not in authors.values() and len(authors) == 1
    assert a.split(":")[0] == b.split(":")[0] == str(device)
    # every new id is above every id or timestamp already in the page (labels' timestamps included)
    from rmzotero.tablet.scene import _crdt_ids

    again = Page.parse(page.to_bytes())
    ids = [c for c in _crdt_ids(again.blocks) if c.part1 == device]
    assert len(set((c.part1, c.part2) for c in ids if c.part2 > 0)) > 3


def test_legacy_page_lists():
    from rmzotero.tablet import content as tc

    legacy = {"formatVersion": 1, "pages": ["a", "b", "note"], "redirectionPageMap": [0, 1, -1], "pageCount": 3}
    assert tc.is_legacy(legacy)
    refs = tc.pages(legacy)
    assert [(r.id, r.redir) for r in refs] == [("a", 0), ("b", 1), ("note", None)]
    gone = tc.set_deleted(legacy, "b", True)
    assert gone["pages"] == ["a", "note"] and gone["redirectionPageMap"] == [0, -1] and gone["pageCount"] == 2
    back = tc.set_deleted(gone, "b", False, index=1, redir=1)
    assert back["pages"] == legacy["pages"] and back["redirectionPageMap"] == legacy["redirectionPageMap"]
