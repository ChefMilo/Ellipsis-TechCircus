import { classifyHostname } from "../lib/hostname-match";
import { isProbablyArticle, ARTICLE_CONFIDENCE_THRESHOLD } from "../lib/article-heuristic";

const LOCATION_CHANGE_EVENT = "ws1:locationchange";
// SPA route changes can fire before the new view has rendered; give the
// page a brief moment to settle before re-scoring the DOM.
const SPA_RESCORE_DELAY_MS = 300;

function runTriage(): void {
  const url = location.href;
  const tier0 = classifyHostname(url);

  if (tier0 === "whitelisted") {
    console.log(`[WS1 Tier0] whitelisted: ${url} (skipping Tier 1 heuristic)`);
    return;
  }

  const tier1 = isProbablyArticle(document);
  console.log(
    `[WS1 Tier0] not_whitelisted: ${url} | ` +
      `[WS1 Tier1] isArticle=${tier1.isArticle} confidence=${tier1.confidence.toFixed(2)} ` +
      `(threshold=${ARTICLE_CONFIDENCE_THRESHOLD})`
  );
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
    setTimeout(runTriage, SPA_RESCORE_DELAY_MS);
  });
}

bootstrap();
