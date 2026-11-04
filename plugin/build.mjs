// Bundles src/ into the add-on and packages build/rmzotero.xpi plus build/updates.json.
//   node build.mjs            build the .xpi (version from PLUGIN_VERSION, else package.json)
//   node build.mjs --test     also build the unit tests
import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, rmSync, readdirSync, readFileSync, writeFileSync } from "node:fs";

const REPO = "zsrobinson/rmzotero";
const ID = "rmzotero@zsrobinson.com";
const test = process.argv.includes("--test");
const version = (process.env.PLUGIN_VERSION || JSON.parse(readFileSync("package.json", "utf8")).version).replace(/^v/, "");

rmSync("build", { recursive: true, force: true });
mkdirSync("build/addon", { recursive: true });
cpSync("addon", "build/addon", { recursive: true });

const manifest = JSON.parse(readFileSync("addon/manifest.json", "utf8"));
manifest.version = version;
// Zotero checks this for new versions: the updates.json attached to the latest GitHub release.
manifest.applications.zotero.update_url = `https://github.com/${REPO}/releases/latest/download/updates.json`;
writeFileSync("build/addon/manifest.json", JSON.stringify(manifest, null, 2));

const common = { bundle: true, target: "firefox115", logLevel: "warning" };
await build({ ...common, entryPoints: ["src/main.ts"], outfile: "build/addon/content/rmzotero.js", format: "iife", globalName: "RMZotero" });
await build({ ...common, entryPoints: ["src/prefs.ts"], outfile: "build/addon/content/preferences.js", format: "iife" });

if (test) {
  const tests = readdirSync("test").filter((f) => f.endsWith(".test.ts")).map((f) => `test/${f}`);
  await build({ bundle: true, platform: "node", format: "esm", entryPoints: tests, outdir: "build/test", outExtension: { ".js": ".mjs" }, logLevel: "warning" });
} else {
  execFileSync("zip", ["-qr", "../rmzotero.xpi", "."], { cwd: "build/addon" });
  const updates = {
    addons: {
      [ID]: {
        updates: [{
          version,
          update_link: `https://github.com/${REPO}/releases/download/v${version}/rmzotero.xpi`,
          applications: { zotero: { strict_min_version: "6.999" } },
        }],
      },
    },
  };
  writeFileSync("build/updates.json", JSON.stringify(updates, null, 2));
  console.log(`built build/rmzotero.xpi (version ${version})`);
}
