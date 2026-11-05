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


def test_broken_epub_fails_its_edition_once_and_says_why(world):  # noqa: F811
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
    assert calls == []  # nothing happens until asked
    eng.request_edition(epub)
    eng.sync()
    eng.sync()
    assert len(calls) == 1  # a failed edition isn't retried by itself
    [job] = eng.editions()
    assert job["status"] == "failed" and "not a valid EPUB" in job["message"] and job["progress"] is None
    d = collect(eng)
    assert d["queue"]["tablet_editions"][0]["status"] == "failed" and "Tablet edition of Broken Book: failed" in format_text(d)
    eng.request_edition(epub)  # asking again retries it
    eng.sync()
    assert len(calls) == 2


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


def test_nested_steps_in_a_phase_are_not_counted_twice():
    import time as _t

    from rmzotero.diagnostics import Activity

    act = Activity()
    with act.doing("conversions"):
        for book in ("a", "b"):
            with act.doing("conversions", book):
                assert act.what == f"conversions: {book}"
                _t.sleep(0.05)
    assert act.what == "idle"
    assert 0.1 <= act.phases["conversions"] < 0.15


def test_idle_cycles_are_collapsed():
    from rmzotero.diagnostics import summarise_cycles

    def cycle(t, uploaded=0, errors=()):
        return {"started": t, "seconds": 0.2, "uploaded": uploaded, "synced_docs": 0, "derived": 0, "to_zotero": 0, "to_tablet": 0,
                "pending": 0, "backed_off": 0, "paused": False, "errors": list(errors), "slowest_documents": []}

    newest_first = [cycle(100), cycle(99), cycle(98), cycle(97, uploaded=2), cycle(96), cycle(95, errors=["boom"]), cycle(94)]
    out = summarise_cycles(newest_first)
    assert [c.get("idle_cycles") or c["started"] for c in out] == [3, 97, 1, 95, 1]
    assert out[0]["newest"] == 100 and out[0]["oldest"] == 98 and out[0]["average_seconds"] == 0.2
