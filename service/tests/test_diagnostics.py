import json

from rmzotero.api import Api
from rmzotero.diagnostics import BACKOFF_FIRST, Failures, collect, format_text
from rmzotero.state import State

from test_engine import add_paper, world  # noqa: F401


def test_failures_back_off_and_retry_when_inputs_change(tmp_path):
    t = [1000.0]
    f = Failures(State(tmp_path / "s.db"), clock=lambda: t[0])
    assert not f.blocked("K", "rendition", "v1")
    assert f.record("K", "rendition", "v1", "boom") == BACKOFF_FIRST
    assert f.blocked("K", "rendition", "v1")
    assert not f.blocked("K", "rendition", "v2")  # the file or settings changed: try again now
    assert f.record("K", "rendition", "v1", "boom") == 2 * BACKOFF_FIRST  # exponential
    t[0] += 2 * BACKOFF_FIRST + 1
    assert not f.blocked("K", "rendition", "v1")
    f.clear("K", "rendition")
    assert f.all() == []


def test_broken_epub_is_not_retried_every_cycle(world):  # noqa: F811
    z, files, cloud, clock, eng, tablet = world
    book = z.add_item({"itemType": "book", "title": "Broken Book", "collections": [z.add_collection("Novels")]})
    epub = z.add_item({"itemType": "attachment", "parentItem": book, "linkMode": "imported_file",
                       "contentType": "application/epub+zip", "title": "EPUB", "filename": "b.epub"})
    md5, _ = files.put(epub, "b.epub", b"not really an epub")
    z.update(epub, md5=md5)
    calls = []
    original = eng._make_rendition
    eng._make_rendition = lambda *a, **k: (calls.append(1), original(*a, **k))
    eng.sync()
    assert len(calls) == 1
    rep = eng.sync()
    assert len(calls) == 1 and rep.backed_off == 1  # waiting out its backoff
    d = collect(eng)
    [fail] = d["failures"]
    assert fail["item"] == "Broken Book" and fail["kind"] == "rendition" and fail["attempts"] == 1 and "traceback_tail" in fail
    clock.advance(BACKOFF_FIRST + 1)
    eng.sync()
    assert len(calls) == 2  # retried after the backoff
    assert collect(eng)["failures"][0]["attempts"] == 2


def test_diagnostics_report_and_endpoints(world, tmp_path):  # noqa: F811
    z, files, cloud, clock, eng, tablet = world
    add_paper(z, files, [z.add_collection("ML")])
    eng.sync()
    eng.sync()
    d = collect(eng)
    assert d["service"]["paused"] is False and d["activity"]["doing"] == "idle"
    assert d["last_cycle"]["seconds"] >= 0 and "zotero refresh" in d["last_cycle"]["phases"]
    assert len(d["recent_cycles"]) == 2 and d["documents"] == {"active": 1}
    assert d["queue"]["documents_not_yet_opened_on_tablet"] == 1
    assert "zotero refresh" in d["phase_average_seconds"]
    text = format_text(d)
    assert "Last cycle" in text and "Average time per step" in text
    json.dumps(d)  # serialisable for the API and the connector
    code, body = Api(eng, "t").dispatch("GET", "/api/diagnostics", {"authorization": "Bearer t"}, b"")
    assert code == 200 and body["library"]["items"] >= 2
