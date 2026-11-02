import assert from "node:assert/strict";
import { test } from "node:test";
import { collectionTree, columnText, flatten, formatBytes, sectionModel } from "../src/view";
import { StatusCache } from "../src/status";
import type { ItemDetail } from "../src/api";

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
