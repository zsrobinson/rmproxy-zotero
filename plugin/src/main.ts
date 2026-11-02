// Plugin lifecycle: column, item pane section, context menu, preferences pane.

import { ServiceClient, type DocState, type ItemDetail } from "./api";
import { StatusCache } from "./status";
import { FONTS, LINE_HEIGHTS, MARGINS, TEXT_SCALES, columnText, sectionModel } from "./view";
import { prefs, renameTemplate, timers, zoteroRequester } from "./zotero";

const HTML = "http://www.w3.org/1999/xhtml";

let pluginID = "";
let rootURI = "";
let columnKey: string | false = false;
let sectionID: string | false = false;
let notifierID: string | null = null;
let refreshTimer: ReturnType<typeof setInterval> | null = null;
let cache: StatusCache;
const windowNodes = new WeakMap<Window, Element[]>();

export function client(): ServiceClient {
  const p = prefs();
  return new ServiceClient(p.url, p.token, zoteroRequester);
}

function itemKeysFor(items: any[]): string[] {
  return items.map((i) => i.key);
}

function selectedItems(win: Window): any[] {
  return win.ZoteroPane?.getSelectedItems?.() ?? [];
}

/** Synced attachment keys for a selection (items expand to their PDF/EPUB attachments). */
async function attachmentKeys(items: any[]): Promise<string[]> {
  const out: string[] = [];
  const c = client();
  for (const it of items) {
    const d: ItemDetail = await c.item(it.key);
    out.push(...d.attachments.map((a) => a.key));
  }
  return out;
}

function notify(win: Window, text: string): void {
  try {
    const pw = new Zotero.ProgressWindow({ closeOnClick: true });
    pw.changeHeadline("reMarkable");
    pw.addDescription(text);
    pw.show();
    pw.startCloseTimer(4000);
  } catch {
    win.alert?.(text);
  }
}

async function pushRenameTemplate(): Promise<void> {
  const c = client();
  if (!c.configured) return;
  try {
    const s = await c.settings();
    const t = renameTemplate();
    if (t && t !== s.filename_template) await c.saveSettings({ filename_template: t });
  } catch (e) {
    Zotero.debug(`rmzotero: couldn't sync rename template: ${e}`);
  }
}

// -- item pane section ---------------------------------------------------------------------------

function el(doc: Document, tag: string, attrs: Record<string, string> = {}, text?: string): HTMLElement {
  const e = doc.createElementNS(HTML, tag) as HTMLElement;
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}

function select(doc: Document, options: { value: string; label: string }[], value: string, onChange: (v: string) => void): HTMLSelectElement {
  const s = el(doc, "select") as HTMLSelectElement;
  for (const o of options) {
    const opt = el(doc, "option", { value: o.value }, o.label) as HTMLOptionElement;
    if (o.value === value) opt.selected = true;
    s.appendChild(opt);
  }
  s.addEventListener("change", () => onChange(s.value));
  return s;
}

async function renderSection(body: HTMLElement, item: any, setSummary: (s: string) => void): Promise<void> {
  const doc = body.ownerDocument;
  body.replaceChildren(el(doc, "div", { class: "rmz-muted" }, "Loading…"));
  const c = client();
  if (!c.configured) {
    body.replaceChildren(el(doc, "div", {}, "Set the service address and token in Settings → reMarkable."));
    setSummary("Not configured");
    return;
  }
  let detail: ItemDetail;
  try {
    detail = await c.item(item.key);
  } catch (e) {
    body.replaceChildren(el(doc, "div", { class: "rmz-error" }, String((e as Error).message ?? e)));
    setSummary("Service unreachable");
    return;
  }
  const m = sectionModel(detail);
  setSummary(m.summary);
  const root = el(doc, "div", { class: "rmz" });
  root.appendChild(el(doc, "style", {}, `
    .rmz { display: flex; flex-direction: column; gap: 8px; font-size: 12px; }
    .rmz-row { display: flex; flex-direction: column; gap: 3px; padding-bottom: 6px; border-bottom: 1px solid var(--fill-quinary, #ddd); }
    .rmz-title { font-weight: 600; }
    .rmz-muted { color: var(--fill-secondary, #777); }
    .rmz-error { color: #c0392b; }
    .rmz-actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .rmz-grid { display: grid; grid-template-columns: auto 1fr; gap: 3px 8px; align-items: center; }
  `));
  if (!m.rows.length) root.appendChild(el(doc, "div", { class: "rmz-muted" }, "No PDF or EPUB to send."));
  const rerender = () => void renderSection(body, item, setSummary);
  for (const r of m.rows) {
    const row = el(doc, "div", { class: "rmz-row" });
    row.appendChild(el(doc, "div", { class: "rmz-title" }, r.title));
    row.appendChild(el(doc, "div", {}, r.stateLabel + (r.location ? ` — ${r.location}` : "")));
    if (r.annotations) row.appendChild(el(doc, "div", { class: "rmz-muted" }, r.annotations));
    if (r.error) row.appendChild(el(doc, "div", { class: "rmz-error" }, r.error));
    const actions = el(doc, "div", { class: "rmz-actions" });
    const button = (label: string, fn: () => Promise<unknown>) => {
      const b = el(doc, "button", {}, label);
      b.addEventListener("click", async () => {
        b.setAttribute("disabled", "true");
        try {
          await fn();
          cache.invalidate([item.key]);
        } catch (e) {
          notify(doc.defaultView as Window, String((e as Error).message ?? e));
        }
        rerender();
      });
      actions.appendChild(b);
    };
    if (r.canRestore) button("Send to tablet again", () => c.restore(r.key));
    if (r.canRemove) button("Remove from tablet", () => c.remove(r.key));
    if (actions.childElementCount) row.appendChild(actions);
    if (r.isRendition && r.sourceKey) {
      const t = r.typography ?? {};
      const grid = el(doc, "div", { class: "rmz-grid" });
      const save = (patch: Record<string, unknown>) => void c.setTypography(r.sourceKey!, { ...t, ...patch }).then(rerender);
      grid.appendChild(el(doc, "span", { class: "rmz-muted" }, "Font"));
      grid.appendChild(select(doc, [{ value: "", label: "Default" }, ...FONTS.map((f) => ({ value: f, label: f }))], t.font ?? "", (v) => save({ font: v || undefined })));
      grid.appendChild(el(doc, "span", { class: "rmz-muted" }, "Size"));
      grid.appendChild(select(doc, [{ value: "", label: "Default" }, ...TEXT_SCALES.map((s) => ({ value: String(s), label: `${s}×` }))], t.text_scale ? String(t.text_scale) : "", (v) => save({ text_scale: v ? Number(v) : undefined })));
      grid.appendChild(el(doc, "span", { class: "rmz-muted" }, "Line spacing"));
      grid.appendChild(select(doc, [{ value: "", label: "Default" }, ...LINE_HEIGHTS.map((s) => ({ value: String(s), label: `${s}%` }))], t.line_height ? String(t.line_height) : "", (v) => save({ line_height: v ? Number(v) : undefined })));
      grid.appendChild(el(doc, "span", { class: "rmz-muted" }, "Margins"));
      grid.appendChild(select(doc, [{ value: "", label: "Default" }, ...MARGINS.map((s) => ({ value: String(s), label: String(s) }))], t.margins ? String(t.margins) : "", (v) => save({ margins: v ? Number(v) : undefined })));
      grid.appendChild(el(doc, "span", { class: "rmz-muted" }, "Alignment"));
      grid.appendChild(select(doc, [{ value: "", label: "Default" }, { value: "justify", label: "Justified" }, { value: "left", label: "Left" }], t.alignment ?? "", (v) => save({ alignment: v || undefined })));
      row.appendChild(el(doc, "div", { class: "rmz-muted" }, "Tablet edition text settings (a new edition is made if this one has markup):"));
      row.appendChild(grid);
    }
    root.appendChild(row);
  }
  if (m.showHome) {
    const home = el(doc, "div", { class: "rmz-grid" });
    home.appendChild(el(doc, "span", { class: "rmz-muted" }, "Folder on tablet"));
    home.appendChild(select(doc, m.homeChoices.map((h) => ({ value: h.key, label: h.label })),
      m.homeChoices.find((h) => h.selected)?.key ?? "", (v) => void c.setHome(detail.item.key, v || null).then(rerender)));
    root.appendChild(home);
  }
  for (const e of m.events) root.appendChild(el(doc, "div", { class: "rmz-muted" }, e));
  body.replaceChildren(root);
}

// -- context menu -----------------------------------------------------------------------------------

function addMenu(win: Window): void {
  const doc = win.document;
  const menu = doc.getElementById("zotero-itemmenu");
  if (!menu) return;
  const nodes: Element[] = [];
  const sep = (doc as any).createXULElement("menuseparator");
  menu.appendChild(sep);
  nodes.push(sep);
  const item = (label: string, fn: () => Promise<void>) => {
    const mi = (doc as any).createXULElement("menuitem");
    mi.setAttribute("label", label);
    mi.addEventListener("command", () => void fn().catch((e: Error) => notify(win, e.message ?? String(e))));
    menu.appendChild(mi);
    nodes.push(mi);
  };
  item("Sync with reMarkable now", async () => {
    await client().syncNow();
    notify(win, "Sync started.");
  });
  item("Send to reMarkable again", async () => {
    const items = selectedItems(win);
    for (const k of await attachmentKeys(items)) await client().restore(k);
    cache.invalidate(itemKeysFor(items));
    notify(win, "Will be sent at the next sync.");
  });
  item("Remove from reMarkable", async () => {
    const items = selectedItems(win);
    for (const k of await attachmentKeys(items)) await client().remove(k);
    cache.invalidate(itemKeysFor(items));
    notify(win, "Moved to the tablet's trash. The items stay in Zotero.");
  });
  windowNodes.set(win, nodes);
}

// -- lifecycle ----------------------------------------------------------------------------------------

export async function startup({ id, rootURI: root }: { id: string; version: string; rootURI: string }): Promise<void> {
  pluginID = id;
  rootURI = root;
  const T = timers();
  cache = new StatusCache(
    (keys) => client().itemsStatus(keys),
    () => Zotero.ItemTreeManager.refreshColumns?.(),
    (fn, ms) => T.setTimeout(fn, ms),
  );

  Zotero.PreferencePanes.register({
    pluginID,
    src: rootURI + "content/preferences.xhtml",
    scripts: [rootURI + "content/preferences.js"],
    label: "reMarkable",
    image: rootURI + "content/icon.svg",
  });

  columnKey = await Zotero.ItemTreeManager.registerColumn({
    dataKey: "rmzoteroStatus",
    label: "reMarkable",
    pluginID,
    width: "40",
    fixedWidth: false,
    zoteroPersist: ["width", "hidden", "sortDirection"],
    dataProvider: (item: any) => {
      if (!client().configured) return "";
      if (!(item.isRegularItem?.() || item.isAttachment?.())) return "";
      return columnText(cache.get(item.key) as DocState | undefined);
    },
  });

  sectionID = Zotero.ItemPaneManager.registerSection({
    paneID: "rmzotero-section",
    pluginID,
    header: { l10nID: "rmzotero-section-header", icon: rootURI + "content/icon.svg" },
    sidenav: { l10nID: "rmzotero-section-sidenav", icon: rootURI + "content/icon.svg" },
    onItemChange: ({ item, setEnabled, tabType }: any) => {
      setEnabled(tabType === "library" && !!item && (item.isRegularItem() || item.isAttachment()));
      return true;
    },
    onRender: ({ body }: any) => {
      body.textContent = "";
    },
    onAsyncRender: async ({ body, item, setSectionSummary }: any) => {
      await renderSection(body, item, setSectionSummary);
    },
  });

  notifierID = Zotero.Notifier.registerObserver(
    {
      notify: (event: string, type: string, ids: number[]) => {
        if (type !== "item" || !["add", "modify", "trash", "delete"].includes(event)) return;
        const keys = ids.map((id) => Zotero.Items.get(id)).filter(Boolean).map((i: any) => i.parentKey || i.key);
        if (keys.length) timers().setTimeout(() => cache.invalidate(keys), 5000);
      },
    },
    ["item"],
    "rmzotero",
  );
  refreshTimer = timers().setInterval(() => cache.invalidate(), 2 * 60 * 1000);
  Zotero.debug(`rmzotero: started (column ${columnKey || "not registered"}, section ${sectionID || "not registered"})`);
  await pushRenameTemplate();
}

export function onMainWindowLoad(win: Window): void {
  win.MozXULElement?.insertFTLIfNeeded("rmzotero.ftl");
  addMenu(win);
  Zotero.debug(`rmzotero: window ready (${windowNodes.get(win)?.length ?? 0} menu entries)`);
}

export function onMainWindowUnload(win: Window): void {
  for (const n of windowNodes.get(win) ?? []) n.remove();
  windowNodes.delete(win);
}

export function shutdown(): void {
  if (columnKey) Zotero.ItemTreeManager.unregisterColumn(columnKey);
  if (sectionID) Zotero.ItemPaneManager.unregisterSection(sectionID);
  if (notifierID) Zotero.Notifier.unregisterObserver(notifierID);
  if (refreshTimer) timers().clearInterval(refreshTimer);
  for (const win of Zotero.getMainWindows?.() ?? []) onMainWindowUnload(win);
}
