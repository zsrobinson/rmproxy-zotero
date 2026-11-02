# EPUBs reach the tablet as PDF renditions we generate

The tablet's own EPUB markup cannot be reliably read back or mapped to Zotero, so instead of sending EPUBs as-is we render each EPUB to a PDF ourselves, matching the tablet's ebook typography options (font, size, line spacing, margins) and page size. This gives EPUBs the same full annotation sync as PDFs, at the cost of reflow on the tablet: changing typography means producing a new rendition rather than tweaking a setting.

## Considered Options

- **Send EPUBs natively, read-only**: true reflow on the tablet, but no annotation sync.
