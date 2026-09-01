import { classifyHostname } from "../lib/hostname-match";
import { ANALYZE_MESSAGE, type AnalysisRequest } from "../content/analysis-client";

// Tier 0 triage: on every tab navigation, classify the hostname and log the
// result. No UI, no backend calls — this just decides what's worth
// checking further downstream.
chrome.tabs.onUpdated.addListener((_tabId, changeInfo, tab) => {
  if (!changeInfo.url || !tab.url) {
    return;
  }

  const result = classifyHostname(tab.url);
  console.log(`[WS1 Tier0] ${result}: ${tab.url}`);
});

/**
 * Backend origin. Under MV3 the service worker is the only place a network call
 * may originate, so this is the single point where the extension talks to the
 * backend. Must stay in sync with `host_permissions` in extension/manifest.json —
 * a fetch to a host that isn't listed there fails as a CORS error with no
 * useful message.
 */
const BACKEND_ORIGIN = "http://127.0.0.1:8000";
const ANALYZE_URL = `${BACKEND_ORIGIN}/analyze`;

/**
 * Give up before the user does. The backend's own Tier 2 budget is 750ms and a
 * Tier 3 escalation adds LLM and search calls on top, so this is deliberately
 * generous — but it must be finite, or a hung backend leaves the in-page panel
 * spinning forever with no way to retry.
 */
const REQUEST_TIMEOUT_MS = 30_000;

async function analyze(payload: AnalysisRequest): Promise<unknown> {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(ANALYZE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: abort.signal,
    });
    if (!response.ok) {
      // 422 means our payload didn't match the backend contract — worth saying
      // so explicitly, because it's a wiring bug rather than a service outage.
      const detail = response.status === 422 ? " (payload rejected by backend)" : "";
      throw new Error(`backend returned ${response.status}${detail}`);
    }
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== ANALYZE_MESSAGE) {
    return false; // not ours; let another listener handle it
  }

  analyze(message.payload as AnalysisRequest)
    .then(sendResponse)
    .catch((error: unknown) => {
      const reason = error instanceof Error ? error.message : String(error);
      const offline = reason.includes("Failed to fetch") || reason.includes("aborted");
      console.warn(`[WS3] analyze failed: ${reason}`);
      // The content script turns an { error } reply into a visible failure state.
      // Say which of the two failures it was: a backend that isn't running looks
      // identical to one that's broken unless we distinguish them here.
      sendResponse({
        error: offline
          ? `Fact-check backend not reachable at ${BACKEND_ORIGIN}`
          : `Fact-check failed: ${reason}`,
      });
    });

  return true; // keep the message channel open for the async sendResponse
});
