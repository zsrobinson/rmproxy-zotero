from rmzotero.config import Settings
from rmzotero.planner import UNFILED, plan
from rmzotero.state import State
from rmzotero.zotero.client import MemoryZotero
from rmzotero.zotero.library import Library


def pdf(z, item, title="Full Text PDF"):
    return z.add_item({"itemType": "attachment", "parentItem": item, "linkMode": "imported_file", "contentType": "application/pdf", "title": title, "filename": f"{title}.pdf"})


def setup():
    z = MemoryZotero()
    ml = z.add_collection("ML")
    tr = z.add_collection("Transformers", parent=ml)
    cls = z.add_collection("Class")
    old = z.add_collection("Old semester", parent=cls)
    return z, ml, tr, cls, old


def build(z, settings=None):
    state = State(":memory:")
    lib = Library(state)
    lib.refresh(z)
    return lib, state, plan(lib, settings or Settings(ocr=False), state)


def test_home_prefers_deeper_then_alphabetical_and_pointer_modes():
    z, ml, tr, cls, old = setup()
    cls = z.add_collection("Zoology")  # sorts after ML/Transformers
    item = z.add_item({"itemType": "journalArticle", "title": "Attention", "date": "2017", "collections": [ml, tr, cls],
                       "creators": [{"creatorType": "author", "lastName": "Vaswani"}]})
    att = pdf(z, item)
    lib, state, p = build(z)
    d = p.docs[att]
    assert d.folder == tr and d.location == "Zotero/ML/Transformers" and d.name == "Vaswani - 2017 - Attention"
    # unrelated mode: no pointer in the parent ML, one in Class
    assert [(x.folder, x.location) for x in p.pointers] == [(cls, "Zotero/ML/Transformers")]
    assert p.folders[tr] == ("Transformers", ml) and p.folders[ml] == ("ML", "__root__:Zotero")
    p_all = plan(lib, Settings(ocr=False, pointers="all"), state)
    assert sorted(x.folder for x in p_all.pointers) == sorted([ml, cls])
    assert plan(lib, Settings(ocr=False, pointers="none"), state).pointers == []
    state.exec("INSERT INTO home_overrides VALUES (?, ?)", (item, cls))
    assert plan(lib, Settings(ocr=False), state).docs[att].folder == cls


def test_exclusions_unfiled_trash_and_multiple_attachments():
    z, ml, tr, cls, old = setup()
    a = z.add_item({"itemType": "book", "title": "Only old", "collections": [old]})
    pdf(z, a)
    b = z.add_item({"itemType": "book", "title": "Both", "collections": [old, ml]})
    b_att = pdf(z, b)
    c = z.add_item({"itemType": "book", "title": "Loose"})
    c1, c2 = pdf(z, c, "Preprint PDF"), pdf(z, c, "Published PDF")
    d = z.add_item({"itemType": "book", "title": "Trashed", "deleted": 1})
    pdf(z, d)
    lib, state, p = build(z, Settings(ocr=False, excluded_collections=[cls], sync_root=""))
    assert set(p.docs) == {b_att, c1, c2}
    assert p.docs[b_att].folder == ml and p.docs[b_att].location == "ML"
    assert p.docs[c1].folder == UNFILED and p.docs[c1].name.endswith("— Preprint PDF")
    assert p.folders[UNFILED] == ("Unfiled Items", "")
    assert all(x.folder != old for x in p.pointers)


def test_derivations_pending_then_used():
    z, ml, *_ = setup()
    item = z.add_item({"itemType": "book", "title": "Novel", "collections": [ml]})
    epub = z.add_item({"itemType": "attachment", "parentItem": item, "linkMode": "imported_file", "contentType": "application/epub+zip", "title": "EPUB"})
    scan = pdf(z, item, "Scan")
    lib, state, p = build(z, Settings())
    assert {(d.source_key, d.kind) for d in p.pending} == {(scan, "ocr-check")}  # EPUB editions are made on request
    assert epub not in p.docs
    assert p.docs == {}
    rendition = z.add_item({"itemType": "attachment", "parentItem": item, "linkMode": "imported_file", "contentType": "application/pdf", "title": "EPUB (tablet edition)"})
    lib.refresh(z)
    state.exec("INSERT INTO derived VALUES (?, 'rendition', ?, '{}')", (epub, rendition))
    state.exec("INSERT INTO derived VALUES (?, 'searchable', NULL, '{}')", (scan,))
    p = plan(lib, Settings(), state)
    assert {k: v.kind for k, v in p.docs.items()} == {rendition: "rendition", scan: "pdf"}
    assert p.docs[rendition].source_key == epub
