# Every synced attachment is its own tablet document; files never transition

Each PDF attachment of an item (and each EPUB's rendition) becomes its own tablet document. When a new version of a paper arrives (preprint → published), it is a new attachment and a new document; the old one and its markup stay, on both sides. We do not move markup from one file to another. When an item has several synced attachments, document names gain a suffix of the attachment's title (falling back to its filename when titles collide).

## Considered Options

- **Transition markup to the replacement file**, re-anchoring highlights by text: avoids duplicates, but ink cannot be re-anchored reliably and markup could be silently mangled.
- **Sync only the item's main attachment**: simpler names, but drops legitimate second copies.
