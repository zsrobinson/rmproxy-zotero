import json
import urllib.request

import pytest

from rmzotero.api import Api
from rmzotero.config import Config, Settings
from rmzotero.engine import Engine
from rmzotero.state import State
from rmzotero.tablet.cloud import MemoryCloud
from rmzotero.zotero.client import MemoryZotero
from rmzotero.zotero.webdav import MemoryFiles

from test_engine import PAPER, add_paper


@pytest.fixture
def api(tmp_path):
    z, files, cloud = MemoryZotero(7), MemoryFiles(), MemoryCloud()
    cfg = Config(data_dir=tmp_path, zotero_user_id=7, zotero_api_key="", webdav_url="", webdav_username="", webdav_password="",
                 rmfakecloud_url="", defaults=Settings(ocr=False))
    eng = Engine(cfg, State(tmp_path / "s.db"), z, files, cloud)
    ml = z.add_collection("ML")
    other = z.add_collection("Other")
    item, att = add_paper(z, files, [ml])
    eng.sync()
    return Api(eng, "secret"), eng, z, item, att, ml, other


def call(api, method, path, body=None, token="secret"):
    return api.dispatch(method, path, {"authorization": f"Bearer {token}"}, json.dumps(body).encode() if body is not None else b"")


def test_auth_and_status(api):
    a, eng, z, item, att, ml, other = api
    assert call(a, "GET", "/api/status", token="wrong")[0] == 401
    assert a.dispatch("GET", "/api/health", {}, b"")[0] == 200
    code, st = call(a, "GET", "/api/status")
    assert code == 200 and st["documents"] == {"active": 1}


def test_item_status_detail_and_actions(api):
    a, eng, z, item, att, ml, other = api
    code, res = call(a, "POST", "/api/items/status", {"keys": [item, att]})
    assert res["status"] == {item: "synced", att: "synced"}
    code, d = call(a, "GET", f"/api/items/{item}")
    assert d["attachments"][0]["location"] == "Zotero/ML" and d["attachments"][0]["state"] == "synced"
    assert call(a, "POST", f"/api/items/{item}/home", {"collection": "NOPE1234"})[0] == 400
    assert call(a, "POST", f"/api/attachments/{att}/remove")[1] == {"ok": True}
    assert call(a, "POST", "/api/items/status", {"keys": [item]})[1]["status"][item] == "removed"
    eng.sync()  # the removal reaches the tablet: in its trash
    row = eng.state.doc(att)
    assert eng.tree.is_trashed(row["doc_id"]) and row["state"] == "removed"
    assert call(a, "POST", f"/api/attachments/{att}/restore")[1] == {"ok": True}
    eng.sync()
    assert call(a, "POST", "/api/items/status", {"keys": [item]})[1]["status"][item] == "synced"


def test_settings_and_collections(api):
    a, eng, z, item, att, ml, other = api
    code, s = call(a, "PUT", "/api/settings", {"excluded_collections": [other], "pointers": "all"})
    assert code == 200 and s["excluded_collections"] == [other] and s["pointers"] == "all"
    assert call(a, "PUT", "/api/settings", {"pointers": "sometimes"})[0] == 400
    code, cols = call(a, "GET", "/api/collections")
    by = {c["name"]: c for c in cols["collections"]}
    assert by["ML"]["documents"] == 1 and by["ML"]["bytes"] >= len(PAPER) and by["Other"]["excluded"]
    assert call(a, "POST", "/api/collections/projection", {"excluded_collections": [ml]})[1]["documents"] == 0
    assert call(a, "POST", "/api/collections/projection", {"excluded_collections": []})[1]["documents"] == 1


def test_real_http_server(api):
    a, *_ = api
    server = a.serve("127.0.0.1", 0)
    port = server.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/settings", headers={"Authorization": "Bearer secret"})
    with urllib.request.urlopen(req) as r:
        assert json.loads(r.read())["sync_root"] == "Zotero"
    server.shutdown()


def test_check_commands(tmp_path, monkeypatch, capsys):
    from rmscene import scene_items as si

    from rmzotero import cli
    from rmzotero.tablet import content as tc
    from rmzotero.tablet.scene import Page
    from rmzotero.tablet.tree import BlobCache, CloudTree

    z, files, cloud = MemoryZotero(7), MemoryFiles(), MemoryCloud()
    item, att = add_paper(z, files, [])
    monkeypatch.setattr(cli, "_clients", lambda cfg: (z, files, cloud))
    conf = tmp_path / "c.toml"
    conf.write_text(f'data_dir = "{tmp_path / "data"}"\n[api]\ntoken = "x"\n')

    assert cli.main(["--config", str(conf), "check-zotero"]) == 0
    assert sum(1 for o in z.items() if o.get("itemType") == "annotation") == 2
    assert cli.main(["--config", str(conf), "check-zotero", "--cleanup"]) == 0
    assert sum(1 for o in z.items() if o.get("itemType") == "annotation") == 0

    # step 1: upload without a page list
    assert cli.main(["--config", str(conf), "check-tablet"]) == 0
    assert cli.main(["--config", str(conf), "check-tablet", "--annotate"]) == 2  # not opened yet
    tablet = CloudTree(cloud, BlobCache(tmp_path / "t"))

    def tablet_draw(n):
        tablet.root_hash = ""
        tablet.mirror()
        doc = next(d for d in tablet.docs.values() if d.name == cli.CHECK_DOC_NAME and not tablet.is_trashed(d.id))
        content = doc.content if tc.all_pages(doc.content) else {**doc.content, "cPages": tc.new_pdf_content(2)["cPages"]}
        pid = tc.pages(content)[0].id
        e = doc.page_entries().get(pid)
        page = Page.parse(tablet.read(e)) if e else Page.empty()
        page.add(page.first_user_layer_id(), [si.Line(si.PenColor.BLACK, si.Pen.PENCIL_2, [si.Point(n, 0, 0, 0, 8, 100), si.Point(n + 5, 5, 0, 0, 8, 100)], 1.0, 0.0)])
        tablet.stage(doc.id, content=content, files={f"/{pid}.rm": page.to_bytes()})
        tablet.commit()

    tablet_draw(0)  # the tablet opens it (making its own page ids) and the user draws
    # step 2: the service adds its layer to the tablet's own page
    assert cli.main(["--config", str(conf), "check-tablet", "--annotate"]) == 0
    assert cli.main(["--config", str(conf), "check-tablet", "--verify"]) == 1  # no new writing yet
    tablet_draw(10)
    # step 3
    assert cli.main(["--config", str(conf), "check-tablet", "--verify"]) == 0
    assert "✓ The tablet kept" in capsys.readouterr().out
