/**
 * The single Shadow DOM host that carries all of WS2's UI (summary pill +
 * evidence drawer). Open mode so tests can introspect; `:host { all: initial }`
 * plus the closed style boundary keep the host page's CSS out.
 */
import { STATUS, STATUS_KEYS } from "./status-config";

export interface ShadowUI {
  host: HTMLElement;
  shadow: ShadowRoot;
  /** Remove the host from the page entirely. */
  destroy(): void;
}

export const HOST_TAG = "dasfax-root";

function statusVars(): string {
  return STATUS_KEYS.map(
    (k) => `    --dasfax-${k}-accent: ${STATUS[k].accent};
    --dasfax-${k}-tint: ${STATUS[k].tint};`,
  ).join("\n");
}

const CSS = `
:host {
  all: initial;
  position: fixed;
  inset: 0;
  z-index: 2147483647;
  pointer-events: none;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  --dasfax-bg: #ffffff;
  --dasfax-fg: #1f2328;
  --dasfax-muted: #57606a;
  --dasfax-border: #d0d7de;
  --dasfax-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
  --dasfax-radius: 12px;
${statusVars()}
}
@media (prefers-color-scheme: dark) {
  :host {
    --dasfax-bg: #1c2128;
    --dasfax-fg: #e6edf3;
    --dasfax-muted: #9198a1;
    --dasfax-border: #3d444d;
    --dasfax-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  }
}

* { box-sizing: border-box; }
button { font: inherit; color: inherit; cursor: pointer; }

.dasfax-layer { pointer-events: none; }
.dasfax-layer > * { pointer-events: auto; }

/* --- summary pill ------------------------------------------------------- */
.dasfax-pill {
  position: fixed;
  right: 20px;
  bottom: 20px;
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 320px;
  padding: 10px 14px;
  background: var(--dasfax-bg);
  color: var(--dasfax-fg);
  border: 1px solid var(--dasfax-border);
  border-radius: 999px;
  box-shadow: var(--dasfax-shadow);
}
.dasfax-pill__button {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 0;
  background: transparent;
  padding: 0;
}
.dasfax-pill__dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: none;
  background: var(--dasfax-muted);
}
.dasfax-pill[data-level="ok"] .dasfax-pill__dot { background: var(--dasfax-supported-accent); }
.dasfax-pill[data-level="caution"] .dasfax-pill__dot { background: var(--dasfax-partially_supported-accent); }
.dasfax-pill[data-level="high_risk"] .dasfax-pill__dot { background: var(--dasfax-contradicted-accent); }
.dasfax-pill[data-level="trusted"] .dasfax-pill__dot { background: var(--dasfax-supported-accent); }
.dasfax-pill[data-level="unrated"] .dasfax-pill__dot { background: var(--dasfax-muted); }
.dasfax-pill__text { font-weight: 600; white-space: nowrap; }
.dasfax-pill__sub { color: var(--dasfax-muted); font-weight: 400; }
.dasfax-pill__dismiss {
  border: 0;
  background: transparent;
  color: var(--dasfax-muted);
  padding: 4px;
  border-radius: 6px;
  line-height: 0;
}
.dasfax-pill__dismiss:hover { color: var(--dasfax-fg); }

.dasfax-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--dasfax-border);
  border-top-color: var(--dasfax-fg);
  border-radius: 50%;
  animation: dasfax-spin 0.7s linear infinite;
  flex: none;
}
@keyframes dasfax-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .dasfax-spinner { animation-duration: 2s; }
}

.dasfax-collapsed {
  position: fixed;
  right: 20px;
  bottom: 20px;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 1px solid var(--dasfax-border);
  background: var(--dasfax-bg);
  color: var(--dasfax-fg);
  box-shadow: var(--dasfax-shadow);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}

/* --- drawer ----------------------------------------------------------- */
.dasfax-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.28);
  opacity: 0;
  transition: opacity 0.15s ease;
}
.dasfax-backdrop[data-open="true"] { opacity: 1; }

.dasfax-drawer {
  position: fixed;
  top: 0;
  right: 0;
  height: 100%;
  width: min(420px, 92vw);
  background: var(--dasfax-bg);
  color: var(--dasfax-fg);
  border-left: 1px solid var(--dasfax-border);
  box-shadow: var(--dasfax-shadow);
  transform: translateX(100%);
  transition: transform 0.18s ease;
  display: flex;
  flex-direction: column;
}
.dasfax-drawer[data-open="true"] { transform: translateX(0); }
@media (prefers-reduced-motion: reduce) {
  .dasfax-backdrop, .dasfax-drawer { transition: none; }
}

.dasfax-drawer__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--dasfax-border);
}
.dasfax-drawer__title { font-weight: 700; font-size: 15px; }
.dasfax-drawer__close {
  border: 0;
  background: transparent;
  color: var(--dasfax-muted);
  padding: 6px;
  border-radius: 6px;
  line-height: 0;
}
.dasfax-drawer__close:hover { color: var(--dasfax-fg); }
.dasfax-drawer__body { overflow-y: auto; padding: 16px; flex: 1; }

.dasfax-verdict {
  font-size: 13px;
  color: var(--dasfax-muted);
  margin: 0 0 14px;
}

.dasfax-claim-list { display: flex; flex-direction: column; gap: 8px; }
.dasfax-claim-row {
  width: 100%;
  text-align: left;
  border: 1px solid var(--dasfax-border);
  background: transparent;
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.dasfax-claim-row:hover { border-color: var(--dasfax-muted); }
.dasfax-claim-row[aria-current="true"] { border-color: var(--dasfax-fg); }

.dasfax-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  color: #fff;
}
.dasfax-badge[data-status] { background: var(--dasfax-muted); }
${STATUS_KEYS.map((k) => `.dasfax-badge[data-status="${k}"] { background: var(--dasfax-${k}-accent); }`).join("\n")}
.dasfax-badge__icon { line-height: 0; }

.dasfax-claim-detail { margin-top: 4px; }
.dasfax-claim-detail__text {
  font-size: 14px;
  margin: 8px 0 12px;
}
.dasfax-claim-detail__meta { color: var(--dasfax-muted); font-size: 12px; margin-bottom: 10px; }
.dasfax-claim-detail__explanation { margin: 10px 0 16px; }
.dasfax-claim-detail__unanchored {
  font-size: 12px;
  color: var(--dasfax-muted);
  border: 1px dashed var(--dasfax-border);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 12px;
}

.dasfax-sources { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.dasfax-source {
  border: 1px solid var(--dasfax-border);
  border-radius: 8px;
  padding: 8px 10px;
}
.dasfax-source__title {
  font-weight: 600;
  font-size: 13px;
  color: inherit;
  text-decoration: none;
}
.dasfax-source__title:hover { text-decoration: underline; }
.dasfax-source__domain { color: var(--dasfax-muted); font-size: 12px; }
.dasfax-source__snippet { font-size: 12px; color: var(--dasfax-muted); margin-top: 4px; }

.dasfax-back {
  border: 0;
  background: transparent;
  color: var(--dasfax-muted);
  padding: 4px 0;
  margin-bottom: 8px;
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
}
.dasfax-back:hover { color: var(--dasfax-fg); }

.dasfax-error { color: var(--dasfax-contradicted-accent); font-weight: 600; }
.dasfax-retry {
  border: 1px solid var(--dasfax-border);
  background: transparent;
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 13px;
}

:focus-visible {
  outline: 2px solid var(--dasfax-partially_supported-accent);
  outline-offset: 2px;
}
.dasfax-visually-hidden {
  position: absolute;
  width: 1px; height: 1px;
  margin: -1px; padding: 0; border: 0;
  clip: rect(0 0 0 0); clip-path: inset(50%);
  overflow: hidden; white-space: nowrap;
}
`;

/** Creates the host + shadow root and injects scoped styles. Idempotent per call. */
export function createShadowRoot(doc: Document = document): ShadowUI {
  const host = doc.createElement(HOST_TAG);
  host.setAttribute("data-dasfax", "root");
  const shadow = host.attachShadow({ mode: "open" });

  const style = doc.createElement("style");
  style.textContent = CSS;
  shadow.appendChild(style);

  const layer = doc.createElement("div");
  layer.className = "dasfax-layer";
  shadow.appendChild(layer);

  (doc.documentElement || doc.body).appendChild(host);

  return {
    host,
    shadow,
    destroy() {
      host.remove();
    },
  };
}

/** The `.dasfax-layer` element inside a shadow root. */
export function layerOf(shadow: ShadowRoot): HTMLElement {
  const layer = shadow.querySelector<HTMLElement>(".dasfax-layer");
  if (!layer) throw new Error("dasfax: shadow layer missing");
  return layer;
}
