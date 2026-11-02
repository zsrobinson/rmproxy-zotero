// A lazily filled cache of per-item tablet status for the item list column.

import type { DocState } from "./api";

export class StatusCache {
  private states = new Map<string, DocState>();
  private wanted = new Set<string>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inflight = false;

  constructor(
    private fetchStatuses: (keys: string[]) => Promise<Record<string, DocState>>,
    private onUpdate: () => void,
    private schedule: (fn: () => void, ms: number) => ReturnType<typeof setTimeout> = setTimeout,
  ) {}

  /** Called while rendering a row: returns what we know and queues a fetch if we know nothing. */
  get(key: string): DocState | undefined {
    const s = this.states.get(key);
    if (s === undefined) this.want([key]);
    return s;
  }

  want(keys: string[]): void {
    for (const k of keys) this.wanted.add(k);
    if (this.timer === null) this.timer = this.schedule(() => void this.flush(), 300);
  }

  invalidate(keys?: string[]): void {
    if (!keys) {
      const all = [...this.states.keys()];
      this.states.clear();
      this.want(all);
    } else {
      for (const k of keys) this.states.delete(k);
      this.want(keys);
    }
  }

  async flush(): Promise<void> {
    this.timer = null;
    if (this.inflight || !this.wanted.size) return;
    const keys = [...this.wanted];
    this.wanted.clear();
    this.inflight = true;
    try {
      const res = await this.fetchStatuses(keys);
      for (const k of keys) this.states.set(k, res[k] ?? "not_synced");
      this.onUpdate();
    } catch {
      // service unreachable: show nothing rather than stale or wrong states
    } finally {
      this.inflight = false;
      if (this.wanted.size && this.timer === null) this.timer = this.schedule(() => void this.flush(), 300);
    }
  }
}
