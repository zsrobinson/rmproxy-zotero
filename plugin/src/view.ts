// Pure view logic, shared by the column, the item pane section and the settings pane (unit tested).

import type { AttachmentInfo, CollectionInfo, DocState, EditionJob, EpubTypography, ItemDetail } from "./api";

export const STATE_LABEL: Record<DocState, string> = {
  synced: "On tablet",
  pending: "Syncing…",
  typesetting: "Making tablet edition…",
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
  typesetting: "⏳",
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
    isEpub: boolean;
    hasEdition: boolean;
    edition: EditionJob | null;
    editionBusy: boolean;
    typographyEffective: EpubTypography | null;
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
      isEpub: !!a.epub,
      hasEdition: d.attachments.some((b) => b.source_key === a.key && b.key !== a.key),
      edition: a.edition ?? null,
      editionBusy: !!a.edition && (a.edition.status === "queued" || a.edition.status === "working"),
      typographyEffective: a.typography_effective ?? null,
    };
  });
  for (const r of rows) {
    if (r.isEpub && r.state === "not_synced") {
      r.stateLabel = r.editionBusy ? STATE_LABEL.typesetting : r.hasEdition ? "Read on the tablet as its tablet edition" : "Not on tablet until you make a tablet edition";
    }
    if (r.isEpub && r.edition?.status === "failed") r.error = r.edition.message;
  }
  const active = rows.filter((r) => r.state !== "not_synced");
  const busy = rows.some((r) => r.editionBusy);
  const summary = busy
    ? STATE_LABEL.typesetting
    : !active.length
    ? rows.some((r) => r.isEpub) ? "EPUB: no tablet edition yet" : "Not on tablet"
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

/** One line of the progress window (and item pane) for an edition job. */
export function editionLine(title: string, job: EditionJob | undefined): { text: string; percent: number; finished: boolean; failed: boolean } {
  if (!job) return { text: `${title}: waiting…`, percent: 0, finished: false, failed: false };
  switch (job.status) {
    case "queued":
      return { text: `${title}: waiting for the service…`, percent: 0, finished: false, failed: false };
    case "working":
      return { text: `${title}: ${job.stage && job.stage !== "waiting" ? job.stage : "starting"}…`, percent: job.progress ?? 0, finished: false, failed: false };
    case "done":
      return { text: `${title}: ready${job.result_key ? "" : ` (${job.message ?? ""})`}`, percent: 100, finished: true, failed: false };
    default:
      return { text: `${title}: ${(job.message ?? "failed").split("\n")[0]}`, percent: 100, finished: true, failed: true };
  }
}
