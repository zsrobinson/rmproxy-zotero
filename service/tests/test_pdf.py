import pytest

from rmzotero.pdf import edit
from rmzotero.pdf.geometry import S, PageBox, rotate_rm_point
from rmzotero.pdf.simple import text_pdf
from rmzotero.pdf.text import TextLayer, line_rects, text_of


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_display_user_roundtrip(rot):
    box = PageBox(10, 20, 612, 792, rot)
    for ux, uy in [(10, 20), (300, 400), (622, 812)]:
        dx, dy = box.user_to_display(ux, uy)
        assert box.display_to_user(dx, dy) == pytest.approx((ux, uy))
    x, y = box.user_to_rm(100, 200)
    assert box.rm_to_user(x, y) == pytest.approx((100, 200))


def test_rm_origin_is_top_centre():
    box = PageBox(0, 0, 600, 800, 0)
    assert box.rm_to_user(0, 0) == pytest.approx((300, 800))
    assert box.rm_to_user(100, 100) == pytest.approx((300 + 100 * S, 800 - 100 * S))
    # rotated 90: the displayed top-left is the user-space origin
    r = PageBox(0, 0, 600, 800, 90)
    assert r.rm_to_user(-r.display_size[0] / 2 / S, 0) == pytest.approx((0, 0))


def test_rotate_point_keeps_content_spot():
    box = PageBox(0, 0, 600, 800, 0)
    x, y = box.user_to_rm(100, 700)
    nx, ny = rotate_rm_point(x, y, box, 90)
    assert PageBox(0, 0, 600, 800, 90).rm_to_user(nx, ny) == pytest.approx((100, 700))


def test_text_layer_and_edits():
    pdf = text_pdf([[("Hello world", 12)], [("Second page", 12)]], size=(300, 400))
    tl = TextLayer(pdf)
    assert tl.has_text(0) and tl.has_text(1)
    chars = tl.chars_in_rects(0, [[0, 0, 300, 400]])
    assert text_of(chars) == "Hello world"
    assert len(line_rects(chars)) == 1

    more = edit.insert_blank_pages(pdf, [(1, (100, 200))])
    assert edit.page_count(more) == 3
    assert edit.page_boxes(more)[1].width == 100
    assert TextLayer(more).pages_without_text() == [1]
    fps = edit.page_fingerprints(more)
    rotated = edit.set_rotation(more, {2: 90})
    assert edit.rotations(rotated) == [0, 0, 90]
    assert edit.page_fingerprints(rotated) == fps  # rotation doesn't change identity
    fewer = edit.delete_pages(rotated, {1})
    assert edit.page_fingerprints(fewer) == [fps[0], fps[2]]
    back = edit.copy_page_into(more, 1, fewer, 1)
    assert edit.page_fingerprints(back)[1] == fps[1]


def test_page_text_keeps_line_breaks():
    pdf = text_pdf([[("First line of a paragraph that wraps onto a second line somewhere along here.", 12), ("Next paragraph.", 12)]], size=(200, 400))
    t = TextLayer(pdf).page_text(0)
    assert "\n" in t and "here.\nNext" in t.replace("\r", "")
