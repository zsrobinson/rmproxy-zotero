/* global Services */
// Zotero 7/8 bootstrapped plugin entry point; the logic lives in content/rmzotero.js (built from src/).
var RMZotero;

function install() {}

async function startup({ id, version, rootURI }) {
  Services.scriptloader.loadSubScript(rootURI + "content/rmzotero.js");
  await RMZotero.startup({ id, version, rootURI });
}

function onMainWindowLoad({ window }) {
  RMZotero?.onMainWindowLoad(window);
}

function onMainWindowUnload({ window }) {
  RMZotero?.onMainWindowUnload(window);
}

function shutdown() {
  RMZotero?.shutdown();
  RMZotero = undefined;
}

function uninstall() {}
