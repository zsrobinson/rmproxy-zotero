// Bundles src/ into the add-on and packages build/rmzotero.xpi. `--test` also builds the unit tests.
import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, rmSync, readdirSync } from "node:fs";

const test = process.argv.includes("--test");
rmSync("build", { recursive: true, force: true });
mkdirSync("build/addon", { recursive: true });
cpSync("addon", "build/addon", { recursive: true });

const common = { bundle: true, target: "firefox115", logLevel: "warning" };
await build({ ...common, entryPoints: ["src/main.ts"], outfile: "build/addon/content/rmzotero.js", format: "iife", globalName: "RMZotero" });
await build({ ...common, entryPoints: ["src/prefs.ts"], outfile: "build/addon/content/preferences.js", format: "iife" });

if (test) {
  const tests = readdirSync("test").filter((f) => f.endsWith(".test.ts")).map((f) => `test/${f}`);
  await build({ bundle: true, platform: "node", format: "esm", entryPoints: tests, outdir: "build/test", outExtension: { ".js": ".mjs" }, logLevel: "warning" });
} else {
  execFileSync("zip", ["-qr", "../rmzotero.xpi", "."], { cwd: "build/addon" });
  console.log("built build/rmzotero.xpi");
}
