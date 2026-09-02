/**
 * Wires the pieces together: Shadow root + pill + drawer + hovercard + highlight
 * layer, plus the cross-cutting concerns — document-level pointer/keyboard
 * hit-testing for highlights, a MutationObserver that re-anchors claims lost to
 * lazy-loading or re-renders, and per-URL dismissal memory.
 *
 * Interaction model (desktop-first):
 *   - hovering a highlighted claim opens the hovercard next to it (the primary
 *     way to read a claim's evidence);
 *   - clicking a highlighted claim PINS that hovercard open (a dismiss button
 *     appears); clicking it again, pressing Esc, or clicking away unpins it;
 *   - the full-height drawer is reached only from the summary pill, for the
 *     all-claims overview.
 *
 * `bootstrap.ts` (WS1) calls `mountFactCheckUI()` once the page is judged an
 * article, then drives it with `showLoading()` / `render()` / `showError()`.
 */
import type { AnalysisResponse } from "../../shared/contract";
import { statusKeyFor, type StatusKey } from "./status-config";
import { rangesForClaims } from "../anchor/to-range";
import { Highlights } from "./highlights";
import { Hovercard } from "./hovercard";
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
/** Pointer-move hit-testing is cheap but not free; sample at most this often. */
const MOUSEMOVE_THROTTLE_MS = 50;

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
  private readonly hovercard: Hovercard;
  private readonly highlights: Highlights;
  private readonly articleRoot: Element;
  private readonly reanchorWindowMs: number;

  private response: AnalysisResponse | null = null;
  private statusById = new Map<string, StatusKey>();
  private unanchored = new Set<string>();
  private observer: MutationObserver | null = null;
  private reanchorDeadline = 0;
  private reanchorTimer: number | null = null;

  /** Last claim the pointer resolved to, so mousemove only acts on a change. */
  private lastPointerClaimId: string | null = null;
  private lastMouseMoveAt = 0;

  private readonly onDocClick: (e: MouseEvent) => void;
  private readonly onDocKeydown: (e: KeyboardEvent) => void;
  private readonly onDocMouseMove: (e: MouseEvent) => void;
  private readonly onDocPointerOver: (e: MouseEvent) => void;
  private readonly onDocPointerOut: (e: MouseEvent) => void;
  private readonly onDocFocusIn: (e: FocusEvent) => void;
  private readonly onDocFocusOut: (e: FocusEvent) => void;
  private readonly onWinScroll: () => void;

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

    this.hovercard = new Hovercard(layer, {
      // Pinning a card is the "active" (click-selected) claim.
      onPinChange: (id) => this.highlights.setActive(id),
    });

    this.pill = new Pill(layer, {
      onOpen: () => {
        this.hovercard.hide();
        this.panel.open();
      },
      onRetry: () => opts.onRetry?.(),
      onDismiss: () => {
        if (this.response) writeDismissed(this.response.url, true);
        this.hovercard.hide();
        this.panel.close();
      },
    });

    this.onDocClick = (e) => this.handleDocClick(e);
    this.onDocKeydown = (e) => this.handleDocKeydown(e);
    this.onDocMouseMove = (e) => this.handleDocMouseMove(e);
    this.onDocPointerOver = (e) => this.handleDocPointerOver(e);
    this.onDocPointerOut = (e) => this.handleDocPointerOut(e);
    this.onDocFocusIn = (e) => this.handleDocFocusIn(e);
    this.onDocFocusOut = (e) => this.handleDocFocusOut(e);
    this.onWinScroll = () => this.handleScroll();

    document.addEventListener("click", this.onDocClick, true);
    document.addEventListener("keydown", this.onDocKeydown, true);
    document.addEventListener("mousemove", this.onDocMouseMove, true);
    document.addEventListener("mouseover", this.onDocPointerOver, true);
    document.addEventListener("mouseout", this.onDocPointerOut, true);
    document.addEventListener("focusin", this.onDocFocusIn, true);
    document.addEventListener("focusout", this.onDocFocusOut, true);
    window.addEventListener("scroll", this.onWinScroll, true);
  }

  showLoading(): void {
    this.pill.showLoading();
  }

  showTrusted(summary?: string): void {
    this.hovercard.hide();
    this.highlights.clear();
    this.pill.showTrusted(summary);
  }

  showError(message?: string): void {
    this.hovercard.hide();
    this.highlights.clear();
    this.pill.showError(message);
  }

  render(response: AnalysisResponse): void {
    this.response = response;
    this.lastPointerClaimId = null;
    this.hovercard.hide();

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
    this.hovercard.setData(response, this.unanchored);
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
    document.removeEventListener("mousemove", this.onDocMouseMove, true);
    document.removeEventListener("mouseover", this.onDocPointerOver, true);
    document.removeEventListener("mouseout", this.onDocPointerOut, true);
    document.removeEventListener("focusin", this.onDocFocusIn, true);
    document.removeEventListener("focusout", this.onDocFocusOut, true);
    window.removeEventListener("scroll", this.onWinScroll, true);
    this.observer?.disconnect();
    this.observer = null;
    if (this.reanchorTimer !== null) window.clearTimeout(this.reanchorTimer);
    this.hovercard.destroy();
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

  /** True when hover UX should stay out of the way (drawer open, pill dismissed). */
  private hoverSuppressed(): boolean {
    return this.pill.isDismissed || this.panel.open_;
  }

  /** Resolve the claim a pointer event lands on, span-path first then geometry. */
  private claimFromPointer(e: MouseEvent): string | null {
    return (
      this.highlights.claimForNode(e.target) ??
      this.highlights.claimAtPoint(e.clientX, e.clientY)
    );
  }

  private showFor(claimId: string): void {
    this.highlights.setHover(claimId);
    this.hovercard.scheduleShow(claimId, this.highlights.rectFor(claimId));
  }

  private clearHover(): void {
    this.highlights.setHover(null);
    this.hovercard.scheduleHide();
  }

  private dismissPinned(): void {
    this.hovercard.hide();
    this.highlights.setActive(null);
    this.highlights.setHover(null);
  }

  private togglePin(claimId: string): void {
    if (this.hovercard.isPinned && this.hovercard.visibleClaimId === claimId) {
      this.hovercard.hide();
      this.highlights.setActive(null);
      return;
    }
    this.highlights.setActive(claimId);
    this.hovercard.pin(claimId, this.highlights.rectFor(claimId));
  }

  private handleDocMouseMove(e: MouseEvent): void {
    if (this.hoverSuppressed() || this.hovercard.isHovered) return;
    const now = Date.now();
    if (now - this.lastMouseMoveAt < MOUSEMOVE_THROTTLE_MS) return;
    this.lastMouseMoveAt = now;

    const claimId = this.claimFromPointer(e);
    if (claimId === this.lastPointerClaimId) return;
    this.lastPointerClaimId = claimId;

    if (claimId && !this.unanchored.has(claimId)) this.showFor(claimId);
    else this.clearHover();
  }

  private handleDocPointerOver(e: MouseEvent): void {
    if (this.hoverSuppressed()) return;
    if (this.hovercard.contains(e.target)) {
      this.hovercard.cancelHide();
      return;
    }
    const claimId = this.highlights.claimForNode(e.target);
    if (!claimId || this.unanchored.has(claimId)) return;
    this.lastPointerClaimId = claimId;
    this.showFor(claimId);
  }

  private handleDocPointerOut(e: MouseEvent): void {
    if (this.hoverSuppressed()) return;
    const to = e.relatedTarget;
    if (this.hovercard.contains(to) || this.highlights.claimForNode(to)) return;
    this.lastPointerClaimId = null;
    this.clearHover();
  }

  private handleDocFocusIn(e: FocusEvent): void {
    if (this.hoverSuppressed()) return;
    const claimId = (e.target as HTMLElement | null)?.dataset?.dasfaxClaimId;
    if (!claimId || this.unanchored.has(claimId)) return;
    this.showFor(claimId);
  }

  private handleDocFocusOut(e: FocusEvent): void {
    const claimId = (e.target as HTMLElement | null)?.dataset?.dasfaxClaimId;
    if (!claimId) return;
    this.clearHover();
  }

  private handleScroll(): void {
    const shown = this.hovercard.visibleClaimId;
    if (!shown) return;
    if (this.hovercard.isPinned) {
      this.hovercard.reposition(this.highlights.rectFor(shown));
    } else {
      this.hovercard.hide();
    }
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
        this.hovercard.setData(this.response, this.unanchored);
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
    if (this.pill.isDismissed) return;
    // Clicks inside our own UI (pill, drawer, hovercard, source links) are
    // handled by those components.
    if (this.isOwnEvent(e.target)) return;

    // Let a real click on an inline link inside a highlighted sentence navigate.
    const target = e.target;
    if (target instanceof Element && target.closest("a[href]")) return;

    const path = e.composedPath?.() ?? [];
    let claimId: string | null = null;
    for (const node of path) {
      claimId = this.highlights.claimForNode(node as Node);
      if (claimId) break;
    }
    claimId ??= this.highlights.claimForNode(e.target);
    claimId ??= this.highlights.claimAtPoint(e.clientX, e.clientY);

    if (!claimId) {
      // A click anywhere off the claims dismisses a pinned card.
      if (this.hovercard.isPinned) this.dismissPinned();
      return;
    }

    e.preventDefault();
    e.stopPropagation();
    this.togglePin(claimId);
  }

  private handleDocKeydown(e: KeyboardEvent): void {
    if (e.key === "Escape") {
      if (this.hovercard.visibleClaimId) {
        e.preventDefault();
        this.dismissPinned();
      }
      return;
    }
    if (e.key !== "Enter" && e.key !== " ") return;
    const claimId = (e.target as HTMLElement | null)?.dataset?.dasfaxClaimId;
    if (!claimId) return;
    e.preventDefault();
    this.togglePin(claimId);
  }
}

export function mountFactCheckUI(options: MountOptions = {}): FactCheckController {
  return new Controller(options);
}
