"""End-to-end: Zotero (in memory) <-> engine <-> rmfakecloud (in memory) <-> a simulated tablet."""

import json
from pathlib import Path

import pytest
from rmscene import scene_items as si

from rmzotero.config import Config, Settings, save_settings
from rmzotero.engine import Engine
from rmzotero.pdf import edit
from rmzotero.pdf.simple import text_pdf
from rmzotero.pdf.text import TextLayer
from rmzotero.state import State
from rmzotero.tablet import content as tc
from rmzotero.tablet.cloud import MemoryCloud
from rmzotero.tablet.scene import Page
from rmzotero.tablet.tree import TRASH, BlobCache, CloudTree
from rmzotero.zotero.client import MemoryZotero
from rmzotero.zotero.webdav import MemoryFiles

PAPER = text_pdf([[("Attention is all you need. " * 4, 12)], [("Second page of the paper", 12)], [("Third page", 12)]], size=(447.3, 596.4))


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def advance(self, s=1000):
        self.t += s


class Tablet:
    """Edits documents the way the tablet would: new page files, cPages changes, metadata moves."""

    def __init__(self, cloud, tmp):
        self.tree = CloudTree(cloud, BlobCache(tmp / "tablet"))

    def sync(self):
        self.tree.root_hash = ""
        self.tree.mirror()

    def doc_named(self, name):
        self.sync()
        return next(d for d in self.tree.docs.values() if d.name == name)

    def folder_path(self, doc):
        return self.tree.path_of(doc.id)

    def page(self, doc, pid):
        e = doc.page_entries().get(pid)
        return Page.parse(self.tree.read(e)) if e else Page.empty()

    def draw(self, doc, pid, points, tool=si.Pen.BALLPOINT_2):
        page = self.page(doc, pid)
        page.add(page.first_user_layer_id(), [si.Line(si.PenColor.BLACK, tool, [si.Point(x, y, 0, 0, 8, 100) for x, y in points], 1.0, 0.0)])
        self.tree.stage(doc.id, files={f"/{pid}.rm": page.to_bytes()})
        self.tree.commit()

    def insert_note_page_after(self, doc, index, points):
        content = json.loads(json.dumps(doc.content))
        ps = sorted(content["cPages"]["pages"], key=lambda p: p["idx"]["value"])
        new_idx = ps[index]["idx"]["value"] + "m"
        content["cPages"]["pages"].append({"id": "note-page", "idx": {"timestamp": "1:5", "value": new_idx}})
        page = Page.empty()
        page.add(page.first_user_layer_id(), [si.Line(si.PenColor.BLACK, si.Pen.FINELINER_2, [si.Point(x, y, 0, 0, 8, 100) for x, y in points], 1.0, 0.0)])
        self.tree.stage(doc.id, content=content, files={"/note-page.rm": page.to_bytes()})
        self.tree.commit()

    def trash(self, doc):
        self.tree.stage(doc.id, metadata={**doc.metadata, "parent": TRASH})
        self.tree.commit()


@pytest.fixture
def world(tmp_path):
    z = MemoryZotero(7)
    files = MemoryFiles()
    cloud = MemoryCloud()
    clock = Clock()
    cfg = Config(data_dir=tmp_path / "data", zotero_user_id=7, zotero_api_key="", webdav_url="", webdav_username="",
                 webdav_password="", rmfakecloud_url="", quiet_seconds=120, defaults=Settings(ocr=False))
    state = State(tmp_path / "data" / "state.db")
    eng = Engine(cfg, state, z, files, cloud, clock=clock)
    return z, files, cloud, clock, eng, Tablet(cloud, tmp_path)


def add_paper(z, files, collections, title="Attention", pdf=PAPER):
    item = z.add_item({"itemType": "journalArticle", "title": title, "date": "2017", "collections": collections,
                       "creators": [{"creatorType": "author", "lastName": "Vaswani"}]})
    att = z.add_item({"itemType": "attachment", "parentItem": item, "linkMode": "imported_file", "contentType": "application/pdf",
                      "title": "Full Text PDF", "filename": "paper.pdf"})
    md5, mtime = files.put(att, "paper.pdf", pdf)
    z.update(att, md5=md5, mtime=mtime)
    return item, att


def highlight_of_first_word(att, version_text=True):
    tl = TextLayer(PAPER)
    cs = tl.chars(0)[:9]  # "Attention"
    rect = [min(c.left for c in cs), min(c.bottom for c in cs), max(c.right for c in cs), max(c.top for c in cs)]
    return {"itemType": "annotation", "parentItem": att, "annotationType": "highlight", "annotationText": "Attention",
            "annotationComment": "key idea", "annotationColor": "#5fb236", "annotationPageLabel": "1",
            "annotationSortIndex": "00000|000000|00010", "annotationPosition": json.dumps({"pageIndex": 0, "rects": [rect]})}


def test_full_round_trip(world):
    z, files, cloud, clock, eng, tablet = world
    ml = z.add_collection("ML")
    tr = z.add_collection("Transformers", parent=ml)
    zoo = z.add_collection("Zoology")
    item, att = add_paper(z, files, [tr, zoo])
    hl = z.add_item(highlight_of_first_word(att))

    rep = eng.sync()
    assert not rep.errors, rep.errors
    assert rep.uploaded == 1

    doc = tablet.doc_named("Vaswani - 2017 - Attention")
    assert tablet.folder_path(doc) == ["Zotero", "ML", "Transformers"]
    pointer = tablet.doc_named("→ Vaswani - 2017 - Attention")
    assert tablet.folder_path(pointer) == ["Zotero", "Zoology"]
    pages = tc.pages(doc.content)
    assert len(pages) == 3
    marks = tablet.page(doc, pages[0].id).marks()
    assert len(marks) == 1 and marks[0].on_zotero_layer and marks[0].is_highlight
    gen = cloud.generation

    # nothing changed: nothing written
    clock.advance()
    assert not eng.sync().errors
    assert cloud.generation == gen

    # the user writes on page 2 at the tablet
    tablet.draw(doc, pages[1].id, [(-100, 300), (-50, 320), (0, 300)])
    clock.advance(10)
    rep = eng.sync()
    assert rep.pending == 1 and rep.annotations_to_zotero == 0  # waits for the quiet period
    clock.advance(500)
    rep = eng.sync()
    assert rep.annotations_to_zotero == 1, rep.errors
    inks = [o for o in z.items() if o.get("annotationType") == "ink"]
    assert len(inks) == 1 and json.loads(inks[0]["annotationPosition"])["pageIndex"] == 1

    # recolour the ink in Zotero: the user's stroke changes colour on the tablet, keeping its pen
    z.update(inks[0]["key"], annotationColor="#ff6666")
    clock.advance(500)
    eng.sync()
    clock.advance(500)
    eng.sync()
    doc = tablet.doc_named("Vaswani - 2017 - Attention")
    [stroke] = [m for m in tablet.page(doc, pages[1].id).marks()]
    assert stroke.value.color_rgba[:3] == (255, 102, 102) and stroke.value.tool == si.Pen.BALLPOINT_2 and not stroke.on_zotero_layer

    # a note page inserted after page 1 on the tablet becomes a page in the Zotero PDF
    tablet.insert_note_page_after(doc, 0, [(0, 500), (100, 600)])
    eng.sync()  # change noticed; waits for quiet
    clock.advance(500)
    rep = eng.sync()
    assert not rep.errors, rep.errors
    _fn, zpdf = files.get(att)
    assert edit.page_count(zpdf) == 4
    by_page = sorted(json.loads(o["annotationPosition"])["pageIndex"] for o in z.items() if o.get("itemType") == "annotation")
    assert by_page == [0, 1, 2]  # highlight p0, note-page ink p1, the earlier ink shifted to p2

    # delete the first page in Zotero: the tablet page goes too
    z.update(att, md5="changed")
    files.put(att, "paper.pdf", edit.delete_pages(zpdf, {0}))
    z.remove(hl)  # Zotero removes annotations on a deleted page itself
    eng.sync()
    clock.advance(500)
    rep = eng.sync()
    assert not rep.errors, rep.errors
    doc = tablet.doc_named("Vaswani - 2017 - Attention")
    assert pages[0].id not in [p.id for p in tc.pages(doc.content)]

    # remove on the tablet -> "removed on tablet", Zotero untouched; restore brings it back with its markup
    tablet.trash(doc)
    clock.advance(500)
    eng.sync()
    assert eng.state.doc(att)["state"] == "removed"
    assert any(o["key"] == item for o in z.items())
    eng.request_restore(att)
    eng.sync()
    doc = tablet.doc_named("Vaswani - 2017 - Attention")
    assert tablet.folder_path(doc) == ["Zotero", "ML", "Transformers"]
    assert tablet.page(doc, pages[1].id).marks()

    # exclude the collection -> trashed on the tablet; the pointer goes away too
    s = eng.settings
    s.excluded_collections = [ml, zoo]
    save_settings(eng.state, s, s)
    clock.advance(500)
    rep = eng.sync()
    assert rep.trashed == 1
    doc = tablet.doc_named("Vaswani - 2017 - Attention")
    assert tablet.tree.is_trashed(doc.id)
    tablet.sync()
    assert all(d.name != "→ Vaswani - 2017 - Attention" for d in tablet.tree.docs.values())


def test_conflicting_tablet_commit_is_retried(world):
    z, files, cloud, clock, eng, tablet = world
    item, att = add_paper(z, files, [])
    eng.sync()
    doc = tablet.doc_named("Vaswani - 2017 - Attention")
    pid = tc.pages(doc.content)[0].id
    # Zotero adds an annotation while the tablet writes on the same page; the service only sees the old tablet state
    z.add_item(highlight_of_first_word(att))
    clock.advance(500)
    eng.lib.refresh(z)
    eng.tree.mirror()
    tablet.draw(doc, pid, [(0, 900), (10, 910)])
    eng.sync()  # tablet changed during the cycle -> commit conflict is detected via the generation
    clock.advance(500)
    eng.sync()
    clock.advance(500)
    eng.sync()
    doc = tablet.doc_named("Vaswani - 2017 - Attention")
    marks = tablet.page(doc, pid).marks()
    assert sum(1 for m in marks if not m.on_zotero_layer) == 1  # the user's stroke survived
    assert sum(1 for m in marks if m.on_zotero_layer) == 1  # and the Zotero highlight arrived
    kinds = sorted(o.get("annotationType") for o in z.items() if o.get("itemType") == "annotation")
    assert kinds == ["highlight", "ink"]
