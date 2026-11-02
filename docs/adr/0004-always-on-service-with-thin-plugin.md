# Syncing runs in an always-on service beside rmfakecloud; the plugin is UI only

All sync work runs in a long-lived service deployed in the same docker compose stack as rmfakecloud on the user's NAS. It reads attachment files directly from the user's Zotero WebDAV share and reads/writes metadata and annotations through the Zotero web API. The Zotero plugin only shows status and settings and asks the service to act. This keeps sync running while Zotero is closed, at the cost of a second deployable and of depending on web-API annotation writes (to be proven by a spike; fallback is plugin-side writes into the local Zotero database).

## Considered Options

- **Sync engine inside the Zotero plugin**: one deployable and direct local-database access, but sync only happens while Zotero is open on some machine.
