"""The MCP server claude.ai connects to (Streamable HTTP at /mcp/<secret>)."""

from __future__ import annotations

import json
import logging
import threading

import anyio
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .ops import Busy, LibraryOps, NotFound

log = logging.getLogger(__name__)

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
CHANGE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
CHANGE_OPEN = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
RECLASSIFY = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

INSTRUCTIONS = """\
This is the user's Zotero library, which also syncs to their reMarkable tablet.
Collections decide what is on the tablet: items in synced collections appear there automatically,
so organise the library rather than trying to "send" things. Highlights and handwriting made on the
tablet appear as Zotero annotations; use view_pages to see handwriting and drawings.
Ask the user how they want things organised before creating collections or filing many items.
"""


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


def build(ops: LibraryOps, secret: str) -> FastMCP:
    mcp = FastMCP(
        "rmzotero",
        instructions=INSTRUCTIONS,
        streamable_http_path=f"/mcp/{secret}",
        stateless_http=True,
        json_response=True,
        # the secret path is the credential; the server sits behind the user's own reverse proxy
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def call(fn, *args, **kw):
        try:
            return await anyio.to_thread.run_sync(lambda: fn(*args, **kw))
        except (NotFound, ValueError, Busy) as e:
            raise ValueError(str(e)) from e

    @mcp.tool(annotations=READ)
    async def search_library(query: str, collection: str | None = None, limit: int = 20) -> str:
        """Find items by words in their title, authors, year, publication, DOI, tags, abstract, or in the text and
        comments of their annotations. Optionally only within a collection (key) and its subcollections."""
        return _json(await call(ops.search_library, query, collection, limit))

    @mcp.tool(annotations=READ)
    async def get_item(key: str) -> str:
        """Full details of an item (or of the item an attachment/note belongs to): metadata, tags, collections,
        notes, attachments, and whether/where each attachment is on the tablet."""
        return _json(await call(ops.get_item, key))

    @mcp.tool(annotations=READ)
    async def list_collections() -> str:
        """All collections with their keys, full paths and item counts."""
        return _json(await call(ops.list_collections))

    @mcp.tool(annotations=READ)
    async def get_annotations(key: str) -> str:
        """Highlights, comments, notes and drawings on an item's attachments, in page order, with colour and whether
        they were made on the tablet or in Zotero. Pass a collection key to get them for every item in it."""
        return _json(await call(ops.get_annotations, key))

    @mcp.tool(annotations=READ)
    async def read_pages(key: str, pages: str | None = None) -> str:
        """The text of an item's PDF (or a specific attachment), e.g. pages="1-3" or "5,7". Up to 20 pages per call."""
        return _json(await call(ops.read_pages, key, pages))

    @mcp.tool(annotations=READ)
    async def view_pages(key: str, pages: str | None = None) -> list:
        """Images of PDF pages with the user's tablet handwriting, drawings and highlights drawn exactly as made
        (or Zotero's annotations for pages never written on at the tablet). Up to 4 pages per call, e.g. pages="2"."""
        images = await call(ops.view_pages, key, pages)
        out: list = []
        for label, png in images:
            out.append(f"Page {label}:")
            out.append(Image(data=png, format="png"))
        return out

    @mcp.tool(annotations=CHANGE_OPEN)
    async def add_items(queries: list[str], collections: list[str] | None = None, attach_pdfs: bool = True) -> str:
        """Add items to the library from DOIs, ISBNs, arXiv IDs, PMIDs or URLs of article/book pages, using Zotero's
        own translators. Existing items are reused (and filed into the given collections) instead of duplicated.
        Freely available PDFs are attached; the result says which items have no PDF. Collections are keys."""
        return _json(await call(ops.add_items, queries, collections, attach_pdfs))

    @mcp.tool(annotations=CHANGE)
    async def create_collection(name: str, parent: str | None = None) -> str:
        """Create a collection (optionally inside a parent collection key). Returns the existing one if the
        same name already exists in that place."""
        return _json(await call(ops.create_collection, name, parent))

    @mcp.tool(annotations=RECLASSIFY)
    async def file_items(item_keys: list[str], add_to: list[str] | None = None, remove_from: list[str] | None = None) -> str:
        """Add items to collections and/or remove them from collections (keys). Items are never deleted. What's on the
        tablet follows the collections, so removing an item from its synced collections takes it off the tablet."""
        return _json(await call(ops.file_items, item_keys, add_to, remove_from))

    @mcp.tool(annotations=CHANGE)
    async def add_note(item_key: str, text: str) -> str:
        """Attach a note to an item (plain text; blank lines separate paragraphs)."""
        return _json(await call(ops.add_note, item_key, text))

    @mcp.tool(annotations=RECLASSIFY)
    async def edit_item(item_key: str, fields: dict | None = None, add_tags: list[str] | None = None,
                        remove_tags: list[str] | None = None) -> str:
        """Change an item's metadata fields (Zotero field names, e.g. title, date, publicationTitle, DOI, abstractNote,
        creators) and add/remove tags. Collections are changed with file_items."""
        return _json(await call(ops.edit_item, item_key, fields, add_tags, remove_tags))

    return mcp


def serve(ops: LibraryOps, secret: str, host: str, port: int) -> tuple[threading.Thread, dict]:
    """Run the MCP server on its own thread; returns the thread and a dict filled with the bound port."""
    import uvicorn

    app = build(ops, secret).streamable_http_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    info: dict = {"server": server}

    def run() -> None:
        server.run()

    t = threading.Thread(target=run, name="mcp", daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            info["port"] = server.servers[0].sockets[0].getsockname()[1]
            break
        threading.Event().wait(0.05)
    return t, info
