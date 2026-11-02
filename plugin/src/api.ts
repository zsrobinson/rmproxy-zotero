// Client for the rmzotero service's HTTP API.

export type DocState = "synced" | "pending" | "removed" | "archived" | "trashed" | "error" | "not_synced";

export interface EpubTypography {
  font: string;
  text_scale: number;
  line_height: number;
  margins: number;
  alignment: string;
}

export interface Settings {
  sync_root: string;
  excluded_collections: string[];
  pointers: "all" | "none" | "unrelated";
  ocr: boolean;
  archive_after_days: number;
  tablet_capacity_gb: number;
  filename_template: string;
  epub: EpubTypography;
}

export interface CollectionInfo {
  key: string;
  name: string;
  parent: string | null;
  path: string[];
  excluded: boolean;
  explicitly_excluded: boolean;
  documents: number;
  bytes: number;
  unknown_sizes: number;
}

export interface AttachmentInfo {
  key: string;
  title: string | null;
  source_key: string | null;
  typography: Partial<EpubTypography> | null;
  state: DocState;
  name?: string;
  location?: string | null;
  kind?: string;
  error?: string | null;
  last_synced?: number;
  annotations?: { from_tablet: number; from_zotero: number; zotero_only: number };
}

export interface ServiceEvent {
  ts: number;
  level: string;
  att_key: string | null;
  message: string;
}

export interface ItemDetail {
  item: { key: string; title: string };
  collections: { key: string; path: string[] }[];
  home_override: string | null;
  attachments: AttachmentInfo[];
  pointers: { collection: string; suppressed: boolean; location: string | null }[];
  events: ServiceEvent[];
}

export interface Status {
  version: string;
  last_report: { finished: number; errors: string[]; uploaded: number; synced_docs: number; pending: number } | null;
  storage: { used_bytes: number; capacity_bytes: number } | null;
  documents: Record<string, number>;
  events: ServiceEvent[];
}

export type Requester = (
  method: string,
  url: string,
  headers: Record<string, string>,
  body: string | null,
) => Promise<{ status: number; text: string }>;

export class ServiceError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export class ServiceClient {
  constructor(
    private baseURL: string,
    private token: string,
    private request: Requester,
  ) {
    this.baseURL = baseURL.replace(/\/+$/, "");
  }

  get configured(): boolean {
    return !!this.baseURL && !!this.token;
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    if (!this.configured) throw new ServiceError(0, "The rmzotero service isn't configured yet (Settings → reMarkable).");
    const headers: Record<string, string> = { Authorization: `Bearer ${this.token}` };
    let payload: string | null = null;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    let res;
    try {
      res = await this.request(method, `${this.baseURL}${path}`, headers, payload);
    } catch (e) {
      throw new ServiceError(0, `Can't reach the rmzotero service: ${(e as Error).message ?? e}`);
    }
    let data: any = {};
    try {
      data = res.text ? JSON.parse(res.text) : {};
    } catch {
      throw new ServiceError(res.status, `Unexpected response from the service (HTTP ${res.status})`);
    }
    if (res.status === 401) throw new ServiceError(401, "The service rejected the token. Check it in Settings → reMarkable.");
    if (res.status >= 400) throw new ServiceError(res.status, data.error || `HTTP ${res.status}`);
    return data as T;
  }

  health() {
    return this.call<{ ok: boolean; version: string }>("GET", "/api/health");
  }
  status() {
    return this.call<Status>("GET", "/api/status");
  }
  settings() {
    return this.call<Settings>("GET", "/api/settings");
  }
  saveSettings(patch: Partial<Settings>) {
    return this.call<Settings>("PUT", "/api/settings", patch);
  }
  collections() {
    return this.call<{ collections: CollectionInfo[]; storage: Status["storage"] }>("GET", "/api/collections");
  }
  projection(excluded: string[]) {
    return this.call<{ documents: number; bytes: number; unknown_sizes: number; capacity_bytes: number | null }>(
      "POST", "/api/collections/projection", { excluded_collections: excluded });
  }
  async itemsStatus(keys: string[]): Promise<Record<string, DocState>> {
    const out: Record<string, DocState> = {};
    for (let i = 0; i < keys.length; i += 500) {
      const res = await this.call<{ status: Record<string, DocState> }>("POST", "/api/items/status", { keys: keys.slice(i, i + 500) });
      Object.assign(out, res.status);
    }
    return out;
  }
  item(key: string) {
    return this.call<ItemDetail>("GET", `/api/items/${key}`);
  }
  setHome(itemKey: string, collection: string | null) {
    return this.call<{ ok: boolean }>("POST", `/api/items/${itemKey}/home`, { collection });
  }
  restore(attKey: string) {
    return this.call<{ ok: boolean }>("POST", `/api/attachments/${attKey}/restore`);
  }
  remove(attKey: string) {
    return this.call<{ ok: boolean }>("POST", `/api/attachments/${attKey}/remove`);
  }
  setTypography(sourceKey: string, typography: Partial<EpubTypography> | null) {
    return this.call<{ ok: boolean }>("PUT", `/api/attachments/${sourceKey}/typography`, typography ?? {});
  }
  syncNow() {
    return this.call<{ ok: boolean }>("POST", "/api/sync");
  }
}
