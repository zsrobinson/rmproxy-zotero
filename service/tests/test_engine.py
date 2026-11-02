"""End-to-end: Zotero (in memory) <-> engine <-> rmfakecloud (in memory) <-> a simulated tablet."""

import json
import uuid
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

    def __init__(self, cloud, tmp, legacy=True):
        self.tree = CloudTree(cloud, BlobCache(tmp / "tablet"))
        self.legacy = legacy

    def sync(self):
        self.tree.root_hash = ""
        self.tree.mirror()

    def doc_named(self, name):
        self.sync()
        return next(d for d in self.tree.docs.values() if d.name == name)

    def open(self, name):
        """Open a document the way the tablet does the first time: it creates its own page list."""
        doc = self.doc_named(name)
        if tc.all_pages(doc.content):
            return doc
        n = doc.content.get("pageCount") or 1
        if self.legacy:  # like software 3.27.3 on a reMarkable 2: a plain page list, formatVersion 1
            ids = [str(uuid.uuid4()) for _ in range(n)]
            content = {**doc.content, "formatVersion": 1, "pages": ids, "redirectionPageMap": list(range(n))}
            content.pop("cPages", None)
        else:  # newer software: cPages
            content = {**doc.content, "cPages": tc.new_pdf_content(n)["cPages"]}
        self.tree.stage(doc.id, content=content)
        self.tree.commit()
        return self.doc_named(name)

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
        if tc.is_legacy(content):
            content["pages"].insert(index + 1, "note-page")
            content["redirectionPageMap"].insert(index + 1, -1)
            content["pageCount"] = len(content["pages"])
        else:
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


@pytest.fixture(params=["legacy-pages", "cpages"])
def world(tmp_path, request):
    z = MemoryZotero(7)
    files = MemoryFiles()
    cloud = MemoryCloud()
    clock = Clock()
    cfg = Config(data_dir=tmp_path / "data", zotero_user_id=7, zotero_api_key="", webdav_url="", webdav_username="",
                 webdav_password="", rmfakecloud_url="", quiet_seconds=120, defaults=Settings(ocr=False))
    state = State(tmp_path / "data" / "state.db")
    eng = Engine(cfg, state, z, files, cloud, clock=clock)
    return z, files, cloud, clock, eng, Tablet(cloud, tmp_path, legacy=request.param == "legacy-pages")


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
    # nothing is drawn until the tablet has opened the document and chosen its page ids
    assert tc.all_pages(doc.content) == []
    doc = tablet.open("Vaswani - 2017 - Attention")
    eng.sync()  # notices the first open
    clock.advance(500)
    assert not eng.sync().errors
    doc = tablet.doc_named("Vaswani - 2017 - Attention")
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
    doc = tablet.open("Vaswani - 2017 - Attention")
    eng.sync()
    clock.advance(500)
    eng.sync()
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


def test_first_sync_of_a_large_library_is_one_commit(world):
    import time

    z, files, cloud, clock, eng, tablet = world
    cols = [z.add_collection(f"Course {i}") for i in range(10)]
    for i in range(200):
        add_paper(z, files, [cols[i % 10]], title=f"Paper {i}")
    t = time.time()
    rep = eng.sync()
    assert not rep.errors and rep.uploaded == 200
    assert cloud.root_writes == 1  # folders and all documents land in one tablet commit
    assert time.time() - t < 30
    clock.advance()
    assert eng.sync().synced_docs == 0 and cloud.root_writes == 1


@pytest.mark.skipif(not __import__("shutil").which("pandoc"), reason="pandoc not installed")
def test_epub_becomes_a_tablet_edition_and_typography_change_respects_markup(world):
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from make_epub import make_epub

    z, files, cloud, clock, eng, tablet = world
    novels = z.add_collection("Novels")
    book = z.add_item({"itemType": "book", "title": "A Test Novel", "collections": [novels],
                       "creators": [{"creatorType": "author", "lastName": "Lovelace"}]})
    epub = z.add_item({"itemType": "attachment", "parentItem": book, "linkMode": "imported_file",
                       "contentType": "application/epub+zip", "title": "EPUB", "filename": "novel.epub"})
    files.put(epub, "novel.epub", make_epub())
    rep = eng.sync()
    assert not rep.errors, rep.errors
    editions = [o for o in z.items() if any(t["tag"] == "rmzotero:rendition" for t in o.get("tags", []))]
    assert len(editions) == 1 and editions[0]["parentItem"] == book and editions[0]["md5"]
    doc = tablet.doc_named("Lovelace - A Test Novel")
    assert tablet.folder_path(doc) == ["Zotero", "Novels"]
    assert edit.page_boxes(tablet.tree.read(doc.file(".pdf")))[0].width == pytest.approx(447.28, abs=0.5)

    # write in the book, then change the font: the marked-up edition is kept, a new one is made
    doc = tablet.open("Lovelace - A Test Novel")
    pid = tc.pages(doc.content)[1].id
    tablet.draw(doc, pid, [(0, 400), (50, 450)])
    eng.sync()
    clock.advance(500)
    eng.sync()
    eng.set_typography(epub, {"font": "Noto Sans"})
    clock.advance(500)
    rep = eng.sync()
    assert not rep.errors, rep.errors
    editions = [o for o in z.items() if any(t["tag"] == "rmzotero:rendition" for t in o.get("tags", [])) and not o.get("deleted")]
    assert len(editions) == 2
    tablet.sync()
    names = sorted(d.name for d in tablet.tree.docs.values() if not d.is_folder and not tablet.tree.is_trashed(d.id))
    assert len(names) == 2 and all(n.startswith("Lovelace - A Test Novel — ") for n in names)


def test_scanned_reading_gets_a_searchable_copy(tmp_path):
    import shutil
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_derive import OCRMYPDF, scanned

    if not (shutil.which("tesseract") and Path(OCRMYPDF).exists()):
        pytest.skip("ocrmypdf/tesseract not installed")
    from rmzotero.engine import Tools

    z, files, cloud, clock = MemoryZotero(7), MemoryFiles(), MemoryCloud(), Clock()
    cfg = Config(data_dir=tmp_path, zotero_user_id=7, zotero_api_key="", webdav_url="", webdav_username="", webdav_password="",
                 rmfakecloud_url="", defaults=Settings(ocr=True))
    eng = Engine(cfg, State(tmp_path / "s.db"), z, files, cloud, clock=clock, tools=Tools(ocrmypdf=OCRMYPDF))
    course = z.add_collection("PHIL 101")
    scan = scanned(text_pdf([[("Photocopied reading about virtue ethics", 18)]], size=(400, 300)))
    item, att = add_paper(z, files, [course], title="Reading 1", pdf=scan)
    rep = eng.sync()
    assert not rep.errors, rep.errors
    copies = [o for o in z.items() if any(t["tag"] == "rmzotero:searchable" for t in o.get("tags", []))]
    assert len(copies) == 1
    _fn, data = files.get(copies[0]["key"])
    assert "virtue" in "".join(c.text for c in TextLayer(data).chars(0)).lower()
    _fn, original = files.get(att)
    assert original == scan  # the original scan is untouched
    tablet = Tablet(cloud, tmp_path)
    doc = tablet.doc_named("Vaswani - 2017 - Reading 1")
    assert TextLayer(tablet.tree.read(doc.file(".pdf"))).has_text(0)  # the tablet got the searchable copy


def test_paused_service_reads_the_library_but_leaves_the_tablet_alone(world):
    z, files, cloud, clock, eng, tablet = world
    ml = z.add_collection("ML")
    add_paper(z, files, [ml])
    s = eng.settings
    s.paused = True
    save_settings(eng.state, s, s)
    rep = eng.sync()
    assert rep.paused and rep.uploaded == 0 and cloud.root_writes == 0
    assert ml in eng.lib.collections  # the plugin can show collections and sizes meanwhile
    s.paused = False
    save_settings(eng.state, s, s)
    assert eng.sync().uploaded == 1
