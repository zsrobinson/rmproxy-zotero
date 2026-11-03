import assert from "node:assert/strict";
import { test } from "node:test";
import { collectionTree, columnText, editionLine, flatten, formatBytes, sectionModel } from "../src/view";
import { StatusCache } from "../src/status";
import type { EditionJob, ItemDetail } from "../src/api";

test("formatBytes", () => {
  assert.equal(formatBytes(0), "0 MB");
  assert.equal(formatBytes(2048), "2 KB");
  assert.equal(formatBytes(5.5 * 1024 ** 2), "5.5 MB");
  assert.equal(formatBytes(3 * 1024 ** 3), "3.0 GB");
});

test("collection tree nests, sorts and propagates exclusion", () => {
  const base = { documents: 1, bytes: 10, unknown_sizes: 0, excluded: false, path: [] as string[] };
  const nodes = flatten(collectionTree([
    { ...base, key: "B", name: "Zoo", parent: null, explicitly_excluded: false },
    { ...base, key: "A", name: "ML", parent: null, explicitly_excluded: true },
    { ...base, key: "C", name: "Transformers", parent: "A", explicitly_excluded: false },
  ]));
  assert.deepEqual(nodes.map((n) => [n.name, n.depth, n.inheritedExclusion]), [["ML", 0, false], ["Transformers", 1, true], ["Zoo", 0, false]]);
});

test("section model summarises attachments and home choices", () => {
  const d: ItemDetail = {
    item: { key: "ITEM0001", title: "Attention" },
    collections: [{ key: "C1", path: ["ML", "Transformers"] }, { key: "C2", path: ["Course"] }],
    home_override: "C2",
    attachments: [{ key: "ATT00001", title: "PDF", source_key: null, typography: null, state: "synced", name: "Vaswani - 2017 - Attention",
      location: "Zotero/Course", kind: "pdf", annotations: { from_tablet: 2, from_zotero: 1, zotero_only: 1 } }],
    pointers: [], events: [],
  };
  const m = sectionModel(d);
  assert.equal(m.summary, "On tablet · Zotero/Course");
  assert.equal(m.rows[0].annotations, "2 from tablet · 1 from Zotero · 1 Zotero-only (notes/comments)");
  assert.ok(m.rows[0].canRemove && !m.rows[0].canRestore);
  assert.equal(m.homeChoices.find((h) => h.selected)?.key, "C2");
  assert.equal(columnText("synced"), "✓");
  assert.equal(columnText(undefined), "");
});

test("status cache batches lookups and notifies", async () => {
  const calls: string[][] = [];
  let updates = 0;
  const pending: (() => void)[] = [];
  const cache = new StatusCache(async (keys) => (calls.push(keys), Object.fromEntries(keys.map((k) => [k, "synced"]))), () => updates++,
    (fn) => (pending.push(fn), 0 as any));
  assert.equal(cache.get("A"), undefined);
  assert.equal(cache.get("B"), undefined);
  assert.equal(pending.length, 1); // one scheduled flush for both
  await cache.flush();
  assert.deepEqual(calls, [["A", "B"]]);
  assert.equal(cache.get("A"), "synced");
  assert.equal(updates, 1);
});

test("EPUB rows offer a tablet edition and follow its progress", () => {
  const typo = { font: "EB Garamond", text_scale: 1, line_height: 150, margins: 125, alignment: "justify" };
  const epub = { key: "EPUB0001", title: "EPUB", source_key: null, typography: null, state: "not_synced" as const, epub: true,
    typography_effective: typo, edition: null as EditionJob | null };
  const detail = (attachments: any[]): ItemDetail => ({ item: { key: "BOOK0001", title: "Novel" }, collections: [], home_override: null,
    attachments, pointers: [], events: [] });
  let m = sectionModel(detail([epub]));
  assert.equal(m.summary, "EPUB: no tablet edition yet");
  assert.ok(m.rows[0].isEpub && !m.rows[0].hasEdition && !m.rows[0].editionBusy);
  assert.equal(m.rows[0].stateLabel, "Not on tablet until you make a tablet edition");
  assert.deepEqual(m.rows[0].typographyEffective, typo);

  const working: EditionJob = { key: "EPUB0001", status: "working", stage: "typesetting", progress: 30, message: null, result_key: null, finished_at: null };
  m = sectionModel(detail([{ ...epub, edition: working }]));
  assert.equal(m.summary, "Making tablet edition…");
  assert.ok(m.rows[0].editionBusy);

  const edition = { key: "ED000001", title: "Novel (tablet edition)", source_key: "EPUB0001", typography: null, state: "synced", kind: "rendition" };
  const failed: EditionJob = { ...working, status: "failed", progress: null, message: "Couldn't typeset this book: boom\nline 3" };
  m = sectionModel(detail([{ ...epub, edition: failed }, edition]));
  assert.ok(m.rows[0].hasEdition && !m.rows[0].editionBusy);
  assert.equal(m.rows[0].stateLabel, "Read on the tablet as its tablet edition");
  assert.equal(m.rows[0].error, failed.message);
  assert.ok(m.rows[1].isRendition);
  assert.equal(columnText("typesetting"), "⏳");
});

test("edition progress lines", () => {
  const base: EditionJob = { key: "E", status: "queued", stage: "waiting", progress: 0, message: null, result_key: null, finished_at: null };
  assert.deepEqual(editionLine("Novel", undefined), { text: "Novel: waiting…", percent: 0, finished: false, failed: false });
  assert.equal(editionLine("Novel", base).text, "Novel: waiting for the service…");
  assert.deepEqual(editionLine("Novel", { ...base, status: "working", stage: "typesetting", progress: 30 }),
    { text: "Novel: typesetting…", percent: 30, finished: false, failed: false });
  assert.deepEqual(editionLine("Novel", { ...base, status: "done", stage: "done", progress: 100, result_key: "K" }),
    { text: "Novel: ready", percent: 100, finished: true, failed: false });
  const f = editionLine("Novel", { ...base, status: "failed", progress: null, message: "Couldn't typeset this book: x\nmore" });
  assert.ok(f.failed && f.finished && f.text === "Novel: Couldn't typeset this book: x");
});
