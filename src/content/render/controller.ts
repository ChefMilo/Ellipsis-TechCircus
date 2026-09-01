/**
 * Wires the pieces together: Shadow root + pill + drawer + highlight layer, plus
 * the cross-cutting concerns — document-level click/keyboard hit-testing for
 * highlights, a MutationObserver that re-anchors claims lost to lazy-loading or
 * re-renders, and per-URL dismissal memory.
 *
 * `bootstrap.ts` (WS1) calls `mountFactCheckUI()` once the page is judged an
 * article, then drives it with `showLoading()` / `render()` / `showError()`.
 */
import type { AnalysisResponse } from "../../shared/contract";
import { statusKeyFor, type StatusKey } from "./status-config";
import { rangesForClaims } from "../anchor/to-range";
import { Highlights } from "./highlights";
import { Panel } from "./panel";
import { Pill } from "./pill";
import { createShadowRoot, layerOf, type ShadowUI } from "./shadow-root";

export interface FactCheckController {
  showLoading(): void;
  render(response: AnalysisResponse): void;
  showError(message?: string): void;
  showTrusted(summary?: string): void;
  teardown(): void;
}

export interface MountOptions {
  /** Element to search for claim text. Defaults to `document.body`. */
  articleRoot?: Element;
  /** Called when the user clicks "Retry" on the error pill. */
  onRetry?(): void;
  /** How long to keep re-anchoring claims after the first render. Default 5000. */
  reanchorWindowMs?: number;
}

const DISMISS_PREFIX = "dasfax:dismissed:";

function readDismissed(url: string): Promise<boolean> {
  try {
    const storage = (chrome as unknown as { storage?: { session?: chrome.storage.StorageArea } })
      .storage?.session;
    if (!storage) return Promise.resolve(false);
    return new Promise((resolve) => {
      storage.get(DISMISS_PREFIX + url, (items) =>
        resolve(Boolean(items?.[DISMISS_PREFIX + url])),
      );
    });
  } catch {
    return Promise.resolve(false);
  }
}

function writeDismissed(url: string, value: boolean): void {
  try {
    const storage = (chrome as unknown as { storage?: { session?: chrome.storage.StorageArea } })
      .storage?.session;
    storage?.set({ [DISMISS_PREFIX + url]: value });
  } catch {
    /* no-op */
  }
}

class Controller implements FactCheckController {
  private readonly shadow: ShadowUI;
  private readonly pill: Pill;
  private readonly panel: Panel;
  private readonly highlights: Highlights;
  private readonly articleRoot: Element;
  private readonly reanchorWindowMs: number;

  private response: AnalysisResponse | null = null;
  private statusById = new Map<string, StatusKey>();
  private unanchored = new Set<string>();
  private observer: MutationObserver | null = null;
  private reanchorDeadline = 0;
  private reanchorTimer: number | null = null;
  private readonly onDocClick: (e: MouseEvent) => void;
  private readonly onDocKeydown: (e: KeyboardEvent) => void;

  constructor(opts: MountOptions) {
    this.articleRoot = opts.articleRoot ?? document.body;
    this.reanchorWindowMs = opts.reanchorWindowMs ?? 5000;

    this.shadow = createShadowRoot();
    const layer = layerOf(this.shadow.shadow);
    this.highlights = new Highlights();

    this.panel = new Panel(layer, {
      onClaimActivate: (id) => this.highlights.setActive(id),
      onClose: () => this.highlights.setActive(null),
    });

    this.pill = new Pill(layer, {
      onOpen: () => this.panel.open(),
      onRetry: () => opts.onRetry?.(),
      onDismiss: () => {
        if (this.response) writeDismissed(this.response.url, true);
        this.panel.close();
      },
    });

    this.onDocClick = (e) => this.handleDocClick(e);
    this.onDocKeydown = (e) => this.handleDocKeydown(e);
    document.addEventListener("click", this.onDocClick, true);
    document.addEventListener("keydown", this.onDocKeydown, true);
  }

  showLoading(): void {
    this.pill.showLoading();
  }

  showTrusted(summary?: string): void {
    this.highlights.clear();
    this.pill.showTrusted(summary);
  }

  showError(message?: string): void {
    this.highlights.clear();
    this.pill.showError(message);
  }

  render(response: AnalysisResponse): void {
    this.response = response;

    if (response.status === "skipped") {
      this.showTrusted(response.articleVerdict.summary);
      return;
    }

    this.statusById = new Map(
      response.verifiedClaims.map((vc) => [
        vc.claim.id,
        statusKeyFor(vc.assessment?.status),
      ]),
    );

    this.anchorAndPaint();

    this.panel.setData(response, this.unanchored);
    this.pill.showResult(response);

    const flagged = this.unanchored.size;
    this.panel.announce(
      `Fact-check complete. ${response.verifiedClaims.length} claim` +
        `${response.verifiedClaims.length === 1 ? "" : "s"} reviewed` +
        (flagged ? `, ${flagged} not located on the page.` : "."),
    );

    void readDismissed(response.url).then((dismissed) => {
      if (dismissed) this.pill.collapse();
    });

    this.startReanchorWatch();
  }

  teardown(): void {
    document.removeEventListener("click", this.onDocClick, true);
    document.removeEventListener("keydown", this.onDocKeydown, true);
    this.observer?.disconnect();
    this.observer = null;
    if (this.reanchorTimer !== null) window.clearTimeout(this.reanchorTimer);
    this.highlights.destroy();
    this.panel.destroy();
    this.pill.destroy();
    this.shadow.destroy();
  }

  // --- internals ------------------------------------------------------- //

  private anchorAndPaint(): void {
    if (!this.response) return;
    const { ranges, unanchored } = rangesForClaims(this.response.verifiedClaims, {
      root: this.articleRoot,
    });
    this.unanchored = new Set(unanchored);
    this.highlights.paint(ranges, this.statusById);
  }

  private startReanchorWatch(): void {
    if (this.unanchored.size === 0) return;
    this.reanchorDeadline = Date.now() + this.reanchorWindowMs;
    this.observer?.disconnect();
    this.observer = new MutationObserver(() => this.scheduleReanchor());
    this.observer.observe(this.articleRoot, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  private scheduleReanchor(): void {
    if (this.reanchorTimer !== null) return;
    this.reanchorTimer = window.setTimeout(() => {
      this.reanchorTimer = null;
      if (!this.response || Date.now() > this.reanchorDeadline) {
        this.observer?.disconnect();
        this.observer = null;
        return;
      }
      const before = this.unanchored.size;
      this.anchorAndPaint();
      if (this.unanchored.size !== before) {
        this.panel.setData(this.response, this.unanchored);
        this.pill.showResult(this.response);
      }
      if (this.unanchored.size === 0) {
        this.observer?.disconnect();
        this.observer = null;
      }
    }, 400);
  }

  private isOwnEvent(target: EventTarget | null): boolean {
    return target instanceof Node && this.shadow.host.contains(target as Node);
  }

  private handleDocClick(e: MouseEvent): void {
    if (this.isOwnEvent(e.target) || this.pill.isDismissed) return;
    // Let a real click on an inline link inside a highlighted sentence navigate.
    const target = e.target;
    if (
      target instanceof Element &&
      target.closest("a[href]") &&
      !this.shadow.host.contains(target)
    ) {
      return;
    }
    const path = e.composedPath?.() ?? [];
    let claimId: string | null = null;
    for (const node of path) {
      claimId = this.highlights.claimForNode(node as Node);
      if (claimId) break;
    }
    claimId ??= this.highlights.claimForNode(e.target);
    claimId ??= this.highlights.claimAtPoint(e.clientX, e.clientY);
    if (!claimId) return;
    e.preventDefault();
    e.stopPropagation();
    this.highlights.setActive(claimId);
    this.panel.showClaim(claimId);
  }

  private handleDocKeydown(e: KeyboardEvent): void {
    if (e.key !== "Enter" && e.key !== " ") return;
    const target = e.target as HTMLElement | null;
    const claimId = target?.dataset?.dasfaxClaimId;
    if (!claimId) return;
    e.preventDefault();
    this.highlights.setActive(claimId);
    this.panel.showClaim(claimId);
  }
}

export function mountFactCheckUI(options: MountOptions = {}): FactCheckController {
  return new Controller(options);
}
