import pytest

from rmzotero.pdf import edit
from rmzotero.pdf.simple import text_pdf
from rmzotero.structure import StructureError, reconcile_structure
from rmzotero.tablet.content import new_pdf_content, pages, all_pages, set_deleted

PDF = text_pdf([[(f"Page {i}", 12)] for i in range(4)], size=(300, 400))


def content_with_note_after(content, after_index):
    import copy

    c = copy.deepcopy(content)
    ps = c["cPages"]["pages"]
    a, b = ps[after_index]["idx"]["value"], ps[after_index + 1]["idx"]["value"]
    ps.append({"id": "note-1", "idx": {"timestamp": "1:9", "value": a + "m"}})  # sorts between a and b
    assert a < a + "m" < b
    return c


def first(content):
    return reconcile_structure(tablet_pages=pages(content), tablet_all=all_pages(content), last_mapping=None, old_zotero=None,
                               new_zotero=PDF, tablet_dirty_pages=set(), zotero_dirty_zidx=set())


def test_first_sync_identity():
    c = new_pdf_content(4)
    r = first(c)
    assert r.mapping == [p.id for p in pages(c)] and r.final_pdf is None


def test_tablet_note_page_inserted_into_zotero():
    c = new_pdf_content(4)
    mapping = first(c).mapping
    c2 = content_with_note_after(c, 1)
    r = reconcile_structure(tablet_pages=pages(c2), tablet_all=all_pages(c2), last_mapping=mapping, old_zotero=PDF,
                            new_zotero=PDF, tablet_dirty_pages={"note-1"}, zotero_dirty_zidx=set())
    assert r.mapping[2] == "note-1" and len(r.mapping) == 5
    assert edit.page_count(r.final_pdf) == 5
    fps = edit.page_fingerprints(r.final_pdf)
    assert fps[:2] == edit.page_fingerprints(PDF)[:2] and fps[3:] == edit.page_fingerprints(PDF)[2:]


def test_zotero_delete_and_rotate_reach_tablet():
    c = new_pdf_content(4)
    mapping = first(c).mapping
    z = edit.set_rotation(edit.delete_pages(PDF, {1}), {2: 90})  # old page 3 is now index 2
    r = reconcile_structure(tablet_pages=pages(c), tablet_all=all_pages(c), last_mapping=mapping, old_zotero=PDF,
                            new_zotero=z, tablet_dirty_pages=set(), zotero_dirty_zidx=set())
    assert r.tablet_delete == {mapping[1]}
    assert r.tablet_rotations == {mapping[3]: 90}
    assert r.current_zidx_to_page == {0: mapping[0], 1: mapping[2], 2: mapping[3]}
    # once the tablet applies the delete, the mapping matches the Zotero file
    c2 = set_deleted(c, mapping[1], True)
    assert r.mapping == [p.id for p in pages(c2)] and r.final_pdf is None
    assert r.deleted_pages == {mapping[1]}


def test_conflicts_keep_pages():
    c = new_pdf_content(4)
    mapping = first(c).mapping
    # Zotero deletes page 1, but the tablet wrote on it: it comes back into Zotero
    z = edit.delete_pages(PDF, {1})
    r = reconcile_structure(tablet_pages=pages(c), tablet_all=all_pages(c), last_mapping=mapping, old_zotero=PDF,
                            new_zotero=z, tablet_dirty_pages={mapping[1]}, zotero_dirty_zidx=set())
    assert not r.tablet_delete and r.warnings
    assert edit.page_fingerprints(r.final_pdf) == edit.page_fingerprints(PDF)
    # the tablet deletes page 2, but Zotero annotated it: the tablet gets it back
    c3 = set_deleted(c, mapping[2], True)
    r = reconcile_structure(tablet_pages=pages(c3), tablet_all=all_pages(c3), last_mapping=mapping, old_zotero=PDF,
                            new_zotero=PDF, tablet_dirty_pages=set(), zotero_dirty_zidx={2})
    assert r.tablet_undelete == {mapping[2]} and r.final_pdf is None and r.mapping == mapping
    # without the conflict, the tablet's delete removes the Zotero page
    r = reconcile_structure(tablet_pages=pages(c3), tablet_all=all_pages(c3), last_mapping=mapping, old_zotero=PDF,
                            new_zotero=PDF, tablet_dirty_pages=set(), zotero_dirty_zidx=set())
    assert edit.page_count(r.final_pdf) == 3 and r.deleted_pages == {mapping[2]}


def test_replaced_file_is_refused():
    c = new_pdf_content(4)
    mapping = first(c).mapping
    other = text_pdf([[("Different", 12)]] * 5, size=(300, 400))
    with pytest.raises(StructureError):
        reconcile_structure(tablet_pages=pages(c), tablet_all=all_pages(c), last_mapping=mapping, old_zotero=PDF,
                            new_zotero=other, tablet_dirty_pages=set(), zotero_dirty_zidx=set())
