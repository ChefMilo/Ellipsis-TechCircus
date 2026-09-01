/**
 * Fetches the fact-check result for the current page.
 *
 * Production path: ask the background service worker (the only place a network
 * call may originate under MV3) and validate the reply against the contract.
 *
 * Dev path: when `?dasfaxMock=1` is in the URL, or `localStorage['dasfax:mock']`
 * is set, resolve a bundled mock instead — so WS2 can be exercised on any real
 * news site before WS3 / the backend fetch exist. The mock's `url` is rewritten
 * to the live page so downstream code behaves normally.
 */
import { isAnalysisResponse, type AnalysisResponse } from "../shared/contract";
import { MOCK_ANALYSIS } from "./render/__mock__/mock-response";

export interface AnalysisRequest {
  url: string;
  title?: string;
  /** Cleaned article text from WS1 extraction, when available. */
  text?: string;
  images?: string[];
}

export const ANALYZE_MESSAGE = "dasfax:analyze";

const MOCK_LATENCY_MS = 600;

export function isMockMode(): boolean {
  try {
    if (new URLSearchParams(location.search).get("dasfaxMock") === "1") return true;
    if (localStorage.getItem("dasfax:mock")) return true;
  } catch {
    /* access to location/localStorage can throw in odd frames */
  }
  return false;
}

function mockResponse(): Promise<AnalysisResponse> {
  const cloned: AnalysisResponse = {
    ...structuredClone(MOCK_ANALYSIS),
    url: location.href,
  };
  return new Promise((resolve) => setTimeout(() => resolve(cloned), MOCK_LATENCY_MS));
}

export async function requestAnalysis(
  payload: AnalysisRequest,
): Promise<AnalysisResponse> {
  if (isMockMode()) return mockResponse();

  const runtime = (chrome as unknown as { runtime?: chrome.runtime.ExtensionContext & typeof chrome.runtime })
    .runtime;
  if (!runtime?.sendMessage) {
    throw new Error("dasfax: extension messaging unavailable");
  }

  const reply: unknown = await runtime.sendMessage({
    type: ANALYZE_MESSAGE,
    payload,
  });

  if (reply && typeof reply === "object" && "error" in reply) {
    throw new Error(String((reply as { error: unknown }).error));
  }
  if (!isAnalysisResponse(reply)) {
    throw new Error("dasfax: malformed analysis response");
  }
  return reply;
}
