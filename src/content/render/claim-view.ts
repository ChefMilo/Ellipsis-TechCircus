/**
 * Shared claim-rendering pieces used by BOTH the evidence drawer (panel.ts) and
 * the hover preview (hovercard.ts), so the two surfaces can never drift apart on
 * how a status badge or a source list looks.
 *
 * All builders use the `h()` hyperscript helper and return detached nodes; the
 * caller decides where they go.
 */
import type { VerifiedClaim } from "../../shared/contract";
import { STATUS, statusKeyFor, type StatusTreatment } from "./status-config";
import { h, hostnameOf, truncate } from "./dom";

/** The status treatment for a claim, keyed off its assessment (or "unverified"). */
export function treatmentFor(vc: VerifiedClaim): StatusTreatment {
  return STATUS[statusKeyFor(vc.assessment?.status)];
}

/** The coloured status pill ("Contradicted", "Needs review", …). */
export function renderBadge(t: StatusTreatment): HTMLElement {
  return h(
    "span",
    { class: "dasfax-badge", dataset: { status: t.key } },
    h("span", { class: "dasfax-badge__icon", html: t.icon }),
    t.label,
  );
}

interface SourceRow {
  title: string;
  url: string;
  snippet?: string;
}

/**
 * The rows to show under "Sources": WS6's own citations when it has any,
 * otherwise every evidence snippet WS5 retrieved for the claim.
 */
export function sourceRowsFor(vc: VerifiedClaim): SourceRow[] {
  const citations = vc.assessment?.citations ?? [];
  if (citations.length > 0) {
    return citations.map((c) => ({
      title: c.source_title ?? hostnameOf(c.source_url),
      url: c.source_url,
      snippet: c.snippet,
    }));
  }
  return vc.claim.evidence.map((e) => ({
    title: e.source_title ?? e.source_domain ?? hostnameOf(e.source_url),
    url: e.source_url,
    snippet: e.snippet,
  }));
}

/** "Sources (N)" heading + a list of linked source rows, or null if there are none. */
export function renderSourcesList(vc: VerifiedClaim): HTMLElement | null {
  const rows = sourceRowsFor(vc);
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
        r.snippet
          ? h("div", { class: "dasfax-source__snippet", text: truncate(r.snippet, 160) })
          : null,
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
