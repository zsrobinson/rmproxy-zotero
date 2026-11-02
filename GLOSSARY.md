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

**Comment**:
The free text a user attaches to a Zotero highlight or underline.
_Avoid_: note (a note is its own annotation type)

**Excluded Collection**:
A collection the user has chosen to keep off the tablet, along with its subcollections.
_Avoid_: ignored collection, blacklist

**Home Collection**:
The one collection whose folder holds an item's document on the tablet, when the item belongs to several collections.
_Avoid_: primary collection, main collection

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

**Sync Root**:
The tablet folder, chosen by the user, under which the library's collections are mirrored as folders.
_Avoid_: Zotero folder, mirror root

**Pointer**:
A one-page placeholder document left in the folder of each of an item's other collections, saying where on the tablet the real document lives.
_Avoid_: shortcut, alias, link

**Zotero Layer**:
The dedicated layer on a tablet page that holds annotations which originated in Zotero, kept apart from the user's own writing.
_Avoid_: overlay

**Stroke**:
One continuous pen, pencil, marker or highlighter line drawn on a tablet page.
_Avoid_: line, scribble

**Highlight** (tablet):
A text-snapped highlight the tablet records together with the text it covers.
_Avoid_: smart highlight

## Bridging

**Removed on Tablet**:
The state of an item whose document the user deleted on the tablet: it stays in its Zotero collections but is no longer sent to the tablet until restored.
_Avoid_: excluded (reserved for collections), deleted

**Rendition**:
A PDF generated from an EPUB attachment, laid out to match the tablet's own ebook typography, which is what the tablet actually receives and is marked up.
_Avoid_: converted PDF, export
