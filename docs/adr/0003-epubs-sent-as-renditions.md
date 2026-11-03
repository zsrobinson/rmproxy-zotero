# EPUBs reach the tablet as PDF renditions we generate

_Renditions are now made only on request: see ADR 0016._

The tablet's own EPUB markup cannot be reliably read back or mapped to Zotero, so instead of sending EPUBs as-is we render each EPUB to a PDF ourselves, matching the tablet's ebook typography options (font, size, line spacing, margins) and page size. This gives EPUBs the same full annotation sync as PDFs, at the cost of reflow on the tablet.

The rendition is stored in Zotero as its own attachment beside the EPUB, and synced annotations live on the rendition. We do not map them back onto the EPUB. A rendition's typography is fixed once it has been written on: changing settings produces a new rendition alongside the old one, which keeps its annotations.

## Considered Options

- **Send EPUBs natively, read-only**: true reflow on the tablet, but no annotation sync.
- **Map rendition annotations back onto the EPUB (CFI positions)**: feasible since we control the layout, but large and not needed.
