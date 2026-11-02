# rmproxy-zotero

Keep a Zotero library and a reMarkable 2 in step, through your self-hosted rmfakecloud:

- your library's PDFs and EPUBs appear on the tablet, in folders that mirror your Zotero collections;
- what you write on the tablet shows up in Zotero as native annotations: highlights with their text, pen strokes as ink;
- what you annotate in Zotero shows up on the tablet, on its own "Zotero" layer;
- page edits made on either side (note pages inserted on the tablet; pages deleted or rotated in Zotero) carry across.

Primary use cases: **class readings and papers** (often scanned; a searchable copy is made automatically) and **books for personal reading** (EPUBs are typeset into tablet-sized PDFs with the tablet's own fonts and text settings).

Design notes: [GLOSSARY.md](GLOSSARY.md), decisions in [docs/adr/](docs/adr/), and the [working assumptions](docs/assumptions.md) that still need checking on real hardware.

**Status:** built and tested end to end against in-memory Zotero/WebDAV, a simulated tablet, and a real rmfakecloud (v0.0.32) in Docker. Not yet run against a real tablet, a real Zotero account, or inside Zotero itself; the two checks in step 3 below cover the riskiest of those.

## How it fits together

```
 Zotero desktop ── plugin ──HTTPS──┐
      │                            │
 zotero.org (data sync)            ▼
      ▲            ┌──────── rmzotero service (Docker, on your NAS) ────────┐
      │            │ Zotero web API ◄── sync engine ──► rmfakecloud (as a   │
 WebDAV share ◄────┤  WebDAV files        │              second device)     │
 (your files)      │                      ▼                                 │
                   │     state + exact copy of every tablet page            │
                   └────────────────────────────────────────────────────────┘
                                                   ▲
                                         reMarkable 2 + rmfakecloud-proxy
```

- **service/**: a Python service: the sync engine, a small HTTP API for the plugin, and command-line checks.
- **plugin/**: a Zotero 7/8 plugin that adds a reMarkable column, an item pane section and a settings pane.
- **deploy/**: an example config and a docker compose snippet.

## Setup

You need rmfakecloud already working with your tablet (yours is), Zotero syncing files to WebDAV (yours does), and a Zotero API key with write access to your personal library (zotero.org → Settings → Security → Create new private key). Your user ID is shown on the same page.

### 1. Deploy the service next to rmfakecloud

The image is built by GitHub Actions and published as `ghcr.io/zsrobinson/rmproxy-zotero` (amd64 and arm64). On the NAS, in the folder with your rmfakecloud `docker-compose.yml`:

```sh
mkdir -p rmzotero/config rmzotero/data
curl -L -o rmzotero/config/rmzotero.toml https://raw.githubusercontent.com/zsrobinson/rmproxy-zotero/main/deploy/rmzotero.example.toml
# edit rmzotero/config/rmzotero.toml: Zotero user id + key, WebDAV URL/login, a long random api.token
```

Add the service from [`deploy/docker-compose.snippet.yml`](deploy/docker-compose.snippet.yml) to your compose file, then:

```sh
docker compose pull rmzotero
```

If the repository is private, the image is too: run `docker login ghcr.io` on the NAS first, with your GitHub username and a token that has `read:packages`.

### 2. Register the service with rmfakecloud

In rmfakecloud's web UI, generate a one-time code (the same kind you used for the tablet), then:

```sh
docker compose run --rm rmzotero register --code <code>
```

### 3. Run the two checks (recommended before the first sync)

These answer the two open questions the design depends on (assumptions A1 and A2):

```sh
# Zotero accepts annotations written by the service (writes a highlight and a zig-zag on one PDF)
docker compose run --rm rmzotero check-zotero              # then look at that PDF in Zotero
docker compose run --rm rmzotero check-zotero --cleanup

# The tablet accepts pages with a "Zotero" layer written by the service
docker compose run --rm rmzotero check-tablet              # then open "rmzotero check" on the tablet and draw on page 1
docker compose run --rm rmzotero check-tablet --verify
```

If either fails, don't start the sync; the output tells you what was lost. `docs/assumptions.md` lists the fallback for each.

### 4. Start it

```sh
docker compose up -d rmzotero
docker compose logs -f rmzotero
```

To start small, set `excluded_collections` in the config (collection keys), or start with the plugin and untick collections before saving.

Expose port 8787 through your existing tunnel or reverse proxy **with HTTPS**, the same way you expose WebDAV.

### 5. Install the plugin

Download `rmzotero.xpi` from the [latest release](https://github.com/zsrobinson/rmproxy-zotero/releases/latest). In Zotero: Tools → Plugins → ⚙ → Install Plugin From File… → `rmzotero.xpi`. Zotero updates it automatically after that. Then Settings → reMarkable: enter the service address and token, press **Connect**, choose collections, **Save**.

### Updating

```sh
docker compose pull rmzotero && docker compose up -d rmzotero
```

## Using it

- **Item list**: right-click the column header → enable "reMarkable". ✓ on tablet · ↻ syncing · ⊘ removed on tablet · ☁ archived · ⚠ problem.
- **Item pane → reMarkable**: where the document lives on the tablet, annotation counts, *Send to tablet again* / *Remove from tablet*, the folder used when the item is in several collections, and text settings for EPUB tablet editions.
- **Right-click items**: Sync now, Send to reMarkable again, Remove from reMarkable.
- **On the tablet**: write and highlight as usual. Moves and renames are put back on the next sync, because Zotero owns the organisation. Deleting a document marks it "removed on tablet"; it stays in Zotero and can be sent back.
- **Timing**: a document syncs once it has been left alone for 2 minutes (`quiet_seconds`), so the service never fights you mid-page.

## What isn't synced (by design, for now)

- **Comments** on highlights and Zotero **notes** stay in Zotero (ADR 0008).
- **EPUB publisher styling** is replaced by our typesetting. Fixed-layout EPUBs are sent as-is without annotation sync, and DRM-protected EPUBs are skipped (ADR 0010).
- **A new version of a paper** is a new attachment and a new tablet document. Markup doesn't move between files (ADR 0011).
- **Reordering pages** isn't supported on either side.
- **Tablet software updates**: rmfakecloud is tested up to 3.27.1, and updates remove rmfakecloud-proxy. Check before updating.

## Development

GitHub Actions run the tests on every push (`ci.yml`), publish the Docker image from `main` (`docker.yml`), and publish the plugin when a version tag is pushed (`release.yml`):

```sh
git tag v0.2.0 && git push origin v0.2.0   # → release with rmzotero.xpi, and image tag 0.2.0
```

Locally:

```sh
cd service && python -m venv .venv && .venv/bin/pip install -e '.[test]' && .venv/bin/pytest
cd plugin && npm ci && npx tsc --noEmit && npm test && npm run build   # → build/rmzotero.xpi
```

The service tests run everything end to end against in-memory Zotero, WebDAV and rmfakecloud, plus a simulated tablet. `tests/test_rmfakecloud_live.py` runs the same flow against a real rmfakecloud:

```sh
docker run -d -p 3001:3000 -e JWT_SECRET_KEY=dev -e STORAGE_URL=http://localhost:3001 --name rmfc ddvk/rmfakecloud
docker exec rmfc /rmfakecloud-docker setuser -u tester -p testpass123 -a -s && docker restart rmfc
RMFAKECLOUD_TEST_URL=http://localhost:3001 RMFAKECLOUD_TEST_LOGIN=tester:testpass123 .venv/bin/pytest tests/test_rmfakecloud_live.py
```
