import { classifyHostname } from "../lib/hostname-match";
import { ANALYZE_MESSAGE, type AnalysisRequest } from "../content/analysis-client";
import { isAnalysisResponse, type AnalysisResponse } from "../shared/contract";
import { analyzeViaBackend } from "./backend-client";
import { getBackendConfig } from "./config";
import { buildFixtureResponse } from "./fixture";

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

function malformedResponseEnvelope(url: string): AnalysisResponse {
  return {
    schemaVersion: "1.0",
    url,
    status: "failed",
    articleVerdict: {
      level: "unrated",
      summary: "The fact-check backend returned a response this extension could not understand.",
    },
    verifiedClaims: [],
    errors: [
      { code: "malformed_response", message: "Response failed isAnalysisResponse()." },
    ],
  };
}

async function handleAnalyze(payload: AnalysisRequest): Promise<AnalysisResponse> {
  // Read fresh every call -- see config.ts's doc comment. Nothing here is cached at
  // module scope: the service worker can be killed and restarted between messages, so
  // a module-level cache is not guaranteed to still be valid (or to exist) later.
  const config = await getBackendConfig();

  const envelope = config.useFixture
    ? buildFixtureResponse(payload.url)
    : await analyzeViaBackend(payload, config);

  // Defense in depth: validate whatever produced `envelope` -- the fixture, a live
  // backend response, or backend-client.ts's own failure fallback -- against the exact
  // guard the content script runs (src/shared/contract.ts's isAnalysisResponse) before
  // it ever leaves this worker. A malformed envelope reaching the content script is
  // silently dropped there with no error surfaced anywhere (see
  // docs/ws3/WS3-CONTRACT-AUDIT.md, Task A), so catching it HERE -- where we can at least log it
  // -- is strictly better than trusting every producer to have gotten the shape right.
  if (!isAnalysisResponse(envelope)) {
    console.error(
      "[WS3] backend/fixture produced a response that fails isAnalysisResponse():",
      envelope,
    );
    return malformedResponseEnvelope(payload.url);
  }
  return envelope;
}

// CRITICAL: this listener must stay a plain (non-async) function. Chrome's
// chrome.runtime.onMessage does NOT support a listener returning a Promise the way
// Firefox's WebExtensions API does (which is why most onMessage examples online use
// `async (message, sender, sendResponse) => {...}` and are simply wrong for Chrome) --
// Chrome ignores that returned promise entirely, so sendResponse fires whenever the
// async work happens to finish, generally after the message channel has already
// closed, and the content script's sendMessage() call just hangs until it times out.
// The correct MV3 pattern: stay synchronous, kick off the async work, return `true`
// synchronously to tell Chrome "I will call sendResponse later, keep the channel
// open", and call sendResponse from inside the promise chain below. Do not "clean
// this up" into an async function.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (
    !message ||
    typeof message !== "object" ||
    (message as { type?: unknown }).type !== ANALYZE_MESSAGE
  ) {
    return false; // not ours -- let it fall through, nothing to respond with
  }

  const payload = (message as { payload?: unknown }).payload;
  if (!payload || typeof (payload as { url?: unknown }).url !== "string") {
    sendResponse(malformedResponseEnvelope(""));
    return false; // responded synchronously; no async work, channel can close
  }

  // All per-request state lives here, in this closure (`payload`, and everything
  // handleAnalyze/analyzeViaBackend derive from it) -- never in a module-level
  // variable. The service worker can be torn down and restarted at any point,
  // including mid-request; anything held in module scope between the request coming
  // in and the response going out would simply be gone when the worker wakes back up
  // to resolve the promise chain below, silently dropping the caller's request.
  handleAnalyze(payload as AnalysisRequest)
    .catch((err: unknown) => {
      // handleAnalyze/analyzeViaBackend already turn every failure mode (network,
      // timeout, malformed shape) into a resolved envelope -- this only fires for a
      // genuine bug (e.g. isAnalysisResponse itself throwing), which must still never
      // reach sendResponse as a raw, unparseable error.
      console.error("[WS3] handleAnalyze threw unexpectedly:", err);
      return malformedResponseEnvelope((payload as AnalysisRequest).url);
    })
    .then((envelope) => {
      try {
        sendResponse(envelope);
      } catch {
        // The content script's port can die before we respond -- the tab was closed,
        // navigated away, or bootstrap.ts's SPA-navigation handler already tore down
        // and re-mounted the fact-check UI (teardownFactCheck() / a fresh
        // startFactCheck() call). sendResponse throwing here means nobody is
        // listening any more; there is nothing to recover, so just don't crash the
        // worker over it.
      }
    });

  return true; // async response coming -- keep the message channel open
});
