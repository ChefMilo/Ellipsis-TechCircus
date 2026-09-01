/**
 * WS1's placeholder trust/override UI:
 *  - a "Trusted source" badge on whitelisted pages
 *  - a "Check this page anyway" override button on pages Tier 1 decided
 *    are not articles
 *
 * Deliberately minimal (a small Shadow DOM chip) -- WS2 owns the real
 * evidence panel (src/content/render/). Placed top-right so it never
 * overlaps WS2's summary pill, which docks bottom-right.
 */

export interface Ws1UiHandle {
  teardown(): void;
}

const HOST_TAG = "ws1-trust-ui";

const CSS = `
:host {
  all: initial;
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 2147483646;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 13px;
}
.ws1-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 999px;
  background: #ffffff;
  color: #1f2328;
  border: 1px solid #d0d7de;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
}
@media (prefers-color-scheme: dark) {
  .ws1-chip {
    background: #1c2128;
    color: #e6edf3;
    border-color: #3d444d;
  }
}
.ws1-badge { font-weight: 600; white-space: nowrap; }
.ws1-button {
  font: inherit;
  color: inherit;
  cursor: pointer;
  background: transparent;
  border: 0;
  padding: 0;
  text-decoration: underline;
  white-space: nowrap;
}
.ws1-button:hover { opacity: 0.75; }
`;

function mount(doc: Document): { host: HTMLElement; shadow: ShadowRoot } {
  const host = doc.createElement(HOST_TAG);
  const shadow = host.attachShadow({ mode: "open" });
  const style = doc.createElement("style");
  style.textContent = CSS;
  shadow.appendChild(style);
  (doc.documentElement || doc.body).appendChild(host);
  return { host, shadow };
}

/** Removes any WS1 badge/button currently on the page, if present. */
export function teardownWs1Ui(doc: Document = document): void {
  doc.querySelectorAll(HOST_TAG).forEach((el) => el.remove());
}

/** Shows the "Trusted source" badge for whitelisted pages. */
export function showTrustedBadge(doc: Document = document): Ws1UiHandle {
  teardownWs1Ui(doc);
  const { host, shadow } = mount(doc);
  const chip = doc.createElement("div");
  chip.className = "ws1-chip";
  const badge = doc.createElement("span");
  badge.className = "ws1-badge";
  badge.textContent = "✓ Trusted source";
  chip.appendChild(badge);
  shadow.appendChild(chip);
  return { teardown: () => host.remove() };
}

/**
 * Shows the "Check this page anyway" override for pages Tier 1 decided are
 * not articles. Clicking it is the ONLY way `onCheckAnyway` runs on such a
 * page -- nothing here calls it automatically.
 */
export function showCheckAnywayButton(
  onCheckAnyway: () => void,
  doc: Document = document
): Ws1UiHandle {
  teardownWs1Ui(doc);
  const { host, shadow } = mount(doc);
  const chip = doc.createElement("div");
  chip.className = "ws1-chip";
  const button = doc.createElement("button");
  button.type = "button";
  button.className = "ws1-button";
  button.textContent = "Check this page anyway";
  button.addEventListener("click", () => {
    host.remove();
    onCheckAnyway();
  });
  chip.appendChild(button);
  shadow.appendChild(chip);
  return { teardown: () => host.remove() };
}
