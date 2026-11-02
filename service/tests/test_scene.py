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
