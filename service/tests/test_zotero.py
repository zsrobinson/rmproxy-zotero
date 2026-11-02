import json

import httpx

from rmzotero.state import State
from rmzotero.tablet.cloud import RmCloudClient
from rmzotero.zotero.client import MemoryZotero, ZoteroClient
from rmzotero.zotero.library import Library
from rmzotero.zotero.naming import document_names, first_creator, render
from rmzotero.zotero.webdav import MemoryFiles, WebDAVFiles, parse_prop, prop_xml


def test_naming_default_template():
    item = {"itemType": "journalArticle", "title": "Attention Is All You Need", "date": "2017-06-12",
            "creators": [{"creatorType": "author", "lastName": "Vaswani"}, {"creatorType": "author", "lastName": "Shazeer"}, {"creatorType": "author", "lastName": "Parmar"}]}
    assert render(item) == "Vaswani et al. - 2017 - Attention Is All You Need"
    assert first_creator({"creators": [{"creatorType": "author", "lastName": "A"}, {"creatorType": "author", "lastName": "B"}]}) == "A and B"
    assert render({"title": "Untitled thing"}) == "Untitled thing"
    assert render(item, '{{ year }}_{{ title truncate="9" case="lower" }}') == "2017_attention"
    names = document_names(item, [{"key": "A", "title": "Preprint PDF"}, {"key": "B", "title": "Full Text PDF"}])
    assert names["A"].endswith("— Preprint PDF")
    dup = document_names(item, [{"key": "A", "title": "PDF", "filename": "a.pdf"}, {"key": "B", "title": "PDF", "filename": "b.pdf"}])
    assert dup == {"A": names["A"].split(" — ")[0] + " — a.pdf", "B": names["A"].split(" — ")[0] + " — b.pdf"}


def test_webdav_layout_over_http():
    store: dict[str, bytes] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        assert path.startswith("/dav/zotero/")
        if req.method == "PUT":
            store[path] = req.content
            return httpx.Response(201)
        if req.method == "DELETE":
            return httpx.Response(204 if store.pop(path, None) else 404)
        return httpx.Response(200, content=store[path]) if path in store else httpx.Response(404)

    dav = WebDAVFiles("https://nas/dav", client=httpx.Client(transport=httpx.MockTransport(handler)))
    md5, mtime = dav.put("ABCD1234", "paper.pdf", b"%PDF-data", 1700000000123)
    assert store["/dav/zotero/ABCD1234.prop"].decode() == prop_xml(1700000000123, md5)
    assert dav.get("ABCD1234") == ("paper.pdf", b"%PDF-data")
    assert dav.stat("ABCD1234") == (1700000000123, md5)
    assert parse_prop('<properties version="1"><mtime>1700000000</mtime><hash>ab</hash></properties>') == (1700000000000, "ab")
    mem = MemoryFiles()
    mem.put("K", "f.pdf", b"x")
    assert mem.get("K") == ("f.pdf", b"x")


def test_zotero_client_paging_and_writes():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, dict(req.url.params)))
        assert req.headers["Zotero-API-Version"] == "3"
        if req.url.path.endswith("/collections"):
            return httpx.Response(200, json=[], headers={"Last-Modified-Version": "10", "Total-Results": "0"})
        if req.url.path.endswith("/items") and req.method == "GET":
            start = int(req.url.params["start"])
            batch = [{"data": {"key": f"K{start + i}", "version": 9}} for i in range(min(100, 150 - start))]
            return httpx.Response(200, json=batch, headers={"Last-Modified-Version": "10", "Total-Results": "150"})
        if req.url.path.endswith("/deleted"):
            return httpx.Response(200, json={"items": ["GONE"]})
        if req.method == "POST":
            body = json.loads(req.content)
            return httpx.Response(200, json={"successful": {"0": {"key": body[0]["key"], "version": 11}},
                                             "failed": {"1": {"code": 412, "message": "conflict"}}}, headers={"Last-Modified-Version": "11"})
        if req.method == "DELETE":
            return httpx.Response(412 if req.url.path.endswith("STALE") else 204, headers={"Last-Modified-Version": "12"})
        raise AssertionError(req)

    z = ZoteroClient(5, "key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    cols, items, deleted, version = z.changes_since(3)
    assert len(items) == 150 and version == 10 and deleted == {"items": ["GONE"]}
    res = z.write_items([{"key": "AAAA2222"}, {"key": "BBBB3333", "version": 1}])
    assert res.versions == {"AAAA2222": 11} and res.conflicts == {"BBBB3333"}
    res = z.delete_items([("OK", 1), ("STALE", 1)])
    assert res.conflicts == {"STALE"}


def test_library_mirror_incremental(tmp_path):
    z = MemoryZotero()
    col = z.add_collection("ML")
    sub = z.add_collection("Transformers", parent=col)
    item = z.add_item({"itemType": "journalArticle", "title": "T", "collections": [sub]})
    att = z.add_item({"itemType": "attachment", "parentItem": item, "linkMode": "imported_file", "contentType": "application/pdf"})
    state = State(tmp_path / "s.db")
    lib = Library(state)
    assert lib.refresh(z) >= {col, sub, item, att}
    assert lib.collection_path(sub) == ["ML", "Transformers"]
    assert [a["key"] for a in lib.attachments(item)] == [att]
    z.update(item, deleted=1)
    assert lib.refresh(z) == {item}
    assert lib.is_trashed(att)
    z.remove(att)
    lib.refresh(z)
    assert lib.attachments(item) == []
    # survives restart
    lib2 = Library(State(tmp_path / "s.db"))
    assert lib2.version == lib.version and item in lib2.items


def test_rmcloud_client_auth_and_conflict(tmp_path):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, req.headers.get("authorization")))
        if req.url.path == "/token/json/2/device/new":
            assert json.loads(req.content)["code"] == "abcd1234"
            return httpx.Response(200, text="DEVICE")
        if req.url.path == "/token/json/2/user/new":
            assert req.headers["authorization"] == "Bearer DEVICE"
            return httpx.Response(200, text="USER")
        if req.url.path == "/sync/v4/root":
            return httpx.Response(200, json={"hash": "h1", "generation": 4, "schemaVersion": 4})
        if req.url.path == "/sync/v3/root":
            body = json.loads(req.content)
            if body["generation"] != 4:
                return httpx.Response(412)
            return httpx.Response(200, json={"hash": body["hash"], "generation": 5})
        if req.url.path.startswith("/sync/v3/files/"):
            return httpx.Response(200, content=b"blob")
        raise AssertionError(req.url.path)

    c = RmCloudClient("https://rm.example", tmp_path / "tok", client=httpx.Client(transport=httpx.MockTransport(handler)))
    c.register("abcd1234")
    assert c.get_root() == ("h1", 4)
    assert c.put_root("h2", 4) == 5
    import pytest

    from rmzotero.tablet.cloud import GenerationConflict

    with pytest.raises(GenerationConflict):
        c.put_root("h3", 3)
    assert c.get_blob("abc", "x.pdf") == b"blob"
    assert all(a == "Bearer USER" for m, p, a in seen if p.startswith("/sync"))
