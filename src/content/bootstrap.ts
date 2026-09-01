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
    .then((response) => {
      // Log the outcome. Without this the console goes silent on success, which is
      // indistinguishable from the request never having been sent — and the failure
      // path uses console.debug, which Chrome hides unless Verbose is enabled.
      const t2 = response.tier2;
      console.log(
        `[WS2] verdict=${response.articleVerdict.level} status=${response.status} ` +
          `claims=${response.verifiedClaims.length}` +
          (t2
            ? ` | tier2 escalated=${t2.escalated} text=${t2.textScore.toFixed(3)} ` +
              `image=${t2.maxImageScore.toFixed(3)} ${t2.latencyMs.toFixed(0)}ms`
            : ""),
        t2?.reasons ?? [],
      );
      return response.status === "skipped"
        ? controller.showTrusted(response.articleVerdict.summary)
        : controller.render(response);
    })
    .catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      // The service worker isn't answering at all — it crashed, or the extension
      // needs reloading after a rebuild. Distinct from "the backend is down",
      // which the worker reports with its own message.
      const workerUnreachable =
        message.includes("Could not establish connection") ||
        message.includes("Receiving end does not exist") ||
        message.includes("messaging unavailable");
      if (workerUnreachable) {
        console.warn(
          "[WS2] extension service worker not responding — reload the extension at " +
            "chrome://extensions, then reload this page. (Or add ?dasfaxMock=1 to test " +
            "rendering without a backend.)",
          error,
        );
        controller.showError("Extension not responding — reload it");
      } else {
        // Includes "backend not reachable" surfaced by the service worker.
        console.warn("[WS2] analysis failed:", message);
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
