"""Command line: run the service, register with rmfakecloud, and the two hardware checks."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import uuid
from pathlib import Path

from rmscene import scene_items as si

from .config import Config
from .state import State

log = logging.getLogger("rmzotero")


def _clients(cfg: Config):
    from .tablet.cloud import RmCloudClient
    from .zotero.client import ZoteroClient
    from .zotero.webdav import WebDAVFiles

    zotero = ZoteroClient(cfg.zotero_user_id, cfg.zotero_api_key)
    files = WebDAVFiles(cfg.webdav_url, cfg.webdav_username, cfg.webdav_password)
    cloud = RmCloudClient(cfg.rmfakecloud_url, cfg.data_dir / "device-token")
    return zotero, files, cloud


def _engine(cfg: Config):
    from .engine import Engine, Tools

    zotero, files, cloud = _clients(cfg)
    state = State(cfg.data_dir / "state.db")
    fonts = os.environ.get("RMZOTERO_FONT_PATHS", "")
    tools = Tools(font_paths=[p for p in fonts.split(":") if p], ocr_language=os.environ.get("RMZOTERO_OCR_LANGUAGE", "eng"))
    return Engine(cfg, state, zotero, files, cloud, tools=tools), cloud


def cmd_run(cfg: Config, args) -> int:
    from .api import Api
    from .listeners import rmcloud_notifications, run_forever, zotero_stream

    engine, cloud = _engine(cfg)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: (stop.set(), engine.wakeup.set()))
    signal.signal(signal.SIGINT, lambda *_: (stop.set(), engine.wakeup.set()))
    host, _, port = cfg.api_listen.rpartition(":")
    Api(engine, cfg.api_token).serve(host or "0.0.0.0", int(port))
    log.info("plugin API listening on %s", cfg.api_listen)
    if cfg.mcp_secret:
        from .claude.ops import LibraryOps
        from .claude.resolve import Resolver
        from .claude.server import serve as serve_mcp

        mhost, _, mport = cfg.mcp_listen.rpartition(":")
        ops = LibraryOps(engine, Resolver(cfg.translation_server_url, cfg.contact_email))
        _t, info = serve_mcp(ops, cfg.mcp_secret, mhost or "0.0.0.0", int(mport))
        log.info("Claude connector listening on %s at /mcp/%s…", cfg.mcp_listen, cfg.mcp_secret[:4])
    else:
        log.info("Claude connector disabled (set [claude] secret to enable)")
    zotero_stream(cfg.zotero_api_key, cfg.zotero_user_id, engine.wakeup, stop)
    rmcloud_notifications(cloud.notifications_url(), cloud.user_token, engine.wakeup, stop)
    run_forever(engine, cfg.poll_seconds, stop)
    return 0


def cmd_sync_once(cfg: Config, args) -> int:
    engine, _ = _engine(cfg)
    rep = engine.sync()
    print(json.dumps(rep.as_dict(), indent=2))
    return 1 if rep.errors else 0


def cmd_register(cfg: Config, args) -> int:
    _, _, cloud = _clients(cfg)
    cloud.register(args.code)
    root, gen = cloud.get_root()
    print(f"Registered with rmfakecloud (tree generation {gen}).")
    return 0


# -- check A: Zotero accepts annotations written through the web API -------------------------------

CHECK_TAG = "rmzotero-check"


def cmd_check_zotero(cfg: Config, args) -> int:
    from .zotero.client import new_key
    from .zotero.library import Library

    zotero, _files, _cloud = _clients(cfg)
    lib = Library(State(cfg.data_dir / "check.db"))
    lib.refresh(zotero)
    if args.cleanup:
        mine = [(k, it["version"]) for k, it in lib.items.items() if any(t.get("tag") == CHECK_TAG for t in it.get("tags") or [])]
        res = zotero.delete_items(mine)
        print(f"Deleted {len(mine) - len(res.conflicts)} check annotations.")
        return 0
    att = lib.items.get(args.attachment) if args.attachment else next(
        (it for it in lib.items.values() if it.get("itemType") == "attachment" and it.get("contentType") == "application/pdf" and not it.get("deleted")), None)
    if att is None:
        print("No PDF attachment found; pass --attachment KEY.")
        return 2
    hk, ik = new_key(), new_key()
    common = {"itemType": "annotation", "parentItem": att["key"], "annotationComment": "Written by rmzotero check-zotero",
              "annotationPageLabel": "1", "tags": [{"tag": CHECK_TAG}]}
    objs = [
        {**common, "key": hk, "version": 0, "annotationType": "highlight", "annotationText": "(check)", "annotationColor": "#5fb236",
         "annotationSortIndex": "00000|000000|00072", "annotationPosition": json.dumps({"pageIndex": 0, "rects": [[72, 700, 300, 714]]})},
        {**common, "key": ik, "version": 0, "annotationType": "ink", "annotationColor": "#ff6666", "annotationSortIndex": "00000|000000|00100",
         "annotationPosition": json.dumps({"pageIndex": 0, "width": 2, "paths": [[100, 600, 150, 650, 200, 600, 250, 650]]})},
    ]
    res = zotero.write_items(objs)
    print(f"Attachment: {att.get('title')} ({att['key']})")
    ok = True
    for o in objs:
        if o["key"] in res.versions:
            print(f"  ✓ {o['annotationType']} annotation {o['key']} accepted (version {res.versions[o['key']]})")
        else:
            ok = False
            print(f"  ✗ {o['annotationType']} annotation refused: {res.failed.get(o['key']) or 'conflict'}")
    lib.refresh(zotero)
    for o in objs:
        back = lib.items.get(o["key"])
        if back is not None:
            print(f"  ✓ read back {o['annotationType']}: page {json.loads(back['annotationPosition'])['pageIndex'] + 1}")
    if not ok:
        print("\nZotero refused the annotations; please share the lines above.")
        return 1
    print("\nNow open this PDF in Zotero (after it syncs). You should see a green highlight near the top of page 1 and a red zig-zag.")
    print("Check that you can recolour or delete them. Remove them afterwards with: rmzotero check-zotero --cleanup")
    return 0 if ok else 1


# -- check B: the tablet accepts pages with a Zotero layer written by the service ---------------------

CHECK_DOC_NAME = "rmzotero check"


def cmd_check_tablet(cfg: Config, args) -> int:
    from .pdf.simple import text_pdf
    from .tablet import content as tc
    from .tablet.scene import Page
    from .tablet.tree import BlobCache, CloudTree, new_metadata

    _z, _f, cloud = _clients(cfg)
    tree = CloudTree(cloud, BlobCache(cfg.data_dir / "blobs"))
    tree.mirror()
    existing = [d for d in tree.docs.values() if d.name == CHECK_DOC_NAME and not tree.is_trashed(d.id)]
    if args.verify:
        if not existing:
            print("No check document on the tablet; run check-tablet first.")
            return 2
        doc = existing[0]
        import datetime as _dt

        def when(ms):
            try:
                return _dt.datetime.fromtimestamp(int(ms) / 1000).isoformat(timespec="seconds") if ms else "never"
            except ValueError:
                return str(ms)

        print(f"Document: last modified {when(doc.metadata.get('lastModified'))}, last opened {when(doc.metadata.get('lastOpened'))}")
        listed = tc.all_pages(doc.content)
        files = doc.page_entries()
        print(f"Page list: {len(listed)} pages ({sum(1 for p in listed if p.deleted)} deleted); page files: {len(files)}")
        unlisted = [pid for pid in files if pid not in {p.id for p in listed}]
        if unlisted:
            print(f"  page files not in the page list: {len(unlisted)} (the tablet may have re-numbered pages)")
        zotero_ok = False
        user_items = 0
        for i, pid in enumerate([p.id for p in listed] + unlisted):
            e = files.get(pid)
            if e is None:
                print(f"  page {i + 1}: no writing")
                continue
            page = Page.parse(tree.read(e))
            marks = page.marks()
            z = [m for m in marks if m.on_zotero_layer]
            u = [m for m in marks if not m.on_zotero_layer]
            print(f"  page {i + 1}: layers {[label for _, label in page.layers()]}; Zotero layer {sum(m.is_stroke for m in z)} strokes + "
                  f"{sum(m.is_highlight for m in z)} highlights; your layers {len(u)} items")
            if sum(m.is_stroke for m in z) == 1 and sum(m.is_highlight for m in z) == 1:
                zotero_ok = True
            user_items += len(u)
        print(f"Service's Zotero layer intact: {'yes' if zotero_ok else 'NO'}")
        print(f"Your writing found: {'yes' if user_items >= 2 else 'not yet'} ({user_items} items on your layers; the service drew 1)")
        ok = zotero_ok and user_items >= 2
        if ok:
            print("✓ The tablet kept the service's page and your writing.")
        elif zotero_ok:
            print("… The service's page survived, but your drawing hasn't arrived. Check the tablet has synced (no pending-sync icon), then run this again.")
        else:
            print("✗ The service's layer was lost; please share the output above.")
        return 0 if ok else 1
    pdf = text_pdf([[("rmzotero tablet check", 20), ("Highlighted text written by the service sits on this line.", 12),
                     ("Draw anything on this page, let the tablet sync, then run: rmzotero check-tablet --verify", 11)],
                    [("Page two", 14)]])
    from .pdf.edit import page_boxes
    from .pdf.text import TextLayer, line_rects

    content = tc.new_pdf_content(2)
    pid = tc.pages(content)[0].id
    box = page_boxes(pdf)[0]
    chars = [c for c in TextLayer(pdf).chars(0)]
    start = "".join(c.text for c in chars).index("Highlighted")
    hl_rect = box.user_rect_to_rm(line_rects(chars[start:start + 11])[0])
    page = Page.empty()
    page.add(page.first_user_layer_id(), [si.Line(si.PenColor.BLACK, si.Pen.FINELINER_2,
                                                  [si.Point(x, 1500, 0, 0, 12, 100) for x in range(-400, 401, 40)], 1.0, 0.0)])
    zl = page.zotero_layer_id()
    page.add(zl, [
        si.Line(si.PenColor.RED, si.Pen.FINELINER_2, [si.Point(x, y, 0, 0, 12, 100) for x, y in [(-300, 600), (300, 600), (300, 900), (-300, 900), (-300, 600)]], 1.0, 0.0, color_rgba=(255, 102, 102, 255)),
        si.GlyphRange(None, 11, "Highlighted", si.PenColor.HIGHLIGHT, [si.Rectangle(*hl_rect)], color_rgba=(166, 230, 120, 255)),
    ])
    doc_id = existing[0].id if existing else str(uuid.uuid4())
    tree.stage(doc_id, metadata=new_metadata(CHECK_DOC_NAME, ""), content=content, files={".pdf": pdf, f"/{pid}.rm": page.to_bytes()})
    tree.commit()
    print(f"Uploaded '{CHECK_DOC_NAME}' to the top level of your tablet.")
    print("On the tablet: open it. Page 1 should show a black line (your layer), a red box and a green highlight (layer 'Zotero').")
    print("Draw something on page 1, wait for it to sync, then run: rmzotero check-tablet --verify")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rmzotero", description="Sync a Zotero library with a reMarkable tablet via rmfakecloud.")
    p.add_argument("--config", default=os.environ.get("RMZOTERO_CONFIG", "/config/rmzotero.toml"))
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the service (sync loop + plugin API)")
    sub.add_parser("sync-once", help="run a single sync cycle and print the report")
    r = sub.add_parser("register", help="register with rmfakecloud using a one-time code from its web UI")
    r.add_argument("--code", required=True)
    cz = sub.add_parser("check-zotero", help="check that Zotero accepts annotations written by the service")
    cz.add_argument("--attachment", help="PDF attachment key to annotate (default: the first PDF found)")
    cz.add_argument("--cleanup", action="store_true", help="delete the check annotations")
    ct = sub.add_parser("check-tablet", help="check that the tablet accepts pages written by the service")
    ct.add_argument("--verify", action="store_true", help="verify after drawing on the check document")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not args.verbose:
        for noisy in ("httpx", "httpcore", "pikepdf"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        cfg = Config.load(args.config)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.data_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except FileNotFoundError as e:
        print(f"rmzotero: {e.filename} not found. Put rmzotero.toml in the folder mounted at /config.", file=sys.stderr)
        return 2
    except PermissionError as e:
        print(f"rmzotero: permission denied for {e.filename} (running as uid {os.getuid()}, gid {os.getgid()}).\n"
              "Make the config readable and the data folder writable by that user, or set PUID/PGID "
              "in the container's environment to the owner of those files.", file=sys.stderr)
        return 2
    return {"run": cmd_run, "sync-once": cmd_sync_once, "register": cmd_register,
            "check-zotero": cmd_check_zotero, "check-tablet": cmd_check_tablet}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
