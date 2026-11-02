# Working assumptions

Unverified facts the design currently rests on. Each lists what was checked and the fallback if it proves false. Check these first when hardware and credentials are available.

| # | Assumption | Evidence so far | If false |
|---|---|---|---|
| A1 | Annotation items created through the Zotero web API (with the user's own key) appear in Zotero and stay editable. | Zotero's dataserver accepts the six annotation types with no third-party gate (source read); Zotero desktop syncs annotations through the same API. One 2026 forum report of failure. | The plugin writes annotations into the local Zotero database instead; they appear only while Zotero is running (ADR 0004 fallback). |
| A2 | The tablet loads v6 pages written by rmscene, including an extra "Zotero" layer, and keeps them when the user writes on the page. | Offline spike: rmscene wrote a page with two layers (a stroke on "Layer 1"; a stroke and text highlight on "Zotero"), read it back intact, and rmc rendered it. Not tried on a tablet. | Two-way sync narrows to tablet → Zotero; Zotero annotations reach the tablet only baked into the PDF. |
| A3 | rmfakecloud accepts a second sync client (the service) alongside the tablet, and the tablet picks up the service's changes. | rmfakecloud implements the device sync protocol; rmapi talks to it via `RMAPI_HOST`. | Fall back to rmfakecloud's upload route for new documents; page writes would need an rmfakecloud change. |
| A4 | The tablet's software version is supported by rmfakecloud (tested up to 3.27.1). | The user's existing setup syncs today. | Pin the tablet's version; do not update the tablet until checked. |
| A5 | The reMarkable 2 EPUB typography options are: fonts Noto Sans, Noto Sans UI, EB Garamond, Noto Mono, Noto Serif; text scale 0.7/0.8/1/1.2/1.5/2; line height 100/150/200; margins 50/125/200; alignment default/justify/left. | Code comments in rmapi-js (reverse-engineered). | Adjust the option list after reading a real document's `.content` file. |
| A6 | The reMarkable 2 page is 1404×1872 px at 226 DPI (about 157.8×210.4 mm), and PDF points map to device pixels at 226/72. | rmapi-js `devices.ts` and rmapi's annotation exporter. | Measure from a synced PDF's page metadata. |
| A7 | Zotero WebDAV storage is `zotero/<ITEMKEY>.zip` plus `zotero/<ITEMKEY>.prop` (`<properties version="1"><mtime>…</mtime><hash>…</hash></properties>`, mtime in ms). | Zotero `webdav.js` source. | — |
