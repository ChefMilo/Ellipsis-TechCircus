/**
 * The hover preview: a small popover anchored to a highlighted claim that shows
 * its status, explanation and sources without leaving the article.
 *
 * On desktop this is WS2's PRIMARY way to inspect a flagged claim — you read,
 * you rest the pointer on the tinted sentence, the evidence appears next to it.
 * The full-height drawer (panel.ts) is the secondary surface, reached from the
 * summary pill, for the all-claims overview and for keyboard users.
 *
 * Behaviour:
 *   - show/hide are intent-debounced (a short delay in, a grace delay out) so the
 *     pointer can travel from the sentence onto the card without it vanishing;
 *   - the card tracks its own pointer enter/leave, so hovering it keeps it open;
 *   - a click on a claim "pins" the card (a dismiss button appears); a pinned
 *     card ignores hover-out until it is unpinned (click again, Esc, close button,
 *     or a click elsewhere — all driven by controller.ts).
 */
import type { AnalysisResponse, VerifiedClaim } from "../../shared/contract";
import { h } from "./dom";
import { renderBadge, renderSourcesList, treatmentFor } from "./claim-view";

const SHOW_DELAY_MS = 100;
const HIDE_DELAY_MS = 250;
const VIEWPORT_MARGIN = 8;
const ANCHOR_GAP = 8;
const MAX_WIDTH = 360;

const CLOSE_ICON =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M12.7 4.7 9.4 8l3.3 3.3-1.4 1.4L8 9.4l-3.3 3.3-1.4-1.4L6.6 8 3.3 4.7l1.4-1.4L8 6.6l3.3-3.3z"/></svg>';

export interface HovercardCallbacks {
  /** Fired when a claim is pinned (id) or the last pin is cleared (null). */
  onPinChange?(claimId: string | null): void;
}

export class Hovercard {
  private readonly doc: Document;
  private readonly card: HTMLElement;
  private readonly cb: HovercardCallbacks;

  private response: AnalysisResponse | null = null;
  private unanchored = new Set<string>();

  private shownId: string | null = null;
  private pinnedId: string | null = null;
  private anchorRect: DOMRect | null = null;
  private pointerInside = false;
  private showTimer: number | null = null;
  private hideTimer: number | null = null;

  constructor(layer: HTMLElement, cb: HovercardCallbacks = {}) {
    this.doc = layer.ownerDocument;
    this.cb = cb;

    this.card = h("div", {
      class: "dasfax-hovercard",
      role: "dialog",
      "aria-label": "Claim fact-check",
      dataset: { open: "false" },
    });
    this.card.hidden = true;
    this.card.addEventListener("mouseenter", () => {
      this.pointerInside = true;
      this.cancelHide();
    });
    this.card.addEventListener("mouseleave", () => {
      this.pointerInside = false;
      this.scheduleHide();
    });

    layer.append(this.card);
  }

  setData(response: AnalysisResponse, unanchored: Iterable<string>): void {
    this.response = response;
    this.unanchored = new Set(unanchored);
  }

  get isPinned(): boolean {
    return this.pinnedId !== null;
  }

  /** True while the pointer is over the card itself. */
  get isHovered(): boolean {
    return this.pointerInside;
  }

  /** The claim whose card is currently on screen, or null. */
  get visibleClaimId(): string | null {
    return this.shownId;
  }

  contains(node: EventTarget | Node | null): boolean {
    return node instanceof Node && this.card.contains(node);
  }

  /** Debounced show. No-op while a *different* claim is pinned. */
  scheduleShow(claimId: string, rect: DOMRect | null): void {
    if (this.pinnedId && this.pinnedId !== claimId) return;
    this.cancelHide();
    if (this.shownId === claimId) {
      this.anchorRect = rect;
      this.position();
      return;
    }
    if (this.showTimer !== null) window.clearTimeout(this.showTimer);
    this.showTimer = window.setTimeout(() => {
      this.showTimer = null;
      this.renderAndPlace(claimId, rect, false);
    }, SHOW_DELAY_MS);
  }

  /** Debounced hide. No-op while pinned. */
  scheduleHide(): void {
    if (this.pinnedId) return;
    if (this.showTimer !== null) {
      window.clearTimeout(this.showTimer);
      this.showTimer = null;
    }
    if (this.hideTimer !== null) return;
    this.hideTimer = window.setTimeout(() => {
      this.hideTimer = null;
      this.hide();
    }, HIDE_DELAY_MS);
  }

  cancelHide(): void {
    if (this.hideTimer !== null) {
      window.clearTimeout(this.hideTimer);
      this.hideTimer = null;
    }
  }

  /** Pin (or re-pin to another claim) the card open, with a dismiss button. */
  pin(claimId: string, rect: DOMRect | null): void {
    this.clearTimers();
    this.pinnedId = claimId;
    this.renderAndPlace(claimId, rect, true);
    this.cb.onPinChange?.(claimId);
  }

  /** Hide the card, ignoring the pinned flag. */
  hide(): void {
    this.clearTimers();
    const wasPinned = this.pinnedId !== null;
    this.pinnedId = null;
    this.shownId = null;
    this.anchorRect = null;
    this.pointerInside = false;
    this.card.dataset.open = "false";
    this.card.hidden = true;
    this.card.textContent = "";
    if (wasPinned) this.cb.onPinChange?.(null);
  }

  /** Re-run positioning against a fresh anchor rect (scroll / re-anchor). */
  reposition(rect: DOMRect | null): void {
    if (this.shownId === null) return;
    this.anchorRect = rect;
    this.position();
  }

  destroy(): void {
    this.hide();
    this.card.remove();
  }

  // --- internals ------------------------------------------------------- //

  private clearTimers(): void {
    if (this.showTimer !== null) {
      window.clearTimeout(this.showTimer);
      this.showTimer = null;
    }
    if (this.hideTimer !== null) {
      window.clearTimeout(this.hideTimer);
      this.hideTimer = null;
    }
  }

  private claimById(claimId: string): VerifiedClaim | undefined {
    return this.response?.verifiedClaims.find((vc) => vc.claim.id === claimId);
  }

  private renderAndPlace(claimId: string, rect: DOMRect | null, pinned: boolean): void {
    const vc = this.claimById(claimId);
    if (!vc) return;
    this.shownId = claimId;
    this.anchorRect = rect;
    this.card.textContent = "";
    this.card.append(this.buildContent(vc, pinned));
    this.card.hidden = false;
    // Force a layout pass so the fade transition runs from the hidden state.
    void this.card.offsetWidth;
    this.card.dataset.open = "true";
    this.position();
  }

  private buildContent(vc: VerifiedClaim, pinned: boolean): DocumentFragment {
    const frag = this.doc.createDocumentFragment();
    const t = treatmentFor(vc);

    const head = h("div", { class: "dasfax-hovercard__head" }, renderBadge(t));
    if (pinned) {
      head.append(
        h("button", {
          class: "dasfax-hovercard__close",
          type: "button",
          "aria-label": "Dismiss",
          html: CLOSE_ICON,
          onClick: () => this.hide(),
        }),
      );
    }
    frag.append(head);

    const scroll = h("div", { class: "dasfax-hovercard__scroll" });

    const metaBits: string[] = [t.blurb];
    if (typeof vc.assessment?.confidence === "number") {
      metaBits.push(`Confidence ${Math.round(vc.assessment.confidence * 100)}%`);
    }
    scroll.append(h("p", { class: "dasfax-hovercard__meta", text: metaBits.join(" · ") }));

    if (this.unanchored.has(vc.claim.id)) {
      scroll.append(
        h("p", {
          class: "dasfax-hovercard__unanchored",
          text: "This claim could not be located in the page text.",
        }),
      );
    }

    if (vc.assessment?.explanation) {
      scroll.append(
        h("p", { class: "dasfax-hovercard__explanation", text: vc.assessment.explanation }),
      );
    }

    const sources = renderSourcesList(vc);
    if (sources) scroll.append(sources);

    frag.append(scroll);
    return frag;
  }

  private position(): void {
    const rect = this.anchorRect;
    if (!rect) return;
    const win = this.doc.defaultView;
    const vw = win?.innerWidth ?? 0;
    const vh = win?.innerHeight ?? 0;

    const cap = Math.max(160, Math.min(MAX_WIDTH, vw - VIEWPORT_MARGIN * 2));
    this.card.style.maxWidth = `${cap}px`;

    const cardRect = this.card.getBoundingClientRect();
    const cardW = cardRect.width || cap;
    const cardH = cardRect.height;

    // Horizontal: align to the claim's left edge, clamped into the viewport.
    let left = rect.left;
    left = Math.min(left, vw - cardW - VIEWPORT_MARGIN);
    left = Math.max(VIEWPORT_MARGIN, left);

    // Vertical: prefer above the claim; flip below if it would clip the top edge.
    let top = rect.top - cardH - ANCHOR_GAP;
    if (top < VIEWPORT_MARGIN) {
      const roomAbove = rect.top - VIEWPORT_MARGIN;
      const roomBelow = vh - rect.bottom - VIEWPORT_MARGIN;
      top =
        roomBelow >= cardH || roomBelow >= roomAbove
          ? rect.bottom + ANCHOR_GAP
          : VIEWPORT_MARGIN;
    }

    this.card.style.left = `${Math.round(left)}px`;
    this.card.style.top = `${Math.round(top)}px`;
  }
}
