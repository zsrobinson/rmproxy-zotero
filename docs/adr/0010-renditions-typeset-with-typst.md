# Renditions are typeset with Typst, not a browser engine

EPUB renditions are produced by converting the EPUB with pandoc and typesetting with Typst, using whole-paragraph ("optimized", Knuth–Plass) line breaking, hyphenation and justification in the spirit of InDesign's paragraph composer, with the tablet's own open fonts (Noto Sans/Sans UI/Serif/Mono, EB Garamond) at the reMarkable 2 page size. Browser engines break lines greedily and headless Chrome is heavy for a constrained NAS; Typst is a single small binary (also embeddable from Python) and fast.

## Considered Options

- **Headless Chrome**: best fidelity to publisher CSS, but greedy line breaking and high memory use.
- **WeasyPrint**: light and good paged-media CSS, but greedy line breaking.
- **LuaLaTeX via pandoc**: excellent microtypography, but a multi-gigabyte TeX install and slow builds.

## Consequences

Publisher CSS is not honoured: renditions look like our typesetting of the book's structure (headings, emphasis, lists, images, footnotes, tables), not the publisher's styling. Complex layouts (fixed-layout EPUBs, comics) are out of scope and fall back to send-as-is without annotation sync.
