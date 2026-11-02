"""Push notifications that wake the sync loop: Zotero's streaming API and rmfakecloud's websocket."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


def _loop(name: str, connect: Callable[[], None], stop: threading.Event) -> None:
    backoff = 5.0
    while not stop.is_set():
        try:
            connect()
            backoff = 5.0
        except Exception as e:  # network trouble: retry with backoff; the poll timer covers the gap
            log.info("%s listener disconnected: %s", name, e)
        stop.wait(backoff)
        backoff = min(backoff * 2, 300.0)


def zotero_stream(api_key: str, user_id: int, wake: threading.Event, stop: threading.Event,
                  url: str = "wss://stream.zotero.org") -> threading.Thread:
    from websockets.sync.client import connect

    def run() -> None:
        with connect(url, open_timeout=30) as ws:
            ws.send(json.dumps({"action": "createSubscriptions", "subscriptions": [{"apiKey": api_key, "topics": [f"/users/{user_id}"]}]}))
            while not stop.is_set():
                try:
                    msg = json.loads(ws.recv(timeout=60))
                except TimeoutError:
                    continue
                if msg.get("event") == "topicUpdated":
                    log.debug("zotero library changed (version %s)", msg.get("version"))
                    wake.set()
                elif msg.get("event") == "connected":
                    wake.set()  # catch anything missed while disconnected

    t = threading.Thread(target=_loop, args=("zotero", run, stop), name="zotero-stream", daemon=True)
    t.start()
    return t


def rmcloud_notifications(url: str, token: Callable[[], str], wake: threading.Event, stop: threading.Event) -> threading.Thread:
    from websockets.sync.client import connect

    def run() -> None:
        with connect(url, additional_headers={"Authorization": f"Bearer {token()}"}, open_timeout=30) as ws:
            wake.set()
            while not stop.is_set():
                try:
                    ws.recv(timeout=60)
                except TimeoutError:
                    continue
                wake.set()

    t = threading.Thread(target=_loop, args=("rmfakecloud", run, stop), name="rmcloud-notify", daemon=True)
    t.start()
    return t


def run_forever(engine, poll_seconds: float, stop: threading.Event) -> None:
    """The main loop: sync when woken, when a document's quiet period ends, or on the poll timer."""
    wake = engine.wakeup
    wake.set()
    while not stop.is_set():
        report = engine.last_report
        timeout = poll_seconds
        if report.next_due:
            timeout = max(5.0, min(timeout, report.next_due - time.time() + 1))
        wake.wait(timeout)
        if stop.is_set():
            break
        wake.clear()
        time.sleep(2)  # let bursts of notifications settle
        wake.clear()
        engine.sync()
