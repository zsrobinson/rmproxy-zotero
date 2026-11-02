// Pure view logic, shared by the column, the item pane section and the settings pane (unit tested).

import type { AttachmentInfo, CollectionInfo, DocState, EpubTypography, ItemDetail } from "./api";

export const STATE_LABEL: Record<DocState, string> = {
  synced: "On tablet",
  pending: "Syncing…",
  removed: "Removed on tablet",
  archived: "Archived",
  trashed: "Not in scope (in tablet trash)",
  error: "Problem",
  not_synced: "Not on tablet",
};

// Compact glyphs for the item list column.
export const STATE_GLYPH: Record<DocState, string> = {
  synced: "✓",
  pending: "↻",
  removed: "⊘",
  archived: "☁",
  trashed: "",
  error: "⚠",
  not_synced: "",
};

export function columnText(state: DocState | undefined): string {
  if (!state) return "";
  return STATE_GLYPH[state] ?? "";
}

export function formatBytes(n: number): string {
  if (!n) return "0 MB";
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(n < 10 * 1024 ** 2 ? 1 : 0)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

export interface CollectionNode extends CollectionInfo {
  depth: number;
  children: CollectionNode[];
  inheritedExclusion: boolean; // excluded because an ancestor is
}

/** Nest the flat collection list for the excluded-collections picker. */
export function collectionTree(list: CollectionInfo[]): CollectionNode[] {
  const nodes = new Map<string, CollectionNode>();
  for (const c of list) nodes.set(c.key, { ...c, depth: 0, children: [], inheritedExclusion: false });
  const roots: CollectionNode[] = [];
  for (const n of nodes.values()) {
    const parent = n.parent ? nodes.get(n.parent) : undefined;
    if (parent) parent.children.push(n);
    else roots.push(n);
  }
  const sort = (xs: CollectionNode[]) => xs.sort((a, b) => a.name.localeCompare(b.name));
  const walk = (xs: CollectionNode[], depth: number, inherited: boolean) => {
    sort(xs);
    for (const x of xs) {
      x.depth = depth;
      x.inheritedExclusion = inherited;
      walk(x.children, depth + 1, inherited || x.explicitly_excluded);
    }
  };
  walk(roots, 0, false);
  return roots;
}

export function flatten(nodes: CollectionNode[]): CollectionNode[] {
  const out: CollectionNode[] = [];
  const walk = (xs: CollectionNode[]) => xs.forEach((x) => (out.push(x), walk(x.children)));
  walk(nodes);
  return out;
}

export function collectionLabel(n: CollectionNode): string {
  const size = n.documents ? `${n.documents} doc${n.documents === 1 ? "" : "s"}, ${formatBytes(n.bytes)}${n.unknown_sizes ? "+" : ""}` : "empty";
  return `${n.name} (${size})`;
}

export interface SectionModel {
  summary: string;
  rows: {
    key: string;
    title: string;
    stateLabel: string;
    state: DocState;
    location: string | null;
    annotations: string | null;
    error: string | null;
    canRestore: boolean;
    canRemove: boolean;
    isRendition: boolean;
    sourceKey: string | null;
    typography: Partial<EpubTypography> | null;
  }[];
  homeChoices: { key: string; label: string; selected: boolean }[];
  showHome: boolean;
  events: string[];
}

export function sectionModel(d: ItemDetail): SectionModel {
  const rows = d.attachments.map((a: AttachmentInfo) => {
    const ann = a.annotations;
    const annotations = ann
      ? [ann.from_tablet && `${ann.from_tablet} from tablet`, ann.from_zotero && `${ann.from_zotero} from Zotero`,
         ann.zotero_only && `${ann.zotero_only} Zotero-only (notes/comments)`].filter(Boolean).join(" · ") || "No annotations yet"
      : null;
    return {
      key: a.key,
      title: a.name || a.title || a.key,
      stateLabel: STATE_LABEL[a.state] ?? a.state,
      state: a.state,
      location: a.location ?? null,
      annotations: a.state === "not_synced" ? null : annotations,
      error: a.error ?? null,
      canRestore: a.state === "removed" || a.state === "archived",
      canRemove: a.state === "synced" || a.state === "pending",
      isRendition: a.kind === "rendition",
      sourceKey: a.source_key,
      typography: a.typography,
    };
  });
  const active = rows.filter((r) => r.state !== "not_synced");
  const summary = !active.length
    ? "Not on tablet"
    : active.length === 1
      ? `${active[0].stateLabel}${active[0].location ? ` · ${active[0].location}` : ""}`
      : `${active.length} documents on tablet`;
  const homeChoices = [
    { key: "", label: "Automatic (deepest, then alphabetical)", selected: !d.home_override },
    ...d.collections.map((c) => ({ key: c.key, label: c.path.join(" / "), selected: d.home_override === c.key })),
  ];
  return {
    summary,
    rows,
    homeChoices,
    showHome: d.collections.length > 1,
    events: d.events.slice(0, 5).map((e) => e.message),
  };
}

export const FONTS = ["EB Garamond", "Noto Serif", "Noto Sans", "Noto Sans UI", "Noto Mono"];
export const TEXT_SCALES = [0.7, 0.8, 1, 1.2, 1.5, 2];
export const LINE_HEIGHTS = [100, 150, 200];
export const MARGINS = [50, 125, 200];
