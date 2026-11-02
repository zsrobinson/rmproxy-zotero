# Annotations sync in both directions

Annotations made on the tablet appear as native Zotero annotations, and Zotero annotations appear on the tablet. This is deliberately chosen over the much simpler one-way (tablet → Zotero) design, despite requiring a persistent identity link between each Zotero annotation and its tablet counterpart, a conflict policy, and the ability to write the tablet's page format rather than only read it.

## Consequences

- Some annotation kinds have no counterpart on the other side (e.g. comments on Zotero highlights); each needs an explicit mapping or an explicit "not synced" rule.
- Writing to tablet pages races with the user writing on the same page; the sync must never silently drop the user's own strokes.
