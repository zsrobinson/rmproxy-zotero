import itertools
import json

from rmscene import scene_items as si

from rmzotero.annotations import convert as cv
from rmzotero.annotations.reconcile import PageCtx, reconcile
from rmzotero.pdf import edit
from rmzotero.pdf.simple import text_pdf
from rmzotero.pdf.text import TextLayer
from rmzotero.tablet.scene import Page

PDF = text_pdf([[("The quick brown fox jumps over the lazy dog", 14)]], size=(447.3, 596.4))
BOX = edit.page_boxes(PDF)[0]
TEXT = TextLayer(PDF)
keys = (f"K{i:07d}" for i in itertools.count())


def stroke(points, tool=si.Pen.BALLPOINT_2, color=si.PenColor.BLACK):
    return si.Line(color=color, tool=tool, points=[si.Point(x, y, 0, 0, 8, 100) for x, y in points], thickness_scale=1.0, starting_length=0.0)


def ctx_for(page):
    return {"p1": PageCtx(page_id="p1", zidx=0, box=BOX, label="1", page=page, modified_ms=1)}


def apply_zotero(zanns, res, version):
    for d in res.creates:
        zanns[d["key"]] = {**d, "version": version, "dateModified": "2026-01-01T00:00:00Z"}
        res.links[d["key"]].zversion = version
    for d in res.updates:
        zanns[d["key"]] = {**d, "version": version}
        res.links[d["key"]].zversion = version
    for k, _v in res.deletes:
        zanns.pop(k, None)
        res.links.pop(k, None)


def run(pages, zanns, links, version):
    res = reconcile(pages, zanns, links, TEXT, lambda: next(keys))
    apply_zotero(zanns, res, version)
    for c in pages.values():
        c.page = Page.parse(c.page.to_bytes())
        c.invalidate()
        c.dirty = False
    return res


def text_y_rm():
    chars = TEXT.chars(0)
    c = chars[4]  # "q"
    return BOX.user_to_rm(c.cx, c.cy), BOX.user_to_rm(chars[8].cx, chars[8].cy)


def test_tablet_marks_become_grouped_annotations_and_stay_stable():
    page = Page.empty()
    layer = page.first_user_layer_id()
    (qx, qy), (bx, by) = text_y_rm()
    page.add(layer, [
        stroke([(-300, 900), (-250, 950)]),
        stroke([(-240, 955), (-200, 990)]),  # close and consecutive -> same ink annotation
        stroke([(qx, qy), (bx, by)], tool=si.Pen.HIGHLIGHTER_2, color=si.PenColor.YELLOW),
        stroke([(300, 1700), (320, 1720)]),  # far away -> its own ink annotation
    ])
    pages, zanns, links = ctx_for(page), {}, {}
    res = run(pages, zanns, links, 1)
    kinds = sorted(d["annotationType"] for d in res.creates)
    assert kinds == ["highlight", "ink", "ink"]
    hl = next(d for d in res.creates if d["annotationType"] == "highlight")
    assert "quick" in hl["annotationText"]
    ink = [d for d in res.creates if d["annotationType"] == "ink"]
    assert sorted(len(json.loads(d["annotationPosition"])["paths"]) for d in ink) == [1, 2]
    links = res.links
    again = run(pages, zanns, links, 2)
    assert not again.creates and not again.updates and not again.deletes


def test_zotero_annotation_drawn_on_zotero_layer_then_deleted_on_tablet():
    page = Page.empty()
    pages = ctx_for(page)
    chars = TEXT.chars(0)[:3]
    rect = [min(c.left for c in chars), min(c.bottom for c in chars), max(c.right for c in chars), max(c.top for c in chars)]
    zanns = {"ZHL00001": {"key": "ZHL00001", "version": 5, "annotationType": "highlight", "annotationText": "The", "annotationColor": "#2ea8e5",
                          "annotationPosition": json.dumps({"pageIndex": 0, "rects": [rect]}), "dateModified": "2026-01-01T00:00:00Z"},
             "ZNOTE001": {"key": "ZNOTE001", "version": 5, "annotationType": "note", "annotationComment": "hi",
                          "annotationPosition": json.dumps({"pageIndex": 0, "rects": [[0, 0, 10, 10]]})}}
    res = run(pages, zanns, {}, 6)
    assert not res.creates
    marks = pages["p1"].page.marks()
    assert len(marks) == 1 and marks[0].on_zotero_layer and marks[0].is_highlight
    assert res.links["ZNOTE001"].unsynced
    links = res.links
    assert not run(pages, zanns, links, 7).deletes
    # user erases it on the tablet
    pages["p1"].page.delete({marks[0].id})
    res = run(pages, zanns, links, 8)
    assert res.deletes == [("ZHL00001", 5)]


def test_zotero_edits_reach_users_own_strokes():
    page = Page.empty()
    layer = page.first_user_layer_id()
    page.add(layer, [stroke([(-300 + i * 10, 900) for i in range(20)])])
    pages, zanns = ctx_for(page), {}
    res = run(pages, zanns, {}, 1)
    links = res.links
    [key] = list(zanns)
    # recolour in Zotero
    zanns[key] = {**zanns[key], "annotationColor": "#ff6666", "version": 2, "dateModified": "2026-02-01T00:00:00Z"}
    res = run(pages, zanns, links, 3)
    links = res.links
    [m] = pages["p1"].page.marks()
    assert m.value.color_rgba[:3] == (255, 102, 102) and m.value.tool == si.Pen.BALLPOINT_2 and not m.on_zotero_layer
    # erase the middle of the stroke in Zotero: the original splits in two, keeping its pen
    pos = json.loads(zanns[key]["annotationPosition"])
    path = pos["paths"][0]
    pos["paths"] = [path[:12], path[-12:]]
    zanns[key] = {**zanns[key], "annotationPosition": json.dumps(pos), "version": 4, "dateModified": "2026-02-02T00:00:00Z"}
    res = run(pages, zanns, links, 5)
    marks = pages["p1"].page.marks()
    assert [len(m.value.points) for m in marks] == [6, 6]
    assert all(m.value.tool == si.Pen.BALLPOINT_2 for m in marks)
    links = res.links
    assert not run(pages, zanns, links, 6).updates
    # delete it in Zotero: strokes go away
    del zanns[key]
    run(pages, zanns, links, 7)
    assert pages["p1"].page.marks() == []


def test_continuing_a_scribble_updates_the_same_annotation():
    page = Page.empty()
    layer = page.first_user_layer_id()
    page.add(layer, [stroke([(0, 500), (20, 520)])])
    pages, zanns = ctx_for(page), {}
    links = run(pages, zanns, {}, 1).links
    pages["p1"].page.add(layer, [stroke([(25, 525), (40, 540)])])
    res = run(pages, zanns, links, 2)
    assert not res.creates and len(res.updates) == 1
    assert len(json.loads(res.updates[0]["annotationPosition"])["paths"]) == 2
