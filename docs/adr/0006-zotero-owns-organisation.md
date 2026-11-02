# Zotero owns organisation; the tablet mirrors it

Only annotations sync both ways. Folders, document names and placement on the tablet are derived from Zotero collections under a user-chosen sync root, and moves or renames made on the tablet are reverted on the next sync. Deleting a document on the tablet marks the item "removed on tablet" (restorable from the plugin) and never changes Zotero's collections. Documents created on the tablet are not imported.

A tablet document can sit in only one folder, so an item in several collections lives in its home collection's folder: the deepest when one collection contains another, otherwise the first by full path, overridable per item. Pointers may be left in the other collections' folders; the user chooses all, none, or only collections that are not ancestors/descendants of the home collection (the default, since a pointer in a parent folder is rarely useful).
