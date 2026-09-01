import { classifyHostname } from "../lib/hostname-match";

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
