"""Diagnostics: what the service is doing, how long things take, and what keeps failing.

Shared by the plugin API (/api/diagnostics), the Claude connector (service_diagnostics) and
``rmzotero diagnostics``. Read-only and free of secrets.
"""

from __future__ import annotations

import json
import time
import traceback
from contextlib import contextmanager

from . import REVISION, __version__
from .state import State

BACKOFF_FIRST = 300.0  # 5 minutes
BACKOFF_MAX = 86400.0  # a day
KEEP_CYCLES = 200


class Failures:
    """Things that failed, with exponential backoff.

    A failure is keyed by (key, kind) and remembers a *fingerprint* of its inputs (file hash,
    settings...). It is retried when its backoff expires, or immediately if the inputs change.
    """

    def __init__(self, state: State, clock=time.time):
        self.state = state
        self.clock = clock

    def record(self, key: str, kind: str, fingerprint: str, message: str, tb: str = "") -> float:
        row = self.state.one("SELECT * FROM failures WHERE key=? AND kind=?", (key, kind))
        now = self.clock()
        attempts = (row["attempts"] + 1) if row and row["fingerprint"] == fingerprint else 1
        delay = min(BACKOFF_MAX, BACKOFF_FIRST * 2 ** (attempts - 1))
        self.state.upsert("failures", {
            "key": key, "kind": kind, "fingerprint": fingerprint, "attempts": attempts,
            "first_ts": row["first_ts"] if row and attempts > 1 else now, "last_ts": now, "next_retry": now + delay,
            "message": message[:1000], "traceback": tb[-4000:],
        }, ("key", "kind"))
        return delay

    def clear(self, key: str, kind: str) -> None:
        self.state.exec("DELETE FROM failures WHERE key=? AND kind=?", (key, kind))

    def blocked(self, key: str, kind: str, fingerprint: str) -> bool:
        row = self.state.one("SELECT fingerprint, next_retry FROM failures WHERE key=? AND kind=?", (key, kind))
        return bool(row) and row["fingerprint"] == fingerprint and row["next_retry"] > self.clock()

    def all(self) -> list[dict]:
        return [dict(r) for r in self.state.all("SELECT * FROM failures ORDER BY last_ts DESC")]


class Activity:
    """What the service is doing right now, plus per-phase timings for the current cycle."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.what = "idle"
        self.since = clock()
        self.phases: dict[str, float] = {}
        self._open: list[str] = []

    @contextmanager
    def doing(self, phase: str, detail: str | None = None):
        prev_what, prev_since = self.what, self.since
        self.what = f"{phase}: {detail}" if detail else phase
        self.since = self.clock()
        # a step inside the same phase (one book within "conversions") updates what we're doing,
        # but its time already counts towards the phase: don't add it twice
        nested = phase in self._open
        self._open.append(phase)
        start = time.perf_counter()
        try:
            yield
        finally:
            self._open.pop()
            if not nested:
                self.phases[phase] = self.phases.get(phase, 0.0) + (time.perf_counter() - start)
            self.what, self.since = prev_what, prev_since


def record_cycle(state: State, report: dict) -> None:
    with state.lock, state.db:
        state.db.execute(
            "INSERT INTO cycles(started, finished, report) VALUES (?, ?, ?)",
            (report.get("started"), report.get("finished"), json.dumps(report)),
        )
        state.db.execute(f"DELETE FROM cycles WHERE id NOT IN (SELECT id FROM cycles ORDER BY id DESC LIMIT {KEEP_CYCLES})")


def _title(engine, key: str) -> str:
    it = engine.lib.items.get(key) or {}
    if it.get("parentItem"):
        parent = engine.lib.items.get(it["parentItem"]) or {}
        return parent.get("title") or it.get("title") or key
    return it.get("title") or key


def collect(engine) -> dict:
    """A snapshot for people and for Claude. Safe to call while a sync is running."""
    for _ in range(3):
        try:
            return _collect(engine)
        except RuntimeError:  # the library changed size mid-read; try again
            time.sleep(0.2)
    return _collect(engine, light=True)


def _collect(engine, light: bool = False) -> dict:
    from . import planner
    from .tablet import content as tc

    now = engine.clock()
    state = engine.state
    settings = engine.settings
    act = engine.activity

    cycles = []
    for r in state.all("SELECT report FROM cycles ORDER BY id DESC LIMIT 20"):
        rep = json.loads(r["report"])
        cycles.append({
            "started": rep.get("started"), "seconds": round((rep.get("finished") or 0) - (rep.get("started") or 0), 2),
            "phases": {k: round(v, 2) for k, v in (rep.get("phases") or {}).items()},
            "uploaded": rep.get("uploaded"), "synced_docs": rep.get("synced_docs"), "derived": rep.get("derived"),
            "to_zotero": rep.get("annotations_to_zotero"), "to_tablet": rep.get("annotations_to_tablet"),
            "pending": rep.get("pending"), "backed_off": rep.get("backed_off"), "paused": rep.get("paused"),
            "errors": rep.get("errors") or [], "slowest_documents": rep.get("slowest") or [],
        })
    averages: dict[str, float] = {}
    for c in cycles:
        for k, v in c["phases"].items():
            averages[k] = averages.get(k, 0.0) + v / len(cycles)

    failures = []
    for f in Failures(state, engine.clock).all():
        failures.append({
            "item": _title(engine, f["key"]), "key": f["key"], "kind": f["kind"], "attempts": f["attempts"],
            "error": f["message"], "next_retry_in_s": max(0, round(f["next_retry"] - now)),
            "failing_since": f["first_ts"], "traceback_tail": (f["traceback"] or "")[-800:],
        })

    queue: dict = {}
    if not light and not settings.paused:
        plan = planner.plan(engine.lib, settings, state)
        blocked = {(f["key"], f["kind"]) for f in failures if f["next_retry_in_s"] > 0}
        kinds = {"rendition": "rendition", "ocr-check": "ocr-check"}
        pending = [d for d in plan.pending if (d.source_key, kinds[d.kind]) not in blocked]
        queue["conversions_pending"] = len(pending)
        queue["conversions_waiting_to_retry"] = len(plan.pending) - len(pending)
        queue["next_conversions"] = [{"item": _title(engine, d.source_key), "kind": d.kind} for d in pending[:10]]
        quiet, never_opened, in_error = [], 0, []
        for row in state.docs("active"):
            doc = engine.tree.docs.get(row["doc_id"])
            if doc is None:
                continue
            if row["kind"] != "epub" and not tc.all_pages(doc.content):
                never_opened += 1
            if row["error"]:
                in_error.append({"item": row["name"], "error": row["error"]})
            last = max(row["tablet_changed_at"] or 0, row["zotero_changed_at"] or 0)
            changed = doc.hash != row["tablet_doc_hash"] or engine._zotero_sig(row["att_key"]) != (row["zlib_version"] or 0)
            if changed and now - last < engine.cfg.quiet_seconds:
                quiet.append({"item": row["name"], "syncs_in_s": round(last + engine.cfg.quiet_seconds - now)})
        queue["documents_waiting_for_quiet_period"] = quiet[:20]
        queue["documents_not_yet_opened_on_tablet"] = never_opened
        queue["documents_with_errors"] = in_error[:20]

    counts = {r["state"]: r["n"] for r in state.all("SELECT state, COUNT(*) AS n FROM docs GROUP BY state")}
    return {
        "service": {
            "version": __version__, "revision": REVISION, "now": now, "uptime_s": round(now - engine.started_at),
            "paused": settings.paused, "quiet_seconds": engine.cfg.quiet_seconds, "poll_seconds": engine.cfg.poll_seconds,
        },
        "activity": {"doing": act.what, "for_s": round(now - act.since, 1)},
        "last_cycle": cycles[0] if cycles else None,
        "phase_average_seconds": {k: round(v, 2) for k, v in sorted(averages.items(), key=lambda kv: -kv[1])},
        "recent_cycles": cycles,
        "queue": queue,
        "failures": failures,
        "documents": counts,
        "library": {"items": len(engine.lib.items), "collections": len(engine.lib.collections), "version": engine.lib.version},
        "tablet": {"documents": len(engine.tree.docs), "generation": engine.tree.generation},
        "storage": state.get("storage"),
        "recent_events": state.events(limit=30),
    }


def format_text(d: dict) -> str:
    """A short human summary of ``collect()`` output (for ``rmzotero diagnostics``)."""
    s, a = d["service"], d["activity"]
    lines = [f"rmzotero {s['version']}{' (' + s['revision'][:7] + ')' if s.get('revision') else ''} - up {s['uptime_s'] // 60} min{' - PAUSED' if s['paused'] else ''}",
             f"Now: {a['doing']} (for {a['for_s']}s)"]
    lc = d.get("last_cycle")
    if lc:
        lines.append(f"Last cycle: {lc['seconds']}s; uploaded {lc['uploaded']}, synced {lc['synced_docs']}, converted {lc['derived']}, "
                     f"waiting {lc['pending']}, backed off {lc['backed_off']}")
    if d["phase_average_seconds"]:
        lines.append("Average time per step: " + ", ".join(f"{k} {v}s" for k, v in d["phase_average_seconds"].items()))
    q = d.get("queue") or {}
    if q:
        lines.append(f"Queue: {q.get('conversions_pending', 0)} conversions pending, {q.get('conversions_waiting_to_retry', 0)} waiting to retry, "
                     f"{len(q.get('documents_waiting_for_quiet_period', []))} documents in their quiet period, "
                     f"{q.get('documents_not_yet_opened_on_tablet', 0)} not yet opened on the tablet")
    lines.append(f"Documents: {d['documents']}; library: {d['library']['items']} items; tablet: {d['tablet']['documents']} documents")
    if d["failures"]:
        lines.append(f"Failures ({len(d['failures'])}):")
        for f in d["failures"][:10]:
            lines.append(f"  - {f['item']} [{f['kind']}] x{f['attempts']}, retry in {f['next_retry_in_s']}s: {f['error'][:160]}")
    return "\n".join(lines)


def format_traceback(e: BaseException) -> str:
    return "".join(traceback.format_exception(type(e), e, e.__traceback__))
