"""The HTTP API the Zotero plugin talks to (JSON, bearer token)."""

from __future__ import annotations

import hmac
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from . import __version__, planner
from .config import Settings, save_settings
from .engine import Engine

log = logging.getLogger(__name__)

Route = tuple[str, re.Pattern, Callable, bool]


class Api:
    def __init__(self, engine: Engine, token: str):
        if not token:
            raise ValueError("an API token is required (api.token in the config or RMZOTERO_API_TOKEN)")
        self.engine = engine
        self.token = token
        self.routes: list[Route] = []
        r = self._route
        r("GET", r"/api/health", self.health, auth=False)
        r("GET", r"/api/status", self.status)
        r("GET", r"/api/diagnostics", self.diagnostics)
        r("GET", r"/api/settings", self.get_settings)
        r("PUT", r"/api/settings", self.put_settings)
        r("GET", r"/api/collections", self.collections)
        r("POST", r"/api/collections/projection", self.projection)
        r("POST", r"/api/items/status", self.items_status)
        r("GET", r"/api/items/(?P<key>[A-Z0-9]{8})", self.item_detail)
        r("POST", r"/api/items/(?P<key>[A-Z0-9]{8})/home", self.set_home)
        r("POST", r"/api/attachments/(?P<key>[A-Z0-9]{8})/restore", self.restore)
        r("POST", r"/api/attachments/(?P<key>[A-Z0-9]{8})/remove", self.remove)
        r("PUT", r"/api/attachments/(?P<key>[A-Z0-9]{8})/typography", self.typography)
        r("POST", r"/api/editions", self.make_editions)
        r("GET", r"/api/editions", self.list_editions)
        r("POST", r"/api/sync", self.sync_now)
        r("POST", r"/api/restore-removed", self.restore_removed)
        r("POST", r"/api/cleanup-copies", self.cleanup_copies)

    def _route(self, method: str, pattern: str, fn: Callable, auth: bool = True) -> None:
        self.routes.append((method, re.compile(pattern + "$"), fn, auth))

    def dispatch(self, method: str, path: str, headers: dict, body: bytes) -> tuple[int, dict]:
        for m, pat, fn, auth in self.routes:
            match = pat.match(path)
            if match and m == method:
                if auth:
                    got = headers.get("authorization", "")
                    if not hmac.compare_digest(got.encode(), f"Bearer {self.token}".encode()):
                        return 401, {"error": "unauthorized"}
                try:
                    payload = json.loads(body) if body else {}
                except ValueError:
                    return 400, {"error": "invalid JSON"}
                try:
                    return 200, fn(payload, **match.groupdict())
                except KeyError as e:
                    return 404, {"error": f"not found: {e}"}
                except ValueError as e:
                    return 400, {"error": str(e)}
                except Exception as e:  # e.g. the library changed mid-request during a sync; the plugin retries
                    log.exception("API %s %s failed", method, path)
                    return 503, {"error": f"temporarily unavailable: {e}"}
        return 404, {"error": "no such endpoint"}

    # -- handlers ---------------------------------------------------------------------------
    def health(self, _):
        return {"ok": True, "version": __version__}

    def status(self, _):
        st = self.engine.state
        counts = {r["state"]: r["n"] for r in st.all("SELECT state, COUNT(*) AS n FROM docs GROUP BY state")}
        return {
            "version": __version__,
            "last_report": st.get("last_report"),
            "storage": st.get("storage"),
            "documents": counts,
            "events": st.events(limit=30),
            "zotero_library_version": self.engine.lib.version,
        }

    def diagnostics(self, _):
        from .diagnostics import collect

        return collect(self.engine)

    def get_settings(self, _):
        return self.engine.settings.as_dict()

    def put_settings(self, payload):
        eng = self.engine
        new = Settings.from_dict(payload, eng.settings)
        save_settings(eng.state, new, eng.cfg.defaults)
        eng.wakeup.set()
        return new.as_dict()

    def collections(self, _):
        eng = self.engine
        lib = eng.lib
        settings = eng.settings
        excluded = planner.excluded_set(lib, settings)
        # what each collection would hold if included
        everything = planner.plan(lib, Settings.from_dict({"excluded_collections": []}, settings), eng.state)
        sizes = {r["att_key"]: r["size"] or 0 for r in eng.state.docs()}
        out = []
        for key, c in lib.collections.items():
            if c.get("deleted"):
                continue
            subtree = [key] + lib.collection_descendants(key)
            atts = set().union(*(everything.collection_items.get(k, set()) for k in subtree))
            out.append({
                "key": key, "name": c["name"], "parent": c.get("parentCollection") or None, "path": lib.collection_path(key),
                "excluded": key in excluded, "explicitly_excluded": key in settings.excluded_collections,
                "documents": len(atts), "bytes": sum(sizes.get(a, 0) for a in atts),
                "unknown_sizes": sum(1 for a in atts if a not in sizes),
            })
        out.sort(key=lambda c: [p.lower() for p in c["path"]])
        storage = eng.state.get("storage") or {}
        return {"collections": out, "storage": storage}

    def projection(self, payload):
        """Documents and bytes the tablet would hold with a candidate set of excluded collections."""
        eng = self.engine
        excluded = [k for k in payload.get("excluded_collections", []) if k in eng.lib.collections]
        p = planner.plan(eng.lib, Settings.from_dict({"excluded_collections": excluded}, eng.settings), eng.state)
        sizes = {r["att_key"]: r["size"] or 0 for r in eng.state.docs()}
        storage = eng.state.get("storage") or {}
        return {"documents": len(p.docs), "bytes": sum(sizes.get(k, 0) for k in p.docs),
                "unknown_sizes": sum(1 for k in p.docs if k not in sizes), "capacity_bytes": storage.get("capacity_bytes")}

    def _doc_info(self, att_key: str) -> dict:
        eng = self.engine
        row = eng.state.doc(att_key)
        if row is None:
            return {"state": "not_synced"}
        state = row["state"]
        if state == "active":
            doc = eng.tree.docs.get(row["doc_id"])
            in_sync = doc is not None and doc.hash == row["tablet_doc_hash"] and eng._zotero_sig(att_key) == (row["zlib_version"] or 0)
            state = "error" if row["error"] else ("synced" if in_sync else "pending")
        location = None
        if row["folder_key"]:
            settings = eng.settings
            root = [p for p in settings.sync_root.split("/") if p]
            path = eng.lib.collection_path(row["folder_key"]) if row["folder_key"] in eng.lib.collections else [planner.UNFILED_NAME]
            location = "/".join(root + path)
        links = eng.state.links_for(att_key)
        return {
            "state": state, "name": row["name"], "location": location, "kind": row["kind"], "error": row["error"],
            "last_synced": row["last_synced"], "size": row["size"],
            "annotations": {"from_tablet": sum(1 for l in links if l["origin"] == "tablet"),
                            "from_zotero": sum(1 for l in links if l["origin"] == "zotero" and not l["unsynced"]),
                            "zotero_only": sum(1 for l in links if l["unsynced"])},
        }

    def _attachment_keys(self, key: str) -> list[str]:
        """The attachments to show for an item: searchable copies stand in for their scans; an EPUB is
        listed with its tablet edition, since it has its own action (making the edition)."""
        lib = self.engine.lib
        it = lib.items.get(key)
        if it is None:
            return []
        if it.get("itemType") == "attachment":
            keys = [key]
        else:
            keys = [a["key"] for a in lib.attachments(key) if not a.get("deleted")]
        derived: dict[str, list[str]] = {}
        for r in self.engine.state.all("SELECT * FROM derived WHERE derived_key IS NOT NULL"):
            derived.setdefault(r["source_key"], []).append(r["derived_key"])
        out = []
        for k in keys:
            att = lib.items.get(k) or {}
            if att.get("contentType") == "application/epub+zip":
                out += [k, *derived.get(k, [])]
            else:
                out += derived.get(k, [k])
        return list(dict.fromkeys(out))

    def _epub_keys(self, key: str) -> list[str]:
        lib = self.engine.lib
        it = lib.items.get(key)
        if it is None:
            return []
        atts = [it] if it.get("itemType") == "attachment" else lib.attachments(key)
        return [a["key"] for a in atts if a.get("contentType") == "application/epub+zip" and not a.get("deleted")]

    def items_status(self, payload):
        result = {}
        busy = {e["key"] for e in self.engine.editions() if e["status"] in ("queued", "working")}
        for key in payload.get("keys", [])[:500]:
            if busy and busy.intersection(self._epub_keys(key)):
                result[key] = "typesetting"
                continue
            infos = [self._doc_info(k) for k in self._attachment_keys(key)]
            states = [i["state"] for i in infos if i["state"] != "not_synced"]
            if not states:
                result[key] = "not_synced"
            else:
                for s in ("error", "pending", "removed", "archived", "trashed", "synced"):
                    if s in states:
                        result[key] = s
                        break
        return {"status": result}

    def item_detail(self, _, key: str):
        eng = self.engine
        lib = eng.lib
        it = lib.items[key]
        item_key = it.get("parentItem") or key if it.get("itemType") == "attachment" else key
        item = lib.items.get(item_key, it)
        cols = [c for c in (item.get("collections") or []) if c in lib.collections]
        override = eng.state.one("SELECT collection_key FROM home_overrides WHERE item_key=?", (item_key,))
        atts = []
        for k in self._attachment_keys(item_key):
            a = lib.items.get(k, {})
            src = eng.state.one("SELECT source_key FROM derived WHERE derived_key=?", (k,))
            typo = eng.state.one("SELECT params FROM typography WHERE att_key=?", (src["source_key"] if src else k,))
            info = {"key": k, "title": a.get("title"), "source_key": src["source_key"] if src else None,
                    "typography": json.loads(typo["params"]) if typo else None, **self._doc_info(k)}
            if a.get("contentType") == "application/epub+zip":
                info["epub"] = True
                info["typography_effective"] = eng._typography(k, eng.settings).as_dict()
                info["edition"] = eng.edition_job(k)
            atts.append(info)
        pointers = [dict(r) for r in eng.state.all(f"SELECT * FROM pointers WHERE att_key IN ({','.join('?' for _ in atts) or 'NULL'})", [a["key"] for a in atts])]
        return {
            "item": {"key": item_key, "title": item.get("title")},
            "collections": [{"key": c, "path": lib.collection_path(c)} for c in cols],
            "home_override": override["collection_key"] if override else None,
            "attachments": atts,
            "pointers": [{"collection": p["collection_key"], "suppressed": bool(p["suppressed"]), "location": p["location"]} for p in pointers],
            "events": eng.state.events([a["key"] for a in atts] + [item_key], limit=20),
        }

    def set_home(self, payload, key: str):
        ckey = payload.get("collection")
        if ckey is not None and ckey not in self.engine.lib.collections:
            raise ValueError("unknown collection")
        self.engine.set_home(key, ckey)
        return {"ok": True}

    def restore(self, _, key: str):
        return {"ok": self.engine.request_restore(key)}

    def remove(self, _, key: str):
        return {"ok": self.engine.request_remove(key)}

    def typography(self, payload, key: str):
        allowed = {"font", "text_scale", "line_height", "margins", "alignment"}
        params = {k: v for k, v in (payload or {}).items() if k in allowed} or None
        self.engine.set_typography(key, params)
        return {"ok": True}

    def make_editions(self, payload):
        """Make tablet editions of the EPUBs among ``keys`` (items or attachments), optionally with text settings."""
        typography = payload.get("typography")
        if typography is not None:
            allowed = {"font", "text_scale", "line_height", "margins", "alignment"}
            typography = {k: v for k, v in typography.items() if k in allowed and v not in (None, "")}
        queued, skipped = [], []
        for key in payload.get("keys", [])[:200]:
            epubs = self._epub_keys(key)
            if not epubs:
                skipped.append({"key": key, "reason": "no EPUB"})
            for k in epubs:
                started = self.engine.request_edition(k, typography)
                queued.append({"key": k, "item_key": key, "already_running": not started})
        return {"queued": queued, "skipped": skipped}

    def list_editions(self, _):
        return {"editions": self.engine.editions()}

    def cleanup_copies(self, payload):
        return {"applied": bool(payload.get("apply")), "copies": self.engine.cleanup_copies(apply=bool(payload.get("apply")))}

    def restore_removed(self, _):
        return {"restored": self.engine.restore_all_removed()}

    def sync_now(self, _):
        self.engine.wakeup.set()
        return {"ok": True}

    # -- server ---------------------------------------------------------------------------------
    def serve(self, host: str, port: int) -> ThreadingHTTPServer:
        api = self

        class Handler(BaseHTTPRequestHandler):
            server_version = f"rmzotero/{__version__}"

            def _handle(self, method: str):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                status, payload = api.dispatch(method, self.path.split("?")[0], {k.lower(): v for k, v in self.headers.items()}, body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.send_header("access-control-allow-origin", "*")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("access-control-allow-origin", "*")
                self.send_header("access-control-allow-headers", "authorization, content-type")
                self.send_header("access-control-allow-methods", "GET, POST, PUT, OPTIONS")
                self.end_headers()

            def log_message(self, fmt, *args):
                log.debug("api: " + fmt, *args)

        server = ThreadingHTTPServer((host, port), Handler)
        threading.Thread(target=server.serve_forever, name="api", daemon=True).start()
        return server
