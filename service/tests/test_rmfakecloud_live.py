"""Live test against a real rmfakecloud (skipped unless RMFAKECLOUD_TEST_URL is set).

    RMFAKECLOUD_TEST_URL=http://localhost:3001 RMFAKECLOUD_TEST_LOGIN=user:password pytest tests/test_rmfakecloud_live.py

The test logs into rmfakecloud's web UI API to get one-time codes: one
registers the service, the other a stand-in "tablet" that edits documents
through the same API the tablet uses. Use a throwaway account.
"""

import json
import os
import threading

import pytest
from rmscene import scene_items as si

from rmzotero.config import Config, Settings
from rmzotero.engine import Engine
from rmzotero.state import State
from rmzotero.tablet import content as tc
from rmzotero.tablet.cloud import RmCloudClient
from rmzotero.tablet.scene import Page
from rmzotero.tablet.tree import BlobCache, CloudTree
from rmzotero.zotero.client import MemoryZotero
from rmzotero.zotero.webdav import MemoryFiles

from test_engine import Clock, add_paper, highlight_of_first_word

URL = os.environ.get("RMFAKECLOUD_TEST_URL")
LOGIN = os.environ.get("RMFAKECLOUD_TEST_LOGIN", "")

pytestmark = pytest.mark.skipif(not URL or ":" not in LOGIN, reason="no live rmfakecloud configured")


def one_time_code() -> str:
    import httpx

    user, password = LOGIN.split(":", 1)
    token = httpx.post(f"{URL}/ui/api/login", json={"email": user, "password": password}).raise_for_status().text
    return httpx.get(f"{URL}/ui/api/newcode", headers={"Authorization": f"Bearer {token}"}).raise_for_status().json()


def test_service_and_tablet_through_real_rmfakecloud(tmp_path):
    service = RmCloudClient(URL, tmp_path / "svc-token")
    service.register(one_time_code())
    tablet_client = RmCloudClient(URL, tmp_path / "tab-token")
    tablet_client.register(one_time_code())

    # notifications reach a listener when the other side commits
    from rmzotero.listeners import rmcloud_notifications

    woke, stop = threading.Event(), threading.Event()
    rmcloud_notifications(service.notifications_url(), service.user_token, woke, stop)

    z, files, clock = MemoryZotero(7), MemoryFiles(), Clock()
    ml = z.add_collection("ML")
    item, att = add_paper(z, files, [ml])
    z.add_item(highlight_of_first_word(att))
    cfg = Config(data_dir=tmp_path / "data", zotero_user_id=7, zotero_api_key="", webdav_url="", webdav_username="",
                 webdav_password="", rmfakecloud_url=URL, quiet_seconds=60, defaults=Settings(ocr=False, sync_root="rmzotero-live-test"))
    eng = Engine(cfg, State(tmp_path / "data" / "s.db"), z, files, service, clock=clock)
    rep = eng.sync()
    assert not rep.errors, rep.errors

    tablet = CloudTree(tablet_client, BlobCache(tmp_path / "tab"))
    tablet.mirror()
    doc = next(d for d in tablet.docs.values() if d.name == "Vaswani - 2017 - Attention")
    assert tablet.path_of(doc.id) == ["rmzotero-live-test", "ML"]
    pid = tc.pages(doc.content)[0].id
    page = Page.parse(tablet.read(doc.page_entries()[pid]))
    assert [m.on_zotero_layer for m in page.marks()] == [True]

    woke.clear()
    page.add(page.first_user_layer_id(), [si.Line(si.PenColor.BLACK, si.Pen.BALLPOINT_2, [si.Point(0, 1200, 0, 0, 8, 100), si.Point(80, 1260, 0, 0, 8, 100)], 1.0, 0.0)])
    tablet.stage(doc.id, files={f"/{pid}.rm": page.to_bytes()})
    assert tablet.commit() == []
    assert woke.wait(15), "no notification from rmfakecloud"
    stop.set()

    eng.sync()
    clock.advance(300)
    rep = eng.sync()
    assert rep.annotations_to_zotero == 1, rep.errors
    ink = [o for o in z.items() if o.get("annotationType") == "ink"]
    assert len(ink) == 1 and json.loads(ink[0]["annotationPosition"])["pageIndex"] == 0

    # clean up: put everything we made in the trash
    eng.tree.mirror()
    for d in list(eng.tree.docs.values()):
        if d.id == doc.id or eng.tree.path_of(d.id)[:1] == ["rmzotero-live-test"] or d.name == "rmzotero-live-test":
            eng.tree.stage(d.id, metadata={**d.metadata, "parent": "trash"})
    eng.tree.commit()
