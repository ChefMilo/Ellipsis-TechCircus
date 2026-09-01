/**
 * The always-on summary pill, fixed bottom-right inside the Shadow DOM. It is
 * the entry point to the drawer and the surface for loading / error / trusted
 * states. Dismissing it collapses it to a small dot the user can click to
 * bring the panel back.
 */
import type { AnalysisResponse } from "../../shared/contract";
import { h } from "./dom";

export interface PillCallbacks {
  onOpen?(): void;
  onRetry?(): void;
  onDismiss?(): void;
}

type Level = "neutral" | "trusted" | "ok" | "caution" | "high_risk";

const CHECK_ICON =
  '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M13.5 3.5 6 11 2.5 7.5 1 9l5 5 9-9z"/></svg>';
const CLOSE_ICON =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M12.7 4.7 9.4 8l3.3 3.3-1.4 1.4L8 9.4l-3.3 3.3-1.4-1.4L6.6 8 3.3 4.7l1.4-1.4L8 6.6l3.3-3.3z"/></svg>';

export class Pill {
  private readonly doc: Document;
  private readonly root: HTMLElement;
  private readonly cb: PillCallbacks;
  private dismissed = false;
  /** Replays the most recent non-collapsed state (used when re-expanding). */
  private lastPaint: (() => void) | null = null;

  constructor(layer: HTMLElement, cb: PillCallbacks = {}) {
    this.doc = layer.ownerDocument;
    this.cb = cb;
    this.root = h("div", {});
    layer.append(this.root);
    this.showLoading();
  }

  showLoading(): void {
    this.paint({
      level: "neutral",
      spinner: true,
      text: "Checking this page…",
    });
  }

  showResult(response: AnalysisResponse): void {
    const claims = response.verifiedClaims;
    const flagged = claims.filter(
      (vc) =>
        vc.assessment?.status === "contradicted" ||
        vc.assessment?.status === "partially_supported",
    ).length;
    const level: Level =
      response.articleVerdict.level === "trusted"
        ? "trusted"
        : (response.articleVerdict.level as Level);

    let text: string;
    let sub: string | undefined;
    if (claims.length === 0) {
      text = "No checkable claims found";
    } else if (flagged > 0) {
      text = `${flagged} claim${flagged === 1 ? "" : "s"} to check`;
      sub = `of ${claims.length} reviewed`;
    } else {
      text = `${claims.length} claim${claims.length === 1 ? "" : "s"} reviewed`;
      sub = "nothing flagged";
    }

    this.paint({ level, text, sub, openable: claims.length > 0 });
  }

  showTrusted(summary = "Trusted source"): void {
    this.paint({ level: "trusted", text: summary, icon: CHECK_ICON });
  }

  showError(message = "Couldn't check this page"): void {
    this.paint({ level: "neutral", text: message, error: true });
  }

  collapse(): void {
    this.dismissed = true;
    this.cb.onDismiss?.();
    this.root.textContent = "";
    this.root.append(
      h("button", {
        class: "dasfax-collapsed",
        type: "button",
        "aria-label": "Reopen Dasfax fact-check",
        html: `${CHECK_ICON}`,
        onClick: () => this.expand(),
      }),
    );
  }

  expand(): void {
    this.dismissed = false;
    this.lastPaint?.();
    this.cb.onOpen?.();
  }

  get isDismissed(): boolean {
    return this.dismissed;
  }

  destroy(): void {
    this.root.remove();
  }

  // --- internals ------------------------------------------------------- //

  private paint(opts: {
    level: Level;
    text: string;
    sub?: string;
    spinner?: boolean;
    icon?: string;
    error?: boolean;
    openable?: boolean;
  }): void {
    this.lastPaint = () => this.paint(opts);
    if (this.dismissed) return;
    this.root.textContent = "";

    const leading = opts.spinner
      ? h("span", { class: "dasfax-spinner", "aria-hidden": "true" })
      : h("span", {
          class: "dasfax-pill__dot",
          "aria-hidden": "true",
          ...(opts.icon ? { html: opts.icon } : {}),
        });

    const label = h(
      "span",
      { class: "dasfax-pill__text" },
      opts.error
        ? h("span", { class: "dasfax-error", text: opts.text })
        : this.doc.createTextNode(opts.text),
      opts.sub ? h("span", { class: "dasfax-pill__sub", text: ` · ${opts.sub}` }) : null,
    );

    const openable = opts.openable ?? false;
    const main = openable
      ? h(
          "button",
          {
            class: "dasfax-pill__button",
            type: "button",
            "aria-label": `${opts.text}. Open fact-check panel.`,
            onClick: () => this.cb.onOpen?.(),
          },
          leading,
          label,
        )
      : h("span", { class: "dasfax-pill__button" }, leading, label);

    const children: Node[] = [main];

    if (opts.error) {
      children.push(
        h("button", {
          class: "dasfax-retry",
          type: "button",
          text: "Retry",
          onClick: () => this.cb.onRetry?.(),
        }),
      );
    }

    children.push(
      h("button", {
        class: "dasfax-pill__dismiss",
        type: "button",
        "aria-label": "Dismiss",
        html: CLOSE_ICON,
        onClick: () => this.collapse(),
      }),
    );

    this.root.append(
      h(
        "div",
        { class: "dasfax-pill", dataset: { level: opts.level } },
        ...children,
      ),
    );
  }
}
