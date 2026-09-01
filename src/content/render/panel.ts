/**
 * The evidence drawer: a right-edge Shadow-DOM panel that lists the article's
 * claims and drills into one claim's status, explanation, and sources.
 *
 * Keyboard model: opening moves focus into the drawer and traps Tab within it;
 * Esc (or the close button, or a backdrop click) closes and restores focus to
 * whatever triggered the open.
 */
import type { AnalysisResponse, VerifiedClaim } from "../../shared/contract";
import { STATUS, statusKeyFor, type StatusTreatment } from "./status-config";
import { focusables, h, hostnameOf, truncate } from "./dom";

export interface PanelCallbacks {
  /** Fired when the user selects a claim (row click / list keyboard). */
  onClaimActivate?(claimId: string): void;
  /** Fired after the drawer closes. */
  onClose?(): void;
}

type View = { kind: "list" } | { kind: "claim"; claimId: string };

function badge(t: StatusTreatment): HTMLElement {
  return h(
    "span",
    { class: "dasfax-badge", dataset: { status: t.key } },
    h("span", { class: "dasfax-badge__icon", html: t.icon }),
    t.label,
  );
}

export class Panel {
  private readonly doc: Document;
  private readonly backdrop: HTMLElement;
  private readonly drawer: HTMLElement;
  private readonly body: HTMLElement;
  private readonly live: HTMLElement;
  private readonly cb: PanelCallbacks;

  private response: AnalysisResponse | null = null;
  private unanchored = new Set<string>();
  private view: View = { kind: "list" };
  private isOpen = false;
  private returnFocus: HTMLElement | null = null;
  private readonly onKeydown: (e: KeyboardEvent) => void;

  constructor(layer: HTMLElement, cb: PanelCallbacks = {}) {
    this.doc = layer.ownerDocument;
    this.cb = cb;

    this.body = h("div", { class: "dasfax-drawer__body" });
    this.live = h("div", {
      class: "dasfax-visually-hidden",
      "aria-live": "polite",
      role: "status",
    });

    const close = h(
      "button",
      {
        class: "dasfax-drawer__close",
        "aria-label": "Close fact-check panel",
        onClick: () => this.close(),
      },
      h("span", {
        html:
          '<svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M12.7 4.7 9.4 8l3.3 3.3-1.4 1.4L8 9.4l-3.3 3.3-1.4-1.4L6.6 8 3.3 4.7l1.4-1.4L8 6.6l3.3-3.3z"/></svg>',
      }),
    );

    this.drawer = h(
      "div",
      {
        class: "dasfax-drawer",
        role: "dialog",
        "aria-modal": "true",
        "aria-label": "Dasfax fact-check",
        dataset: { open: "false" },
      },
      h(
        "div",
        { class: "dasfax-drawer__head" },
        h("div", { class: "dasfax-drawer__title", text: "Fact-check" }),
        close,
      ),
      this.body,
      this.live,
    );

    this.backdrop = h("div", {
      class: "dasfax-backdrop",
      dataset: { open: "false" },
      hidden: "",
      onClick: () => this.close(),
    });

    this.onKeydown = (e: KeyboardEvent) => this.handleKeydown(e);

    layer.append(this.backdrop, this.drawer);
    this.drawer.hidden = true;
  }

  setData(response: AnalysisResponse, unanchored: Iterable<string>): void {
    this.response = response;
    this.unanchored = new Set(unanchored);
    if (this.isOpen) this.render();
  }

  open(claimId?: string): void {
    if (!this.response) return;
    this.view = claimId ? { kind: "claim", claimId } : { kind: "list" };
    if (!this.isOpen) {
      this.returnFocus =
        (this.doc.activeElement as HTMLElement | null) ?? null;
      this.isOpen = true;
      this.backdrop.hidden = false;
      this.drawer.hidden = false;
      // Force layout so the transform transition plays from the hidden state,
      // then flip the open flag. Setting it is synchronous so callers/tests see
      // `data-open="true"` immediately.
      void this.drawer.offsetWidth;
      this.backdrop.dataset.open = "true";
      this.drawer.dataset.open = "true";
      this.doc.addEventListener("keydown", this.onKeydown, true);
    }
    this.render();
    this.focusFirst();
  }

  close(): void {
    if (!this.isOpen) return;
    this.isOpen = false;
    this.backdrop.dataset.open = "false";
    this.drawer.dataset.open = "false";
    this.doc.removeEventListener("keydown", this.onKeydown, true);
    window.setTimeout(() => {
      if (this.isOpen) return;
      this.backdrop.hidden = true;
      this.drawer.hidden = true;
    }, 200);
    this.returnFocus?.focus?.();
    this.returnFocus = null;
    this.cb.onClose?.();
  }

  get open_(): boolean {
    return this.isOpen;
  }

  showClaim(claimId: string): void {
    this.view = { kind: "claim", claimId };
    if (this.isOpen) {
      this.render();
      this.focusFirst();
    } else {
      this.open(claimId);
    }
  }

  announce(message: string): void {
    this.live.textContent = "";
    window.setTimeout(() => (this.live.textContent = message), 30);
  }

  destroy(): void {
    this.doc.removeEventListener("keydown", this.onKeydown, true);
    this.backdrop.remove();
    this.drawer.remove();
  }

  // --- internals --------------------------------------------------------- //

  private handleKeydown(e: KeyboardEvent): void {
    if (!this.isOpen) return;
    if (e.key === "Escape") {
      e.preventDefault();
      this.close();
      return;
    }
    if (e.key === "Tab") {
      const items = focusables(this.drawer);
      if (items.length === 0) return;
      const first = items[0]!;
      const last = items[items.length - 1]!;
      const active = this.doc.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  private focusFirst(): void {
    const items = focusables(this.drawer);
    (items[0] ?? this.drawer).focus?.();
  }

  private claimById(claimId: string): VerifiedClaim | undefined {
    return this.response?.verifiedClaims.find((vc) => vc.claim.id === claimId);
  }

  private render(): void {
    if (!this.response) return;
    this.body.textContent = "";
    if (this.view.kind === "claim") {
      const vc = this.claimById(this.view.claimId);
      if (vc) {
        this.body.append(this.renderClaim(vc));
        return;
      }
    }
    this.body.append(this.renderList());
  }

  private renderList(): DocumentFragment {
    const res = this.response!;
    const frag = this.doc.createDocumentFragment();

    frag.append(
      h("p", { class: "dasfax-verdict", text: res.articleVerdict.summary }),
    );

    if (res.verifiedClaims.length === 0) {
      frag.append(h("p", { text: "No checkable claims were found on this page." }));
      return frag;
    }

    const list = h("div", { class: "dasfax-claim-list", role: "list" });
    for (const vc of [...res.verifiedClaims].sort((a, b) => a.claim.rank - b.claim.rank)) {
      const t = STATUS[statusKeyFor(vc.assessment?.status)];
      const row = h(
        "button",
        {
          class: "dasfax-claim-row",
          role: "listitem",
          type: "button",
          onClick: () => {
            this.cb.onClaimActivate?.(vc.claim.id);
            this.showClaim(vc.claim.id);
          },
        },
        badge(t),
        h("span", {
          class: "dasfax-claim-row__text",
          text: truncate(vc.claim.text, 110),
        }),
      );
      if (this.unanchored.has(vc.claim.id)) row.title = "Not located on the page";
      list.append(row);
    }
    frag.append(list);
    return frag;
  }

  private renderClaim(vc: VerifiedClaim): DocumentFragment {
    const frag = this.doc.createDocumentFragment();
    const { claim, assessment } = vc;
    const t = STATUS[statusKeyFor(assessment?.status)];

    frag.append(
      h(
        "button",
        {
          class: "dasfax-back",
          type: "button",
          onClick: () => {
            this.view = { kind: "list" };
            this.render();
            this.focusFirst();
          },
        },
        "← All claims",
      ),
      h("div", { class: "dasfax-claim-detail" }, badge(t)),
      h("p", { class: "dasfax-claim-detail__text", text: `“${claim.text}”` }),
    );

    const metaBits: string[] = [t.blurb];
    if (typeof assessment?.confidence === "number") {
      metaBits.push(`Confidence ${Math.round(assessment.confidence * 100)}%`);
    }
    frag.append(
      h("p", { class: "dasfax-claim-detail__meta", text: metaBits.join(" · ") }),
    );

    if (this.unanchored.has(claim.id)) {
      frag.append(
        h("p", {
          class: "dasfax-claim-detail__unanchored",
          text:
            "This claim could not be located in the page text, so it is not highlighted above.",
        }),
      );
    }

    if (assessment?.explanation) {
      frag.append(
        h("p", { class: "dasfax-claim-detail__explanation", text: assessment.explanation }),
      );
    }

    const sources = this.renderSources(vc);
    if (sources) frag.append(sources);

    return frag;
  }

  private renderSources(vc: VerifiedClaim): HTMLElement | null {
    const citations = vc.assessment?.citations ?? [];
    const rows =
      citations.length > 0
        ? citations.map((c) => ({
            title: c.source_title ?? hostnameOf(c.source_url),
            url: c.source_url,
            snippet: c.snippet,
          }))
        : vc.claim.evidence.map((e) => ({
            title: e.source_title ?? e.source_domain ?? hostnameOf(e.source_url),
            url: e.source_url,
            snippet: e.snippet,
          }));

    if (rows.length === 0) return null;

    const list = h("ul", { class: "dasfax-sources" });
    for (const r of rows) {
      list.append(
        h(
          "li",
          { class: "dasfax-source" },
          h("a", {
            class: "dasfax-source__title",
            href: r.url,
            target: "_blank",
            rel: "noopener noreferrer",
            text: r.title,
          }),
          h("div", { class: "dasfax-source__domain", text: hostnameOf(r.url) }),
          r.snippet && h("div", { class: "dasfax-source__snippet", text: truncate(r.snippet, 160) }),
        ),
      );
    }

    return h(
      "div",
      {},
      h("div", { class: "dasfax-claim-detail__meta", text: `Sources (${rows.length})` }),
      list,
    );
  }
}
