// The plugin's client against the real Python API server (needs the service venv).
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { test } from "node:test";
import { ServiceClient, ServiceError, type Requester } from "../src/api";

const PY = process.env.SERVICE_PYTHON || "../service/.venv/bin/python";

const fetchRequester: Requester = async (method, url, headers, body) => {
  const r = await fetch(url, { method, headers, body: body ?? undefined });
  return { status: r.status, text: await r.text() };
};

test("plugin client speaks the service API", { skip: !existsSync(PY) }, async () => {
  const proc = spawn(PY, ["../service/tests/serve_fixture.py"], { stdio: ["ignore", "pipe", "inherit"] });
  try {
    const info: any = await new Promise((resolve, reject) => {
      proc.stdout.once("data", (d) => resolve(JSON.parse(String(d))));
      proc.once("exit", (c) => reject(new Error(`fixture exited ${c}`)));
    });
    const base = `http://127.0.0.1:${info.port}`;
    await assert.rejects(new ServiceClient(base, "wrong", fetchRequester).status(), (e: ServiceError) => e.status === 401);
    const c = new ServiceClient(base, "secret", fetchRequester);
    assert.equal((await c.health()).ok, true);
    const status = await c.itemsStatus([info.item, "NOTHERE1"]);
    assert.equal(status[info.item], "synced");
    const detail = await c.item(info.item);
    assert.equal(detail.attachments[0].location, "Zotero/Course");
    assert.equal(detail.attachments[0].annotations?.from_zotero, 1);
    assert.equal(detail.collections.length, 2);
    await c.setHome(info.item, info.tr);
    assert.equal((await c.item(info.item)).home_override, info.tr);
    const s = await c.saveSettings({ pointers: "none", excluded_collections: [info.course] });
    assert.equal(s.pointers, "none");
    const cols = await c.collections();
    assert.ok(cols.collections.find((x) => x.key === info.course)?.excluded);
    const proj = await c.projection([info.ml, info.course]);
    assert.equal(proj.documents, 0);
    assert.equal((await c.remove(info.att)).ok, true);
    assert.equal((await c.itemsStatus([info.item]))[info.item], "removed");
    assert.equal((await c.restore(info.att)).ok, true);
    assert.equal((await c.setTypography(info.att, { font: "Noto Serif" })).ok, true);
  } finally {
    proc.kill();
  }
});
