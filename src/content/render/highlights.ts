/**
 * Paint claim highlights onto the live article.
 *
 * Primary path: the CSS Custom Highlight API (`CSS.highlights` + `Highlight`).
 * No DOM mutation, no reflow, survives the host page re-rendering its subtree.
 * Chrome 105+.
 *
 * Fallback path (API missing — e.g. jsdom, very old engines): wrap the matched
 * `Range`s in `<span data-dasfax-claim-id>` elements, tracked so `clear()` can
 * unwrap them cleanly.
 *
 * Either way, `claimAtPoint()` gives the controller one hit-test entry point, so
 * click handling is identical on both paths.
 */
import { STATUS, STATUS_KEYS, type StatusKey } from "./status-config";

const PAGE_STYLE_ID = "dasfax-highlight-style";
const HL_PREFIX = "dasfax-";
const ACTIVE_HL = "dasfax-active";

// The installed TS DOM lib ships an incomplete `HighlightRegistry` type and no
// `Highlight` constructor. Narrow shims — all uses are runtime-guarded by
// `highlightApiAvailable()`.
type HighlightLike = object;
interface HighlightCtor {
  new (...ranges: Range[]): HighlightLike;
}
interface HighlightRegistryLike {
  set(name: string, highlight: HighlightLike): void;
  delete(name: string): void;
}

function registry(): HighlightRegistryLike {
  return (CSS as unknown as { highlights: HighlightRegistryLike }).highlights;
}
function highlightCtor(): HighlightCtor {
  return (globalThis as unknown as { Highlight: HighlightCtor }).Highlight;
}

interface Entry {
  claimId: string;
  statusKey: StatusKey;
  range: Range;
  /** Fallback path only: the spans wrapping this claim's text. */
  spans: HTMLElement[];
}

function highlightApiAvailable(): boolean {
  return (
    typeof CSS !== "undefined" &&
    "highlights" in CSS &&
    typeof (globalThis as { Highlight?: unknown }).Highlight === "function"
  );
}

function buildPageStyle(): string {
  const hlRules = STATUS_KEYS.map(
    (k) =>
      `::highlight(${HL_PREFIX}${k}) { background-color: ${STATUS[k].tint}; border-radius: 2px; }`,
  ).join("\n");
  const spanRules = STATUS_KEYS.map(
    (k) =>
      `.dasfax-hl[data-dasfax-status="${k}"] { background-color: ${STATUS[k].tint}; box-shadow: inset 0 -2px 0 ${STATUS[k].accent}; }`,
  ).join("\n");
  return `
${hlRules}
::highlight(${ACTIVE_HL}) { background-color: rgba(255, 214, 0, 0.45); }
.dasfax-hl {
  border-radius: 2px;
  cursor: pointer;
  padding-bottom: 1px;
}
.dasfax-hl.dasfax-hl--active { outline: 2px solid #b08800; outline-offset: 1px; }
${spanRules}
`;
}

function ensurePageStyle(doc: Document): void {
  if (doc.getElementById(PAGE_STYLE_ID)) return;
  const style = doc.createElement("style");
  style.id = PAGE_STYLE_ID;
  style.textContent = buildPageStyle();
  (doc.head || doc.documentElement).appendChild(style);
}

/** Wrap the text covered by `range` in one `<span>` per intersecting text node. */
function wrapRange(
  doc: Document,
  range: Range,
  claimId: string,
  statusKey: StatusKey,
): HTMLElement[] {
  const spans: HTMLElement[] = [];
  const root = range.commonAncestorContainer;
  const walker = doc.createTreeWalker(
    root.nodeType === Node.ELEMENT_NODE ? root : (root.parentNode as Node) ?? root,
    NodeFilter.SHOW_TEXT,
  );

  const textNodes: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n as Text;
    if (range.intersectsNode(t)) textNodes.push(t);
  }

  for (const original of textNodes) {
    let node = original;
    const isStart = node === range.startContainer;
    const isEnd = node === range.endContainer;
    const from = isStart ? range.startOffset : 0;
    const to = isEnd ? range.endOffset : node.length;
    if (to <= from) continue;

    if (from > 0) node = node.splitText(from);
    if (to - from < node.length) node.splitText(to - from);

    const span = doc.createElement("span");
    span.className = "dasfax-hl";
    span.dataset.dasfaxClaimId = claimId;
    span.dataset.dasfaxStatus = statusKey;
    span.setAttribute("role", "button");
    span.setAttribute("tabindex", "0");
    span.setAttribute(
      "aria-label",
      `Fact-check: ${STATUS[statusKey].label}. Activate to see evidence.`,
    );
    node.parentNode?.insertBefore(span, node);
    span.appendChild(node);
    spans.push(span);
  }
  return spans;
}

function unwrap(span: HTMLElement): void {
  const parent = span.parentNode;
  if (!parent) return;
  while (span.firstChild) parent.insertBefore(span.firstChild, span);
  parent.removeChild(span);
  (parent as Element).normalize?.();
}

export class Highlights {
  private readonly doc: Document;
  private readonly useApi: boolean;
  private entries = new Map<string, Entry>();
  private activeId: string | null = null;

  constructor(doc: Document = document) {
    this.doc = doc;
    this.useApi = highlightApiAvailable();
  }

  get mode(): "highlight-api" | "span-fallback" {
    return this.useApi ? "highlight-api" : "span-fallback";
  }

  paint(ranges: Map<string, Range>, statusById: Map<string, StatusKey>): void {
    ensurePageStyle(this.doc);
    this.clear();

    for (const [claimId, range] of ranges) {
      const statusKey = statusById.get(claimId) ?? "unverified";
      const spans =
        this.useApi ? [] : wrapRange(this.doc, range, claimId, statusKey);
      this.entries.set(claimId, { claimId, statusKey, range, spans });
    }

    if (this.useApi) this.syncApiHighlights();
  }

  private syncApiHighlights(): void {
    const buckets = new Map<StatusKey, Range[]>();
    for (const entry of this.entries.values()) {
      const list = buckets.get(entry.statusKey) ?? [];
      list.push(entry.range);
      buckets.set(entry.statusKey, list);
    }
    const reg = registry();
    const Ctor = highlightCtor();
    for (const key of STATUS_KEYS) {
      const name = `${HL_PREFIX}${key}`;
      const ranges = buckets.get(key);
      if (!ranges || ranges.length === 0) {
        reg.delete(name);
        continue;
      }
      reg.set(name, new Ctor(...ranges));
    }

    reg.delete(ACTIVE_HL);
    if (this.activeId) {
      const active = this.entries.get(this.activeId);
      if (active) reg.set(ACTIVE_HL, new Ctor(active.range));
    }
  }

  setActive(claimId: string | null): void {
    this.activeId = claimId;
    if (this.useApi) {
      this.syncApiHighlights();
      return;
    }
    for (const entry of this.entries.values()) {
      const on = entry.claimId === claimId;
      for (const span of entry.spans) span.classList.toggle("dasfax-hl--active", on);
    }
  }

  /** claim id for a node inside a fallback highlight span, or null. */
  claimForNode(node: EventTarget | Node | null): string | null {
    if (!(node instanceof Node)) return null;
    const el =
      node.nodeType === Node.ELEMENT_NODE
        ? (node as Element)
        : node.parentElement;
    const span = el?.closest?.("[data-dasfax-claim-id]") as HTMLElement | null;
    return span?.dataset.dasfaxClaimId ?? null;
  }

  /** claim id whose highlight covers the viewport point, or null. */
  claimAtPoint(x: number, y: number): string | null {
    // Fallback spans are real elements — check the composed target first.
    const el = this.doc.elementFromPoint(x, y);
    const byEl = this.claimForNode(el);
    if (byEl) return byEl;

    for (const entry of this.entries.values()) {
      for (const rect of Array.from(entry.range.getClientRects())) {
        if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
          return entry.claimId;
        }
      }
    }
    return null;
  }

  scrollTo(claimId: string): void {
    const entry = this.entries.get(claimId);
    if (!entry) return;
    const first = entry.spans[0];
    if (first) {
      first.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
    const rect = entry.range.getBoundingClientRect();
    const win = this.doc.defaultView;
    if (win && (rect.top < 0 || rect.bottom > win.innerHeight)) {
      win.scrollTo({
        top: win.scrollY + rect.top - win.innerHeight / 2,
        behavior: "smooth",
      });
    }
  }

  has(claimId: string): boolean {
    return this.entries.has(claimId);
  }

  clear(): void {
    for (const entry of this.entries.values()) {
      for (const span of entry.spans) unwrap(span);
    }
    this.entries.clear();
    this.activeId = null;
    if (this.useApi && typeof CSS !== "undefined" && "highlights" in CSS) {
      const reg = registry();
      for (const key of STATUS_KEYS) reg.delete(`${HL_PREFIX}${key}`);
      reg.delete(ACTIVE_HL);
    }
  }

  destroy(): void {
    this.clear();
    this.doc.getElementById(PAGE_STYLE_ID)?.remove();
  }
}
