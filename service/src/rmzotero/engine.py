"""The sync cycle: Zotero library <-> tablet, one pass at a time.

A cycle:
1. refresh the Zotero mirror and the tablet tree, noting which documents changed;
2. make derived attachments that are due (EPUB renditions, searchable copies);
3. plan the tablet from the library and apply organisation: folders, new
   documents, names and places, removals, pointers (one tablet commit);
4. for each document that changed on either side and has been quiet long
   enough, sync page structure and annotations (a tablet commit per document,
   then Zotero writes);
5. archive long-unopened documents if enabled, and check storage.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from . import planner
from .annotations import convert as cv
from .annotations.reconcile import Link, PageCtx, reconcile
from .config import Config, Settings, load_settings
from .derive import ocr as ocr_mod
from .derive import rendition as rend
from .pdf import edit
from .pdf.geometry import NOTE_PAGE_SIZE, PageBox, rotate_rm_point
from .pdf.simple import placeholder_pdf, pointer_pdf
from .pdf.text import TextLayer
from .state import State
from .structure import StructureError, reconcile_structure
from .tablet import content as tc
from .tablet.cloud import BlobStore
from .tablet.scene import Page
from .tablet.tree import COLLECTION, DOCUMENT, TRASH, BlobCache, CloudTree, new_metadata
from .zotero.client import ZoteroAPI, new_key
from .zotero.library import Library
from .zotero.webdav import FileMissing, FileStore

log = logging.getLogger(__name__)

MAX_DERIVATIONS_PER_CYCLE = 4


def explain(e: Exception) -> str:
    """A message a person can act on, for the errors that are usually configuration problems."""
    import httpx

    if isinstance(e, httpx.HTTPStatusError):
        host, code = e.request.url.host, e.response.status_code
        if "zotero.org" in host and code == 403:
            return "Zotero rejected the API key (check [zotero] user_id and api_key; the key needs library write access)."
        if code in (401, 403):
            return f"{host} refused the credentials (HTTP {code}); check the [webdav] or [rmfakecloud] settings."
        return f"{host} answered HTTP {code}."
    if isinstance(e, httpx.ConnectError):
        return f"Can't connect to {e.request.url.host if e.request else 'a server'}: {e}"
    return f"{type(e).__name__}: {e}"


@dataclass
class Report:
    started: float = 0.0
    finished: float = 0.0
    uploaded: int = 0
    synced_docs: int = 0
    annotations_to_zotero: int = 0
    annotations_to_tablet: int = 0
    removed_on_tablet: int = 0
    trashed: int = 0
    derived: int = 0
    pending: int = 0  # documents waiting for their quiet period
    paused: bool = False
    errors: list[str] = field(default_factory=list)
    next_due: float | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Tools:
    pandoc: str = "pandoc"
    ocrmypdf: str = "ocrmypdf"
    font_paths: list[str] = field(default_factory=list)
    ocr_language: str = "eng"


class Engine:
    def __init__(self, cfg: Config, state: State, zotero: ZoteroAPI, files: FileStore, cloud: BlobStore,
                 *, tools: Tools | None = None, clock=time.time):
        self.cfg = cfg
        self.state = state
        self.zotero = zotero
        self.files = files
        self.cloud = cloud
        self.tools = tools or Tools()
        self.clock = clock
        self.lib = Library(state)
        self.cache = BlobCache(cfg.data_dir / "blobs")
        self.tree = CloudTree(cloud, self.cache)
        self.lock = threading.RLock()
        self.last_report = Report()
        self.wakeup = threading.Event()

    # ------------------------------------------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return load_settings(self.state, self.cfg.defaults)

    def sync(self) -> Report:
        with self.lock:
            rep = Report(started=self.clock())
            try:
                self._cycle(rep)
            except Exception as e:  # keep the service alive; report the failure
                log.exception("sync cycle failed")
                msg = explain(e)
                rep.errors.append(msg)
                self.state.event("error", f"Sync failed: {msg}")
            rep.finished = self.clock()
            self.last_report = rep
            self.state.set("last_report", rep.as_dict())
            return rep

    def _cycle(self, rep: Report) -> None:
        settings = self.settings
        changed_keys = self.lib.refresh(self.zotero)
        if settings.paused:
            # read-only: keep the library mirror fresh for the plugin, change nothing anywhere
            rep.paused = True
            return
        self.tree.mirror()
        self._observe(changed_keys)
        rep.derived = self._derive(settings)
        plan = planner.plan(self.lib, settings, self.state)
        self._apply_removals(rep)
        self._detect_tablet_removals(rep)
        new_docs = self._organise(plan, settings, rep)
        dropped = self.tree.commit()
        for doc_id in dropped:
            self._note_dropped(doc_id)
        needs_drawing: set[str] = set()
        for att_key in new_docs:
            row = self.state.doc(att_key)
            if row and row["doc_id"] not in dropped:
                h = self.tree.docs[row["doc_id"]].hash
                fields = {"tablet_doc_hash": h, "tablet_seen_hash": h}
                if self.lib.annotations(att_key):
                    needs_drawing.add(att_key)  # existing Zotero annotations go onto the new document now
                else:
                    sig = self._zotero_sig(att_key)
                    fields.update(zlib_version=sig, zotero_seen_sig=sig, last_synced=self.clock())
                self.state.update_doc(att_key, **fields)
        self._sync_contents(plan, rep, force=needs_drawing)
        self._retire(plan, rep)
        self._archive(settings, rep)
        self._storage_check(settings)

    # -- observation ------------------------------------------------------------------------------
    def _zotero_sig(self, att_key: str) -> int:
        att = self.lib.items.get(att_key)
        sig = att.get("version", 0) if att else 0
        for a in self.lib.annotations(att_key).values():
            sig = max(sig, a.get("version", 0))
        return sig

    def _observe(self, changed_keys: set[str]) -> None:
        now = self.clock()
        for row in self.state.docs("active"):
            doc = self.tree.docs.get(row["doc_id"])
            if doc and doc.hash != row["tablet_seen_hash"]:
                self.state.update_doc(row["att_key"], tablet_seen_hash=doc.hash, tablet_changed_at=now)
            sig = self._zotero_sig(row["att_key"])
            if sig != (row["zotero_seen_sig"] or 0):
                self.state.update_doc(row["att_key"], zotero_seen_sig=sig, zotero_changed_at=now)

    def _note_dropped(self, doc_id: str) -> None:
        row = self.state.doc_by_id(doc_id)
        if row:
            self.state.update_doc(row["att_key"], tablet_changed_at=self.clock())

    # -- derived attachments ---------------------------------------------------------------------
    def _typography(self, source_key: str, settings: Settings):
        row = self.state.one("SELECT params FROM typography WHERE att_key=?", (source_key,))
        base = settings.epub.as_dict()
        if row:
            base.update(json.loads(row["params"]))
        from .config import EpubDefaults

        return EpubDefaults(**base)

    def _derive(self, settings: Settings) -> int:
        plan = planner.plan(self.lib, settings, self.state)
        pending = list(plan.pending)
        # renditions whose typography changed
        for r in self.state.all("SELECT * FROM derived WHERE kind='rendition' AND derived_key IS NOT NULL"):
            params = json.loads(r["params"] or "{}")
            if r["source_key"] in self.lib.items and params.get("typography") != rend.params_hash(self._typography(r["source_key"], settings)):
                pending.append(planner.Derivation(r["source_key"], "rendition"))
        done = 0
        for d in pending[:MAX_DERIVATIONS_PER_CYCLE]:
            try:
                if d.kind == "rendition":
                    self._make_rendition(d.source_key, settings)
                else:
                    self._check_ocr(d.source_key)
                done += 1
            except FileMissing:
                self.state.event("warning", "The file isn't on the WebDAV share yet; will retry.", d.source_key)
            except Exception as e:
                log.exception("derivation failed for %s", d.source_key)
                self.state.event("error", f"Couldn't prepare this file: {e}", d.source_key)
        if len(pending) > MAX_DERIVATIONS_PER_CYCLE:
            self.wakeup.set()
        if done:
            self.lib.refresh(self.zotero)
        return done

    def _set_derived(self, source: str, kind: str, derived_key: str | None, params: dict) -> None:
        self.state.upsert("derived", {"source_key": source, "kind": kind, "derived_key": derived_key, "params": json.dumps(params)}, ("source_key", "kind"))

    def _create_attachment(self, source: dict, title: str, filename: str, data: bytes, tag: str) -> str:
        key = new_key()
        obj = {
            "key": key, "version": 0, "itemType": "attachment", "linkMode": "imported_file", "title": title, "contentType": "application/pdf",
            "charset": "", "filename": filename, "md5": None, "mtime": None, "note": "",
            "tags": [{"tag": tag}], "relations": {"dc:relation": [f"http://zotero.org/users/{self.zotero.user_id}/items/{source['key']}"]},
        }
        if source.get("parentItem"):
            obj["parentItem"] = source["parentItem"]
        else:
            obj["collections"] = list(source.get("collections") or [])
        res = self.zotero.write_items([obj])
        if key not in res.versions:
            raise RuntimeError(f"Zotero refused the new attachment: {res.failed.get(key) or 'conflict'}")
        md5, mtime = self.files.put(key, filename, data)
        res2 = self.zotero.write_items([{"key": key, "version": res.versions[key], "md5": md5, "mtime": mtime}])
        version = res2.versions.get(key, res.versions[key])
        self.lib.apply_local_write([{**obj, "version": version, "md5": md5, "mtime": mtime}])
        return key

    def _make_rendition(self, source_key: str, settings: Settings) -> None:
        src = self.lib.items[source_key]
        _fname, data = self.files.get(source_key)
        info = rend.inspect_epub(data)
        if info.drm:
            self._set_derived(source_key, "rendition", None, {"skip": "drm"})
            self.state.event("warning", "This EPUB is DRM-protected, so it can't be sent to the tablet.", source_key)
            return
        if info.fixed_layout:
            self._set_derived(source_key, "rendition", None, {"fixed": True})
            self.state.event("info", "Fixed-layout EPUB: sent as-is, without annotation sync.", source_key)
            return
        typo = self._typography(source_key, settings)
        pdf = rend.render_epub(data, typo, pandoc=self.tools.pandoc, font_paths=self.tools.font_paths)
        base = (src.get("title") or info.title or "Book").removesuffix(".epub")
        old = self.state.one("SELECT derived_key FROM derived WHERE source_key=? AND kind='rendition'", (source_key,))
        key = self._create_attachment(src, f"{base} (tablet edition)", f"{base} (tablet edition).pdf", pdf, "rmzotero:rendition")
        self._set_derived(source_key, "rendition", key, {"typography": rend.params_hash(typo)})
        old_key = old["derived_key"] if old else None
        if old_key and old_key in self.lib.items:
            has_markup = bool(self.lib.annotations(old_key)) or bool(self.state.links_for(old_key))
            if not has_markup:
                old_item = self.lib.items[old_key]
                res = self.zotero.write_items([{"key": old_key, "version": old_item["version"], "deleted": 1}])
                if old_key in res.versions:
                    self.lib.apply_local_write([{**old_item, "deleted": 1, "version": res.versions[old_key]}])
            else:
                self.state.event("info", "Text settings changed: a new tablet edition was made; the old one keeps its markup.", source_key)

    def _check_ocr(self, source_key: str) -> None:
        src = self.lib.items[source_key]
        _fname, data = self.files.get(source_key)
        if not ocr_mod.needs_ocr(data):
            self._set_derived(source_key, "searchable", None, {})
            return
        if self.lib.annotations(source_key):
            # already marked up in Zotero: keep one copy of the markup rather than splitting it
            self._set_derived(source_key, "searchable", None, {"reason": "annotated"})
            return
        if not ocr_mod.ocr_available(self.tools.ocrmypdf):
            self._set_derived(source_key, "searchable", None, {"error": "ocrmypdf unavailable"})
            return
        try:
            out = ocr_mod.make_searchable(data, binary=self.tools.ocrmypdf, language=self.tools.ocr_language)
        except ocr_mod.OcrError as e:
            self._set_derived(source_key, "searchable", None, {"error": str(e)[:200]})
            self.state.event("warning", f"Text recognition failed; sending the scan as-is. ({e})", source_key)
            return
        base = (src.get("title") or "Scan").removesuffix(".pdf")
        key = self._create_attachment(src, f"{base} (searchable)", f"{base} (searchable).pdf", out, "rmzotero:searchable")
        self._set_derived(source_key, "searchable", key, {})

    # -- organisation ----------------------------------------------------------------------------
    def _detect_tablet_removals(self, rep: Report) -> None:
        for row in self.state.docs("active"):
            doc_id = row["doc_id"]
            if doc_id not in self.tree.docs or self.tree.is_trashed(doc_id):
                self.state.update_doc(row["att_key"], state="removed")
                self.state.event("info", "Removed on tablet. Restore it from the plugin to send it back.", row["att_key"])
                rep.removed_on_tablet += 1
        for row in self.state.all("SELECT * FROM pointers WHERE suppressed=0 AND doc_id IS NOT NULL"):
            if row["doc_id"] not in self.tree.docs or self.tree.is_trashed(row["doc_id"]):
                self.state.exec("UPDATE pointers SET suppressed=1 WHERE att_key=? AND collection_key=?", (row["att_key"], row["collection_key"]))

    def _folder_doc(self, fkey: str) -> str:
        if fkey == "":
            return ""
        row = self.state.one("SELECT doc_id FROM folders WHERE fkey=?", (fkey,))
        return row["doc_id"] if row else ""

    def _ensure_folders(self, plan: planner.Plan) -> None:
        # parents before children
        done: set[str] = set()

        def ensure(fkey: str) -> str:
            if fkey == "" or fkey in done:
                return self._folder_doc(fkey)
            name, parent = plan.folders[fkey]
            parent_id = ensure(parent) if parent else ""
            doc_id = self._folder_doc(fkey)
            cur = self.tree.get(doc_id) if doc_id else None
            if cur is None or self.tree.is_trashed(doc_id):
                doc_id = str(uuid.uuid4())
                self.tree.stage(doc_id, metadata=new_metadata(name, parent_id, COLLECTION), content={})
                self.state.upsert("folders", {"fkey": fkey, "doc_id": doc_id}, "fkey")
            elif cur.name != name or cur.parent != parent_id:
                self.tree.stage(doc_id, metadata={**cur.metadata, "visibleName": name, "parent": parent_id})
            done.add(fkey)
            return doc_id

        for fkey in plan.folders:
            ensure(fkey)

    def _organise(self, plan: planner.Plan, settings: Settings, rep: Report) -> list[str]:
        self._ensure_folders(plan)
        new_docs: list[str] = []
        for att_key, want in plan.docs.items():
            row = self.state.doc(att_key)
            parent = self._folder_doc(want.folder)
            if row is None:
                try:
                    self._upload(want, parent)
                    new_docs.append(att_key)
                    rep.uploaded += 1
                except FileMissing:
                    self.state.event("warning", "The file isn't on the WebDAV share yet; will retry.", att_key)
                except Exception as e:
                    log.exception("upload failed for %s", att_key)
                    self.state.update_doc(att_key, state="error", error=str(e)[:300])
                    rep.errors.append(f"{want.name}: {e}")
                continue
            st = row["state"]
            if st == "removed" and not row["restore_requested"]:
                continue
            if st in ("trashed", "removed", "error") or row["restore_requested"]:
                if st == "error":
                    self.state.exec("DELETE FROM docs WHERE att_key=?", (att_key,))
                    continue  # retried as a fresh upload next cycle
                self._restore(row, want, parent)
                continue
            if st == "archived":
                continue
            doc = self.tree.get(row["doc_id"])
            if doc is None:
                continue
            if doc.name != want.name or doc.parent != parent:
                self.tree.stage(doc.id, metadata={**doc.metadata, "visibleName": want.name, "parent": parent})
            self.state.update_doc(att_key, name=want.name, folder_key=want.folder)
        self._pointers(plan)
        self._prune_folders(plan)
        return new_docs

    def _upload(self, want: planner.DesiredDoc, parent: str) -> None:
        filename, data = self.files.get(want.att_key)
        att = self.lib.items.get(want.att_key, {})
        doc_id = str(uuid.uuid4())
        if want.kind == "epub":
            content = tc.new_pdf_content(0, "epub")
            content.pop("cPages", None)
            self.tree.stage(doc_id, metadata=new_metadata(want.name, parent, DOCUMENT), content=content, files={".epub": data})
            mapping = []
        else:
            # No page list: the tablet makes its own the first time the document is opened, and
            # ignores one supplied by us (verified on a reMarkable 2; assumption A11 was false).
            content = tc.upload_content(edit.page_count(data))
            self.tree.stage(doc_id, metadata=new_metadata(want.name, parent, DOCUMENT), content=content, files={".pdf": data})
            mapping = None
        fhash = self.cache.put(data)
        self.state.update_doc(
            want.att_key, doc_id=doc_id, item_key=want.item_key, kind=want.kind, state="active", folder_key=want.folder,
            name=want.name, zotero_md5=att.get("md5"), zotero_file_hash=fhash, tablet_pdf_hash=fhash,
            pages=mapping, page_hashes={}, size=len(data), error=None, zlib_version=0,
            tablet_content_hash=self.cache.put(json.dumps(content).encode()), restore_requested=0,
        )

    def _restore(self, row, want: planner.DesiredDoc, parent: str) -> None:
        """Bring a document back: from the tablet's trash if it's still there, else rebuilt from the exact archive."""
        doc_id = row["doc_id"]
        doc = self.tree.get(doc_id)
        if doc is not None:
            self.tree.stage(doc_id, metadata={**doc.metadata, "parent": parent, "visibleName": want.name})
        else:
            pdf = self.cache.get_local(row["tablet_pdf_hash"] or "") or self.files.get(want.att_key)[1]
            content_raw = self.cache.get_local(row["tablet_content_hash"] or "")
            content = json.loads(content_raw) if content_raw else tc.new_pdf_content(edit.page_count(pdf))
            files = {(".epub" if row["kind"] == "epub" else ".pdf"): pdf}
            for pid, h in json.loads(row["page_hashes"] or "{}").items():
                data = self.cache.get_local(h)
                if data is not None:
                    files[f"/{pid}.rm"] = data
            self.tree.stage(doc_id, metadata=new_metadata(want.name, parent, DOCUMENT), content=content, files=files)
        self.state.update_doc(row["att_key"], state="active", restore_requested=0, folder_key=want.folder, name=want.name)

    def _pointers(self, plan: planner.Plan) -> None:
        wanted = {(p.att_key, p.folder): p for p in plan.pointers}
        for row in self.state.all("SELECT * FROM pointers"):
            k = (row["att_key"], row["collection_key"])
            if k not in wanted and row["doc_id"] and row["doc_id"] in self.tree.docs and not row["suppressed"]:
                self.tree.stage_remove(row["doc_id"])
                self.state.exec("UPDATE pointers SET doc_id=NULL WHERE att_key=? AND collection_key=?", k)
        for (att_key, ckey), p in wanted.items():
            main = self.state.doc(att_key)
            if not main or main["state"] != "active":
                continue
            row = self.state.one("SELECT * FROM pointers WHERE att_key=? AND collection_key=?", (att_key, ckey))
            parent = self._folder_doc(ckey)
            doc = self.tree.get(row["doc_id"]) if row and row["doc_id"] else None
            if doc is None:
                doc_id = str(uuid.uuid4())
                pdf = pointer_pdf(p.title, p.location)
                self.tree.stage(doc_id, metadata=new_metadata(p.name, parent), content=tc.new_pdf_content(1), files={".pdf": pdf})
                self.state.upsert("pointers", {"att_key": att_key, "collection_key": ckey, "doc_id": doc_id, "suppressed": 0, "location": p.location, "name": p.name}, ("att_key", "collection_key"))
                continue
            files = {".pdf": pointer_pdf(p.title, p.location)} if row["location"] != p.location else None
            if doc.name != p.name or doc.parent != parent or files:
                self.tree.stage(doc.id, metadata={**doc.metadata, "visibleName": p.name, "parent": parent}, files=files)
                self.state.exec("UPDATE pointers SET location=?, name=? WHERE att_key=? AND collection_key=?", (p.location, p.name, att_key, ckey))

    def _prune_folders(self, plan: planner.Plan) -> None:
        live = self.tree.all_docs()
        for row in self.state.all("SELECT * FROM folders"):
            if row["fkey"] in plan.folders:
                continue
            doc_id = row["doc_id"]
            children = [d for d in live.values() if d.parent == doc_id]
            if doc_id in live and not children:
                self.tree.stage_remove(doc_id)
                self.state.exec("DELETE FROM folders WHERE fkey=?", (row["fkey"],))

    def _retire(self, plan: planner.Plan, rep: Report) -> None:
        """Documents no longer in scope: one last markup pull, then into the tablet's trash."""
        for row in self.state.docs("active"):
            if row["att_key"] in plan.docs:
                continue
            if row["att_key"] in self.lib.items and not self.lib.is_trashed(row["att_key"]):
                try:
                    self._sync_doc(row, force=True, rep=rep)
                except Exception:
                    log.exception("final pull failed for %s", row["att_key"])
            doc = self.tree.get(row["doc_id"])
            if doc is not None:
                self.tree.stage(doc.id, metadata={**doc.metadata, "parent": TRASH})
            self.state.update_doc(row["att_key"], state="trashed")
            rep.trashed += 1
        for doc_id in self.tree.commit():
            self._note_dropped(doc_id)

    # -- content sync ------------------------------------------------------------------------------
    def _sync_contents(self, plan: planner.Plan, rep: Report, force: set[str]) -> None:
        now = self.clock()
        quiet = self.cfg.quiet_seconds
        next_due = None
        for row in self.state.docs("active"):
            if row["kind"] == "epub" or row["att_key"] not in plan.docs:
                continue
            doc = self.tree.docs.get(row["doc_id"])
            if doc is None:
                continue
            tablet_changed = doc.hash != row["tablet_doc_hash"]
            zotero_changed = self._zotero_sig(row["att_key"]) != (row["zlib_version"] or 0)
            if not (tablet_changed or zotero_changed or row["att_key"] in force):
                continue
            last_change = max(row["tablet_changed_at"] or 0, row["zotero_changed_at"] or 0)
            if row["att_key"] not in force and now - last_change < quiet:
                due = last_change + quiet
                next_due = due if next_due is None else min(next_due, due)
                rep.pending += 1
                continue
            try:
                self._sync_doc(row, force=False, rep=rep)
                rep.synced_docs += 1
            except Exception as e:
                log.exception("sync failed for %s", row["att_key"])
                self.state.update_doc(row["att_key"], error=str(e)[:300])
                self.state.event("error", f"Sync failed: {e}", row["att_key"])
                rep.errors.append(f"{row['name']}: {e}")
        rep.next_due = next_due

    def _sync_doc(self, row, *, force: bool, rep: Report) -> None:
        att_key = row["att_key"]
        att = self.lib.items[att_key]
        doc = self.tree.docs[row["doc_id"]]

        # Zotero file (current) and the one at the last sync
        if att.get("md5") and att.get("md5") == row["zotero_md5"] and self.cache.has(row["zotero_file_hash"] or ""):
            zfile = self.cache.get_local(row["zotero_file_hash"])
        else:
            _fn, zfile = self.files.get(att_key)
        old_zfile = self.cache.get_local(row["zotero_file_hash"] or "") if row["zotero_file_hash"] else None

        content = doc.content
        if not tc.all_pages(content):
            # Not opened on the tablet yet: its page list (and page ids) don't exist until then.
            # Nothing to reconcile; Zotero annotations are drawn once the tablet has opened it.
            sig = self._zotero_sig(att_key)
            self.state.update_doc(att_key, tablet_doc_hash=doc.hash, tablet_seen_hash=doc.hash,
                                  zlib_version=sig, zotero_seen_sig=sig, last_synced=self.clock())
            return
        tablet_pages = tc.pages(content)
        page_entries = doc.page_entries()
        last_hashes = json.loads(row["page_hashes"] or "{}")
        tablet_dirty = {pid for pid, e in page_entries.items() if last_hashes.get(pid) != e.hash}
        zdirty = {int(cv.position_of(a).get("pageIndex", -1)) for a in self.lib.annotations(att_key).values()
                  if a.get("version", 0) > (row["zlib_version"] or 0)}
        last_mapping = json.loads(row["pages"]) if row["pages"] else None

        st = reconcile_structure(
            tablet_pages=tablet_pages, tablet_all=tc.all_pages(content), last_mapping=last_mapping,
            old_zotero=old_zfile, new_zotero=zfile, tablet_dirty_pages=tablet_dirty, zotero_dirty_zidx=zdirty,
        )
        for w in st.warnings:
            self.state.event("warning", w, att_key)

        # tablet-side structural edits
        new_content = content
        for pid in st.tablet_delete:
            new_content = tc.set_deleted(new_content, pid, True)
        if st.tablet_undelete:
            # in the legacy format a deleted page has left the list; put it back where it was
            prev_raw = self.cache.get_local(row["tablet_content_hash"] or "")
            prev = {p.id: p for p in tc.all_pages(json.loads(prev_raw))} if prev_raw else {}
            for pid in st.tablet_undelete:
                ref = prev.get(pid)
                new_content = tc.set_deleted(new_content, pid, False, index=st.mapping.index(pid) if pid in st.mapping else None,
                                             redir=ref.redir if ref else None)
        tablet_files: dict[str, bytes] = {}
        tablet_pdf = self.tree.read(doc.file(".pdf"))
        redir = {p.id: p.redir for p in tc.all_pages(content)}
        rotated_pages: dict[str, tuple[PageBox, int]] = {}
        if st.tablet_rotations:
            boxes = edit.page_boxes(tablet_pdf)
            rots = {}
            for pid, rot in st.tablet_rotations.items():
                r = redir.get(pid)
                if r is not None and r < len(boxes):
                    rots[r] = rot
                    rotated_pages[pid] = (boxes[r], rot)
            tablet_pdf = edit.set_rotation(tablet_pdf, rots)
            tablet_files[".pdf"] = tablet_pdf

        final_z = st.final_pdf or zfile
        boxes = edit.page_boxes(final_z)
        labels = edit.page_labels(final_z)
        text = TextLayer(final_z)

        pages: dict[str, PageCtx] = {}
        for zidx, pid in enumerate(st.mapping):
            e = page_entries.get(pid)
            page = Page.parse(self.tree.read(e)) if e else Page.empty()
            if pid in rotated_pages:
                old_box, rot = rotated_pages[pid]
                page.transform_all(lambda x, y, b=old_box, r=rot: rotate_rm_point(x, y, b, r))
            modified = tc.page_modified_ms(content, pid) or int(doc.metadata.get("lastModified") or 0)
            ctx = PageCtx(pid, zidx, boxes[zidx], labels[zidx], page, modified)
            ctx.dirty = pid in rotated_pages and e is not None
            pages[pid] = ctx

        # Zotero annotations, re-indexed to the final page order
        zanns: dict[str, dict] = {}
        reindex: dict[str, dict] = {}
        orphan_deletes: list[tuple[str, int]] = []
        new_index = {pid: i for i, pid in enumerate(st.mapping)}
        for k, a in self.lib.annotations(att_key).items():
            pos = cv.position_of(a)
            pid = st.current_zidx_to_page.get(int(pos.get("pageIndex", -1)))
            if pid is None or pid not in new_index:
                if pid in st.deleted_pages:
                    orphan_deletes.append((k, a["version"]))
                continue
            if new_index[pid] != pos.get("pageIndex"):
                pos["pageIndex"] = new_index[pid]
                parts = (a.get("annotationSortIndex") or "00000|000000|00000").split("|")
                a = {**a, "annotationPosition": json.dumps(pos), "annotationSortIndex": "|".join([f"{new_index[pid]:05d}", *parts[1:]])}
                reindex[k] = a
            zanns[k] = a

        links = {r["zkey"]: Link.from_row(r) for r in self.state.links_for(att_key)}
        prior_links = {k: Link.from_row(r) for k, r in ((r["zkey"], r) for r in self.state.links_for(att_key))}
        res = reconcile(pages, zanns, links, text, new_key)

        # ---- tablet commit (per document) ----
        for pid, ctx in pages.items():
            if ctx.dirty:
                e = page_entries.get(pid)
                if e is not None and not Page.roundtrips(self.tree.read(e)):
                    # never rewrite a tablet page we can't reproduce byte for byte: its writing could be lost
                    self.state.event("warning", "A tablet page uses a format the service can't safely edit, so Zotero annotations weren't added to it.", att_key)
                    continue
                tablet_files[f"/{pid}.rm"] = ctx.page.to_bytes()
        content_changed = new_content != doc.content
        if tablet_files or content_changed:
            self.tree.stage(doc.id, files=tablet_files, content=new_content if content_changed else None)
            dropped = self.tree.commit()
            if doc.id in dropped:
                self._note_dropped(doc.id)
                return  # the tablet changed under us; redo next cycle, nothing written to Zotero
            rep.annotations_to_tablet += sum(1 for l in res.links.values() if l.origin == "zotero" and l.tablet_ids and l.zkey not in prior_links)

        # ---- Zotero writes ----
        if st.final_pdf is not None:
            filename = att.get("filename") or "document.pdf"
            md5, mtime = self.files.put(att_key, filename, st.final_pdf)
            w = self.zotero.write_items([{"key": att_key, "version": att["version"], "md5": md5, "mtime": mtime}])
            if att_key in w.versions:
                self.lib.apply_local_write([{**att, "md5": md5, "mtime": mtime, "version": w.versions[att_key]}])
            else:
                raise RuntimeError("Zotero rejected the updated file details; will retry")
        updates = {d["key"]: d for d in res.updates}
        for k, a in reindex.items():
            if k not in updates and k not in {d for d, _ in res.deletes}:
                updates[k] = a
        objects = [{**d, "parentItem": att_key, "version": 0} for d in res.creates] + [
            {k: v for k, v in d.items() if k not in ("dateModified", "dateAdded")} for d in updates.values()]
        written: dict[str, int] = {}
        failed: set[str] = set()
        if objects:
            w = self.zotero.write_items(objects)
            written.update(w.versions)
            failed |= set(w.failed) | w.conflicts
            for k, msg in w.failed.items():
                self.state.event("warning", f"Zotero refused an annotation: {msg}", att_key)
        deletes = res.deletes + orphan_deletes
        deleted_ok: set[str] = set()
        if deletes:
            w = self.zotero.delete_items(deletes)
            deleted_ok = {k for k, _ in deletes if k not in w.conflicts}
            failed |= w.conflicts
        rep.annotations_to_zotero += len([d for d in res.creates if d["key"] in written])

        # mirror our writes locally
        local = []
        for o in objects:
            if o["key"] in written:
                base = self.lib.items.get(o["key"], {})
                local.append({**base, **o, "itemType": "annotation", "version": written[o["key"]]})
        if local:
            self.lib.apply_local_write(local)
        if deleted_ok:
            self.lib.forget(sorted(deleted_ok))

        # links: confirmed ones take the new version; failed ones keep their prior state for a retry
        final_links: dict[str, Link] = {}
        for k, l in res.links.items():
            if k in failed:
                if k in prior_links:
                    final_links[k] = prior_links[k]
                elif l.origin == "tablet":
                    final_links[k] = Link(k, "tablet", l.page_id, l.tablet_ids, 0, l.zfp, l.tfp)  # create pending
                continue
            if k in written:
                l.zversion = written[k]
            final_links[k] = l
        self.state.replace_links(att_key, [l.to_row() for l in final_links.values()])

        # remember what was synced
        cur = self.tree.docs.get(doc.id, doc)
        final_hashes = {pid: e.hash for pid, e in cur.page_entries().items()}
        final_file = st.final_pdf or zfile
        att_now = self.lib.items.get(att_key, att)
        self.state.update_doc(
            att_key, pages=st.mapping, page_hashes=final_hashes, tablet_doc_hash=cur.hash, tablet_seen_hash=cur.hash,
            zotero_md5=att_now.get("md5"), zotero_file_hash=self.cache.put(final_file),
            tablet_pdf_hash=cur.file(".pdf").hash if cur.file(".pdf") else row["tablet_pdf_hash"],
            tablet_content_hash=cur.file(".content").hash if cur.file(".content") else row["tablet_content_hash"],
            zlib_version=self._zotero_sig(att_key), zotero_seen_sig=self._zotero_sig(att_key),
            last_synced=self.clock(), error=None, size=cur.size,
            last_opened=cur.metadata.get("lastOpened") or "", pinned=int(bool(cur.metadata.get("pinned"))),
        )

    # -- archive & storage -------------------------------------------------------------------------
    def _archive(self, settings: Settings, rep: Report) -> None:
        days = settings.archive_after_days
        now = self.clock()
        # bring back archived documents whose placeholder was opened or starred
        for row in self.state.docs("archived"):
            ph = self.tree.docs.get(row["placeholder_doc_id"] or "")
            opened = ph is not None and (ph.metadata.get("pinned") or (ph.metadata.get("lastOpened") or "") not in ("", "0"))
            if ph is None or opened or row["restore_requested"]:
                parent = ph.parent if ph is not None else ""
                if ph is not None:
                    self.tree.stage_remove(ph.id)
                want = planner.DesiredDoc(row["att_key"], row["att_key"], row["kind"], row["item_key"], row["name"], row["folder_key"], "")
                self._restore(row, want, parent or self._folder_doc(row["folder_key"]))
                self.state.update_doc(row["att_key"], placeholder_doc_id=None, tablet_changed_at=now)
        if days > 0:
            for row in self.state.docs("active"):
                doc = self.tree.docs.get(row["doc_id"])
                if doc is None or row["kind"] == "epub":
                    continue
                last = int(doc.metadata.get("lastOpened") or 0) / 1000 or (row["last_synced"] or now)
                in_sync = doc.hash == row["tablet_doc_hash"] and self._zotero_sig(row["att_key"]) == (row["zlib_version"] or 0)
                if now - last > days * 86400 and in_sync:
                    ph_id = str(uuid.uuid4())
                    self.tree.stage(ph_id, metadata=new_metadata(doc.name, doc.parent), content=tc.new_pdf_content(1), files={".pdf": placeholder_pdf(doc.name)})
                    self.tree.stage_remove(doc.id)
                    self.state.update_doc(row["att_key"], state="archived", placeholder_doc_id=ph_id)
        for doc_id in self.tree.commit():
            self._note_dropped(doc_id)

    def _storage_check(self, settings: Settings) -> None:
        total = sum(r["size"] or 0 for r in self.state.docs("active"))
        cap = settings.tablet_capacity_gb * 1024**3
        self.state.set("storage", {"used_bytes": total, "capacity_bytes": cap})
        if total > 0.9 * cap:
            self.state.event("warning", f"Synced documents use {total / 1024**3:.1f} GB of {settings.tablet_capacity_gb} GB. Exclude collections or enable archiving.")

    # -- actions from the plugin ---------------------------------------------------------------------
    def request_restore(self, att_key: str) -> bool:
        row = self.state.doc(att_key)
        if not row:
            return False
        self.state.update_doc(att_key, restore_requested=1)
        self.state.set(f"remove_pending:{att_key}", False)
        self.wakeup.set()
        return True

    def request_remove(self, att_key: str) -> bool:
        """Mark removed now; the next cycle moves it to the tablet's trash (after a final markup pull)."""
        row = self.state.doc(att_key)
        if not row or row["state"] != "active":
            return False
        self.state.update_doc(att_key, state="removed", restore_requested=0)
        self.state.set(f"remove_pending:{att_key}", True)
        self.wakeup.set()
        return True

    def _apply_removals(self, rep: Report) -> None:
        for row in self.state.docs("removed"):
            if not self.state.get(f"remove_pending:{row['att_key']}"):
                continue
            doc = self.tree.docs.get(row["doc_id"])
            if doc is not None and not self.tree.is_trashed(doc.id):
                if row["att_key"] in self.lib.items:
                    try:
                        self._sync_doc(row, force=True, rep=rep)
                    except Exception:
                        log.exception("final pull failed for %s", row["att_key"])
                doc = self.tree.docs.get(row["doc_id"])
                if doc is not None:
                    self.tree.stage(doc.id, metadata={**doc.metadata, "parent": TRASH})
            self.state.set(f"remove_pending:{row['att_key']}", False)
        for doc_id in self.tree.commit():
            self._note_dropped(doc_id)

    def set_home(self, item_key: str, collection_key: str | None) -> None:
        if collection_key:
            self.state.upsert("home_overrides", {"item_key": item_key, "collection_key": collection_key}, "item_key")
        else:
            self.state.exec("DELETE FROM home_overrides WHERE item_key=?", (item_key,))
        self.wakeup.set()

    def set_typography(self, source_key: str, params: dict | None) -> None:
        if params:
            self.state.upsert("typography", {"att_key": source_key, "params": json.dumps(params)}, "att_key")
        else:
            self.state.exec("DELETE FROM typography WHERE att_key=?", (source_key,))
        self.wakeup.set()
