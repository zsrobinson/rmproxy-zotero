"""The Claude connector: library operations, add_items resolution, page images, and the MCP protocol end to end."""

import asyncio
import io
import json

import httpx
import pytest
from PIL import Image as PILImage
from rmscene import scene_items as si

from rmzotero.claude.ops import LibraryOps, NotFound, parse_pages
from rmzotero.claude.render import rm_pixel
from rmzotero.claude.resolve import Resolver, isbn_digits, normalise_doi
from rmzotero.pdf import edit
from rmzotero.pdf.simple import text_pdf
from rmzotero.tablet import content as tc

from test_engine import PAPER, Clock, Tablet, add_paper, highlight_of_first_word, world  # noqa: F401  (fixture)

OA_PDF = text_pdf([[("An open access paper", 14)]], size=(400, 500))
TRANSLATED = {
    "itemType": "journalArticle", "title": "Deep Residual Learning", "date": "2016", "DOI": "10.1109/CVPR.2016.90",
    "creators": [{"creatorType": "author", "firstName": "Kaiming", "lastName": "He"}], "tags": ["vision"],
    "attachments": [{"title": "Snapshot", "mimeType": "text/html", "url": "https://example.org/page"}], "key": "XYZ", "version": 0,
}


def resolver(email="me@example.org"):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/search":
            q = req.content.decode()
            if "10.1109" in q:
                return httpx.Response(200, json=[TRANSLATED])
            if "attention" in q.lower():
                return httpx.Response(200, json=[{"itemType": "journalArticle", "title": "Attention", "DOI": "10.5555/attention", "creators": []}])
            if q == "ambiguous words":
                return httpx.Response(300, json={"a1": "First match", "a2": "Second match"})
            return httpx.Response(501)
        if req.url.host == "api.unpaywall.org":
            assert req.url.params["email"] == email
            return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "https://oa.example.org/paper.pdf"}})
        if req.url.host == "oa.example.org":
            return httpx.Response(200, content=OA_PDF, headers={"content-type": "application/pdf"})
        raise AssertionError(req.url)

    return Resolver("http://translation-server:1969", email, client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def lib_world(world):  # noqa: F811
    z, files, cloud, clock, eng, tablet = world
    ml = z.add_collection("ML")
    item, att = add_paper(z, files, [ml])
    z.add_item(highlight_of_first_word(att))
    eng.sync()
    return z, files, eng, tablet, clock, LibraryOps(eng, resolver()), ml, item, att


def test_helpers():
    assert parse_pages("2,4-5", 10, 4) == [1, 3, 4]
    assert parse_pages(None, 3, 20) == [0, 1, 2]
    assert normalise_doi("https://doi.org/10.1000/ABC.1.") == "10.1000/abc.1"
    assert isbn_digits("0-306-40615-2") == isbn_digits("978-0-306-40615-7")


def test_reading(lib_world):
    z, files, eng, tablet, clock, ops, ml, item, att = lib_world
    found = ops.search_library("vaswani attention")
    assert found["items"][0]["key"] == item and found["items"][0]["collections"] == ["ML"]
    assert ops.search_library("nothing-like-this")["count"] == 0
    detail = ops.get_item(att)  # an attachment key resolves to its item
    assert detail["key"] == item and detail["attachments"][0]["on_tablet"] and detail["attachments"][0]["tablet_location"] == "Zotero/ML"
    assert ops.list_collections()["collections"][0] == {"key": ml, "path": "ML", "items": 1, "parent": None}
    anns = ops.get_annotations(ml)["items"][0]["annotations"]
    assert anns[0]["text"] == "Attention" and anns[0]["color"] == "green" and anns[0]["comment"] == "key idea"
    pages = ops.read_pages(item, "2")
    assert pages["page_count"] == 3 and pages["pages"][0]["text"].startswith("Second page")
    with pytest.raises(NotFound):
        ops.get_item("NOPE1234")


def test_view_pages_draws_tablet_handwriting(lib_world):
    z, files, eng, tablet, clock, ops, ml, item, att = lib_world
    doc = tablet.open("Vaswani - 2017 - Attention")  # the tablet makes its page list on first open
    pid = tc.pages(doc.content)[1].id
    tablet.draw(doc, pid, [(-200, 900), (200, 900)])
    eng.sync()
    clock.advance(500)
    eng.sync()
    [(label, png)] = ops.view_pages(item, "2")
    img = PILImage.open(io.BytesIO(png)).convert("RGB")
    box = edit.page_boxes(PAPER)[1]
    assert label == "2" and img.getpixel(rm_pixel(box, 0, 900))[0] < 60  # the black stroke is there
    # a page never written on shows Zotero's highlight instead
    [(_l, png1)] = ops.view_pages(item, "1")
    r, g, b = PILImage.open(io.BytesIO(png1)).convert("RGB").getpixel((10, 10))
    assert (r, g, b) == (255, 255, 255)


def test_changing_the_library(lib_world):
    z, files, eng, tablet, clock, ops, ml, item, att = lib_world
    col = ops.create_collection("Reading group", parent=ml)
    assert col["created"] and col["path"] == "ML / Reading group"
    assert ops.create_collection("Reading group", parent=ml)["key"] == col["key"]  # no duplicate
    res = ops.file_items([item], add_to=[col["key"]])
    assert res["changed"] == [item] and set(z.objects[item]["collections"]) == {ml, col["key"]}
    assert ops.file_items([item], add_to=[col["key"]])["unchanged"] == [item]
    note = ops.add_note(item, "First thought.\n\nSecond paragraph <b>not html</b>.")
    assert "&lt;b&gt;" in z.objects[note["key"]]["note"] and z.objects[note["key"]]["parentItem"] == item
    ops.edit_item(item, {"title": "Attention Is All You Need"}, add_tags=["transformers"])
    assert z.objects[item]["title"] == "Attention Is All You Need" and {"tag": "transformers"} in z.objects[item]["tags"]
    with pytest.raises(ValueError):
        ops.edit_item(item, {"collections": []})
    assert eng.wakeup.is_set()
    # the tablet follows: filing into the subcollection leaves a pointer there
    clock.advance(500)
    assert not eng.sync().errors


def test_add_items(lib_world):
    z, files, eng, tablet, clock, ops, ml, item, att = lib_world
    z.update(item, DOI="https://doi.org/10.5555/ATTENTION")  # already in the library, under another spelling of its DOI
    eng.lib.refresh(z)
    res = ops.add_items(["doi:10.1109/CVPR.2016.90", "attention", "ambiguous words", "gibberish"], collections=[ml])["results"]
    added, reused, ambiguous, failed = res
    assert added["status"] == "added" and added["pdf"].startswith("PDF attached from https://oa.example.org")
    new = z.objects[added["key"]]
    assert new["title"] == "Deep Residual Learning" and new["collections"] == [ml] and new["tags"] == [{"tag": "vision"}]
    assert "attachments" not in new and new.get("key") == added["key"]
    pdfs = [o for o in z.items() if o.get("parentItem") == added["key"] and o.get("contentType") == "application/pdf"]
    assert len(pdfs) == 1 and files.get(pdfs[0]["key"])[1] == OA_PDF and pdfs[0]["md5"]
    assert reused["status"] == "already in library" and reused["key"] == item and reused["pdf"] == "already had a PDF"
    assert ambiguous["ambiguous"] == {"a1": "First match", "a2": "Second match"}
    assert "no translator" in failed["error"]
    # adding the same DOI again reuses it
    again = ops.add_items(["10.1109/cvpr.2016.90"])["results"][0]
    assert again["key"] == added["key"] and again["status"] == "already in library"
    # the new paper syncs to the tablet like anything else
    clock.advance(500)
    rep = eng.sync()
    assert not rep.errors and rep.uploaded == 1


def test_mcp_protocol_end_to_end(lib_world):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from rmzotero.claude.server import serve

    z, files, eng, tablet, clock, ops, ml, item, att = lib_world
    _t, info = serve(ops, "s3cret-path", "127.0.0.1", 0)
    base = f"http://127.0.0.1:{info['port']}"
    try:
        assert httpx.post(f"{base}/mcp/wrong", json={}).status_code == 404

        async def session():
            async with streamablehttp_client(f"{base}/mcp/s3cret-path") as (read, write, _):
                async with ClientSession(read, write) as s:
                    init = await s.initialize()
                    assert "reMarkable" in (init.instructions or "")
                    tools = {t.name: t for t in (await s.list_tools()).tools}
                    assert set(tools) == {"search_library", "get_item", "list_collections", "get_annotations", "read_pages", "view_pages",
                                          "add_items", "create_collection", "file_items", "add_note", "edit_item", "service_diagnostics"}
                    assert tools["search_library"].annotations.readOnlyHint is True
                    assert tools["add_items"].annotations.readOnlyHint is False
                    assert tools["file_items"].annotations.destructiveHint is True
                    assert tools["service_diagnostics"].annotations.readOnlyHint is True
                    r = await s.call_tool("service_diagnostics", {})
                    diag = json.loads(r.content[0].text)
                    assert "activity" in diag and "failures" in diag and "recent_cycles" in diag
                    r = await s.call_tool("search_library", {"query": "attention"})
                    assert json.loads(r.content[0].text)["items"][0]["key"] == item
                    r = await s.call_tool("view_pages", {"key": item, "pages": "1"})
                    kinds = [c.type for c in r.content]
                    assert kinds == ["text", "image"] and r.content[1].mimeType == "image/png"
                    r = await s.call_tool("get_item", {"key": "NOPE1234"})
                    assert r.isError and "no item" in r.content[0].text
                    r = await s.call_tool("create_collection", {"name": "From Claude"})
                    assert json.loads(r.content[0].text)["created"]

        asyncio.run(session())
    finally:
        info["server"].should_exit = True
    assert any(c.get("name") == "From Claude" for c in z.objects.values() if c["_kind"] == "collection")
