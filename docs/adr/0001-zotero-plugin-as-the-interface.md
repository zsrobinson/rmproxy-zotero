# Zotero itself is the interface, extended by a plugin

The goal is an interface "as similar to Zotero as possible", so we use real Zotero (7/8) with a bootstrapped plugin that adds reMarkable status, controls and views, rather than building a Zotero look-alike web app or extending rmfakecloud's web UI. This avoids maintaining a fork of Zotero's UI and keeps every existing Zotero workflow intact.

## Considered Options

- **Standalone web app** built from zotero/web-library + zotero/reader: browser-accessible from anywhere, but a permanent maintenance burden to track upstream. Kept as a possible later add-on.
- **Extending rmfakecloud's web UI**: closest to the tablet data, furthest from Zotero's interface.
