# Tablet editions are made on request, not automatically

Supersedes the "automatic" part of ADR 0003: EPUBs still reach the tablet only as renditions (tablet editions), but the service no longer typesets every EPUB in scope by itself. The user asks for an edition from the plugin (right-click → *Make reMarkable edition*, or the item pane, where the book's text settings are chosen). The service makes it at the start of its next cycle and adds it to the item as an ordinary PDF attachment, which then syncs like any other PDF. An EPUB without an edition is simply not on the tablet.

Why: typesetting is the one step whose result is a judgement call (a book's structure can come out oddly), so the person should see it happen and be able to check the PDF in Zotero before reading it on the tablet. Automatic typesetting also spent minutes of every cycle on books that failed, and a failure was found in a log rather than where it was asked for.

How the waiting is handled: the request returns at once. The plugin shows a progress window that follows the job's stages (reading, typesetting, adding to Zotero) and ends with "ready" or the reason it failed, and the item list shows ⏳ meanwhile. A failed edition is not retried by itself; asking again retries it.

Making a new edition replaces the old one if it has no markup. If it has markup, the old one stays, as ADR 0003 says. Changing a book's text settings no longer re-typesets anything by itself; the settings are used for the next edition made.

## Considered Options

- **Automatic, with retries and backoff** (the previous behaviour): no action needed, but failures and odd formatting go unnoticed, and conversions slowed every sync.
- **A live preview while choosing settings**: catches formatting problems earlier, but the user preferred checking the finished PDF.
- **Typesetting inside Zotero** (Typst and pandoc as WebAssembly): no wait for the NAS, but a much heavier plugin.
