import { classifyHostname } from "../lib/hostname-match";
import { isProbablyArticle, ARTICLE_CONFIDENCE_THRESHOLD } from "../lib/article-heuristic";
import { extractArticle } from "../lib/article-extractor";
import { mountFactCheckUI, type FactCheckController } from "./render/controller";
import { requestAnalysis, isMockMode } from "./analysis-client";
import { showCheckAnywayButton, showTrustedBadge, teardownWs1Ui } from "./trusted-ui";

const LOCATION_CHANGE_EVENT = "ws1:locationchange";
// SPA route changes can fire before the new view has rendered; give the
// page a brief moment to settle before re-scoring the DOM.
const SPA_RESCORE_DELAY_MS = 300;

// WS2: the in-page fact-check UI for the current page, torn down and rebuilt on
// SPA navigation.
let factCheck: FactCheckController | null = null;

function teardownFactCheck(): void {
  factCheck?.teardown();
  factCheck = null;
}

function startFactCheck(): void {
  teardownFactCheck();
  const controller = mountFactCheckUI({
    articleRoot: document.body,
    onRetry: () => startFactCheck(),
  });
  factCheck = controller;
  controller.showLoading();

  const url = location.href;
  // WS1 Tier 2: local-only extraction, run fresh right before the request
  // so the backend gets the actual cleaned article -- not just url/title.
  const extracted = extractArticle(document, url, {
    whitelisted: false,
    articleConfidence: isProbablyArticle(document).confidence,
  });
  if (extracted) {
    console.log("[WS1 Tier2] extracted article:", extracted);
  } else {
    // `text` is optional on this endpoint: POST /analyze tolerates a
    // missing body and returns a 200 with errors: [{ code: "no_content" }],
    // not a validation error -- confirmed with WS3 2026-09-01. WS2's panel
    // can key off that code for a "couldn't read this page" state.
    console.log(
      "[WS1 Tier2] Readability could not extract content -- sending url/title only"
    );
  }

  requestAnalysis({
    url,
    title: document.title,
    // Sent straight through, untouched -- WS5's claim offsets index into
    // this exact string. Do not re-normalize/reflow it here.
    text: extracted?.bodyText,
    images: extracted?.imageUrls,
    published_at: extracted?.publishDate ?? undefined,
    source_domain: extracted?.sourceDomain ?? undefined,
  })
    .then((response) =>
      response.status === "skipped"
        ? controller.showTrusted(response.articleVerdict.summary)
        : controller.render(response),
    )
    .catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      // Until WS3 wires the background <-> backend path, every non-mock page hits
      // this. It's expected, not a fault — keep it out of chrome://extensions
      // Errors (which only collects console.warn/error), and point at mock mode.
      const noBackendYet =
        message.includes("Could not establish connection") ||
        message.includes("Receiving end does not exist") ||
        message.includes("messaging unavailable");
      if (noBackendYet) {
        console.debug(
          "[WS2] no analysis backend yet (WS3 not wired) — add ?dasfaxMock=1 to test rendering",
        );
        controller.showError("Fact-check backend not connected");
      } else {
        console.warn("[WS2] analysis failed", error);
        controller.showError();
      }
    });
}

function runTriage(): void {
  const url = location.href;
  const tier0 = classifyHostname(url);

  // Dev harness: `?dasfaxMock=1` renders WS2 on any page, bypassing triage —
  // needed because Tier 0 whitelists Straits Times / CNA and would otherwise
  // stop before WS2 ever runs.
  if (isMockMode()) {
    console.log(`[WS2] mock mode: rendering on ${url}`);
    teardownWs1Ui();
    startFactCheck();
    return;
  }

  if (tier0 === "whitelisted") {
    console.log(`[WS1 Tier0] whitelisted: ${url} (skipping Tier 1 heuristic)`);
    teardownFactCheck();
    showTrustedBadge();
    return;
  }

  const tier1 = isProbablyArticle(document);
  console.log(
    `[WS1 Tier0] not_whitelisted: ${url} | ` +
      `[WS1 Tier1] isArticle=${tier1.isArticle} confidence=${tier1.confidence.toFixed(2)} ` +
      `(threshold=${ARTICLE_CONFIDENCE_THRESHOLD})`
  );

  if (!tier1.isArticle) {
    teardownFactCheck();
    // No automatic backend call on a page we don't think is an article --
    // only this explicit, user-initiated override runs startFactCheck().
    showCheckAnywayButton(() => {
      console.log(`[WS1] user override: forcing check on ${url}`);
      startFactCheck();
    });
    return;
  }

  teardownWs1Ui();
  startFactCheck();
}

function patchHistoryMethod(methodName: "pushState" | "replaceState"): void {
  const original = history[methodName];
  history[methodName] = function (this: History, ...args: unknown[]) {
    const result = (original as (...a: unknown[]) => unknown).apply(this, args);
    window.dispatchEvent(new Event(LOCATION_CHANGE_EVENT));
    return result;
  } as History[typeof methodName];
}

function bootstrap(): void {
  runTriage();

  // Many news sites are SPAs where client-side routing never fires a full
  // page load, so also re-run triage on history navigation.
  patchHistoryMethod("pushState");
  patchHistoryMethod("replaceState");
  window.addEventListener("popstate", () =>
    window.dispatchEvent(new Event(LOCATION_CHANGE_EVENT))
  );
  window.addEventListener(LOCATION_CHANGE_EVENT, () => {
    teardownFactCheck();
    teardownWs1Ui();
    setTimeout(runTriage, SPA_RESCORE_DELAY_MS);
  });
}

bootstrap();
