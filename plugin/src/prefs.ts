// Settings pane (Zotero Settings → reMarkable).

import { ServiceClient, type CollectionInfo, type Settings } from "./api";
import { FONTS, LINE_HEIGHTS, MARGINS, TEXT_SCALES, collectionLabel, collectionTree, flatten, formatBytes } from "./view";
import { PREF_TOKEN, PREF_URL, prefs, renameTemplate, zoteroRequester } from "./zotero";

const HTML = "http://www.w3.org/1999/xhtml";

function $(id: string): any {
  return document.getElementById(id);
}

function client(): ServiceClient {
  const p = prefs();
  return new ServiceClient(p.url, p.token, zoteroRequester);
}

function setStatus(text: string, error = false): void {
  const s = $("rmz-status");
  s.textContent = text;
  s.style.color = error ? "#c0392b" : "";
}

function fillSelect(id: string, options: [string, string][], value: string): void {
  const s = $(id);
  s.replaceChildren();
  for (const [v, label] of options) {
    const o = document.createElementNS(HTML, "option") as HTMLOptionElement;
    o.value = v;
    o.textContent = label;
    if (v === value) o.selected = true;
    s.appendChild(o);
  }
}

let current: Settings | null = null;
let excluded = new Set<string>();
let collections: CollectionInfo[] = [];

async function updateProjection(): Promise<void> {
  try {
    const p = await client().projection([...excluded]);
    const cap = p.capacity_bytes ? ` of ${formatBytes(p.capacity_bytes)}` : "";
    $("rmz-projection").textContent = `On the tablet: ${p.documents} documents, ${formatBytes(p.bytes)}${p.unknown_sizes ? " + not yet measured" : ""}${cap}.`;
  } catch {
    $("rmz-projection").textContent = "";
  }
}

function renderCollections(): void {
  const box = $("rmz-collections");
  box.replaceChildren();
  for (const n of flatten(collectionTree(collections))) {
    const label = document.createElementNS(HTML, "label") as HTMLLabelElement;
    label.style.display = "block";
    label.style.paddingLeft = `${n.depth * 16}px`;
    const cb = document.createElementNS(HTML, "input") as HTMLInputElement;
    cb.type = "checkbox";
    cb.checked = !excluded.has(n.key) && !n.inheritedExclusion;
    cb.disabled = n.inheritedExclusion;
    cb.addEventListener("change", () => {
      if (cb.checked) excluded.delete(n.key);
      else excluded.add(n.key);
      for (const c of collections) c.explicitly_excluded = excluded.has(c.key);
      renderCollections();
      void updateProjection();
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + collectionLabel(n)));
    box.appendChild(label);
  }
}

async function load(): Promise<void> {
  const p = prefs();
  $("rmz-url").value = p.url;
  $("rmz-token").value = p.token;
  if (!p.url || !p.token) {
    setStatus("Enter the service address and token, then press Connect.");
    return;
  }
  try {
    const c = client();
    const [health, s, cols, status] = await Promise.all([c.health(), c.settings(), c.collections(), c.status()]);
    current = s;
    collections = cols.collections;
    excluded = new Set(s.excluded_collections);
    const last = status.last_report?.finished ? new Date(status.last_report.finished * 1000).toLocaleString() : "never";
    const docs = Object.entries(status.documents).map(([k, v]) => `${v} ${k}`).join(", ") || "none yet";
    setStatus(`Connected to rmzotero ${health.version}. Last sync: ${last}. Documents: ${docs}.`);
    $("rmz-root").value = s.sync_root;
    fillSelect("rmz-pointers", [["unrelated", "Only in unrelated collections"], ["all", "In every other collection"], ["none", "Never"]], s.pointers);
    $("rmz-ocr").checked = s.ocr;
    $("rmz-archive").value = String(s.archive_after_days);
    $("rmz-capacity").value = String(s.tablet_capacity_gb);
    fillSelect("rmz-font", FONTS.map((f) => [f, f]), s.epub.font);
    fillSelect("rmz-scale", TEXT_SCALES.map((v) => [String(v), `${v}×`]), String(s.epub.text_scale));
    fillSelect("rmz-leading", LINE_HEIGHTS.map((v) => [String(v), `${v}%`]), String(s.epub.line_height));
    fillSelect("rmz-margins", MARGINS.map((v) => [String(v), String(v)]), String(s.epub.margins));
    fillSelect("rmz-align", [["justify", "Justified"], ["left", "Left"]], s.epub.alignment);
    $("rmz-events").textContent = status.events.slice(0, 8).map((e) => `• ${e.message}`).join("\n");
    $("rmz-settings").hidden = false;
    renderCollections();
    void updateProjection();
  } catch (e) {
    setStatus(String((e as Error).message ?? e), true);
  }
}

async function save(): Promise<void> {
  if (!current) return;
  const patch: Partial<Settings> = {
    sync_root: $("rmz-root").value.trim(),
    pointers: $("rmz-pointers").value,
    ocr: $("rmz-ocr").checked,
    archive_after_days: Number($("rmz-archive").value) || 0,
    tablet_capacity_gb: Number($("rmz-capacity").value) || 8,
    excluded_collections: [...excluded],
    filename_template: renameTemplate() || current.filename_template,
    epub: {
      font: $("rmz-font").value,
      text_scale: Number($("rmz-scale").value),
      line_height: Number($("rmz-leading").value),
      margins: Number($("rmz-margins").value),
      alignment: $("rmz-align").value,
    },
  };
  try {
    current = await client().saveSettings(patch);
    setStatus("Saved. Changes apply at the next sync (starting now).");
    await client().syncNow();
  } catch (e) {
    setStatus(String((e as Error).message ?? e), true);
  }
}

function init(): void {
  if (!$("rmz-connect")) return void setTimeout(init, 50);
  $("rmz-connect").addEventListener("click", () => {
    Zotero.Prefs.set(PREF_URL, $("rmz-url").value.trim(), true);
    Zotero.Prefs.set(PREF_TOKEN, $("rmz-token").value.trim(), true);
    void load();
  });
  $("rmz-save").addEventListener("click", () => void save());
  $("rmz-sync").addEventListener("click", () => void client().syncNow().then(() => setStatus("Sync started.")).catch((e: Error) => setStatus(e.message, true)));
  void load();
}

init();
