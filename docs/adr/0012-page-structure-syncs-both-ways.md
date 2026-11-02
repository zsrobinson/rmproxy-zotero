# Page structure syncs both ways, like annotations

To the user, the Zotero attachment and the tablet document are one document, so page-structure edits made on either side are applied to the other rather than one side owning structure. Pages get a persistent identity across both sides; changes are detected as operations (insert, delete, rotate) and replayed on the other side. On the Zotero side this means editing the attachment's PDF in place (as Zotero itself does for rotate and delete) and shifting annotation page indices; on the tablet it means editing the document's page list, and transforming existing strokes when a page is rotated.

A note page inserted on the tablet becomes a blank page at the same position in the Zotero PDF, carrying its markup as annotations. When one side deletes a page that the other side annotated since the last sync, the page is kept and the user is warned: no markup is lost to a structural edit.

## Considered Options

- **The tablet owns page structure** and Zotero-side page edits are reverted: much simpler, but contradicts the "one document, two views" model the user expects.
