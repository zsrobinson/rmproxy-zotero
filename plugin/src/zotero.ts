// Minimal declarations for the Zotero 7/8 globals the plugin uses.
/* eslint-disable @typescript-eslint/no-explicit-any */
declare global {
  const Zotero: any;
  const Services: any;
  const ChromeUtils: any;
  interface Window {
    MozXULElement?: any;
    ZoteroPane?: any;
  }
}

import type { Requester } from "./api";

export const PREF_URL = "extensions.rmzotero.serviceURL";
export const PREF_TOKEN = "extensions.rmzotero.token";

export const zoteroRequester: Requester = async (method, url, headers, body) => {
  const xhr = await Zotero.HTTP.request(method, url, {
    headers,
    body: body ?? undefined,
    successCodes: false, // let us see 4xx/5xx bodies
    timeout: 30000,
  });
  return { status: xhr.status, text: xhr.responseText ?? "" };
};

export function prefs(): { url: string; token: string } {
  return {
    url: (Zotero.Prefs.get(PREF_URL, true) as string) || "",
    token: (Zotero.Prefs.get(PREF_TOKEN, true) as string) || "",
  };
}

export function renameTemplate(): string {
  try {
    return (Zotero.Prefs.get("attachmentRenameTemplate") as string) || "";
  } catch {
    return "";
  }
}

/** setTimeout/setInterval aren't globals in the bootstrap scope; take them from Gecko's Timer module. */
export function timers(): { setTimeout: typeof setTimeout; setInterval: typeof setInterval; clearInterval: typeof clearInterval } {
  try {
    return ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
  } catch {
    return globalThis as any;
  }
}
