# rmproxy-zotero

Keeps a Zotero library and a reMarkable tablet in step: Zotero's items travel to the tablet, and annotations made on either side appear on the other.

## Zotero side

**Library**:
The user's personal Zotero library: the source of truth for what papers and books exist and how they're organised.
_Avoid_: collection (when meaning the whole library), database

**Collection**:
A Zotero folder-like grouping of items; collections nest into subcollections.
_Avoid_: folder (reserve for the tablet)

**Item**:
A Zotero record of one work (paper, book, chapter) with its metadata.
_Avoid_: entry, reference, paper

**Attachment**:
A file belonging to an item, such as a PDF or EPUB.
_Avoid_: file (ambiguous), document (reserved for the tablet)

**Annotation**:
A single mark on an attachment as Zotero models it: a highlight, underline, note, text, image or ink annotation.
_Avoid_: markup, mark

## Tablet side

**Tablet**:
The user's reMarkable 2.
_Avoid_: device, reMarkable (ambiguous with the company and cloud)

**rmfakecloud**:
The self-hosted server that the tablet syncs with instead of reMarkable's own cloud.
_Avoid_: rmproxy, the cloud

**rmfakecloud-proxy**:
The small program on the tablet that redirects its cloud traffic to rmfakecloud.
_Avoid_: rmfakeproxy, rmproxy

**Document**:
A PDF or EPUB as it exists on the tablet, with its pages and whatever has been written on them.
_Avoid_: file, attachment (reserved for Zotero)

**Folder**:
A grouping of documents on the tablet.
_Avoid_: collection (reserved for Zotero)

**Stroke**:
One continuous pen, pencil, marker or highlighter line drawn on a tablet page.
_Avoid_: line, scribble

**Highlight** (tablet):
A text-snapped highlight the tablet records together with the text it covers.
_Avoid_: smart highlight

## Bridging

**Rendition**:
A PDF generated from an EPUB attachment, laid out to match the tablet's own ebook typography, which is what the tablet actually receives and is marked up.
_Avoid_: converted PDF, export
