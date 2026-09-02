/**
 * Owns the one network call this extension makes: POST {baseUrl}/analyze.
 *
 * Never throws and never rejects its returned promise. Every failure mode -- network
 * error, non-2xx, timeout -- resolves to a well-formed AnalysisResponse instead, so
 * whatever calls this always has a valid envelope to hand to isAnalysisResponse() and
 * never has to special-case "the fetch itself blew up" versus "the backend answered
 * with a real, structured error". A malformed response BODY (a 2xx with JSON that
 * doesn't satisfy the contract) is deliberately NOT validated here -- that check lives
 * once, centrally, in service-worker.ts's handleAnalyze(), so every source of an
 * envelope (this module, the fixture) is checked the same way in one place.
 */
import type { AnalysisRequest } from "../content/analysis-client";
import type { AnalysisResponse } from "../shared/contract";
import type { BackendConfig } from "./config";

function failureEnvelope(url: string, code: string, message: string): AnalysisResponse {
  return {
    schemaVersion: "1.0",
    url,
    status: "failed",
    articleVerdict: {
      level: "unrated",
      summary: "The fact-check backend could not be reached, so this page was not checked.",
    },
    verifiedClaims: [],
    errors: [{ code, message }],
  };
}

export async function analyzeViaBackend(
  payload: AnalysisRequest,
  config: BackendConfig,
): Promise<AnalysisResponse> {
  // Request-scoped on purpose: a fresh AbortController/timer per call, never hoisted to
  // module scope, so a slow request from a page the user already left never lingers
  // and never gets confused with a later, unrelated request after a worker restart.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);

  try {
    const response = await fetch(`${config.baseUrl}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });

    if (!response.ok) {
      return failureEnvelope(
        payload.url,
        "backend_http_error",
        `Backend responded ${response.status} ${response.statusText}`,
      );
    }

    return (await response.json()) as AnalysisResponse;
  } catch (err) {
    const isAbort = err instanceof DOMException && err.name === "AbortError";
    return failureEnvelope(
      payload.url,
      isAbort ? "backend_timeout" : "backend_unreachable",
      isAbort
        ? `Backend did not respond within ${config.timeoutMs}ms`
        : `Backend request failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  } finally {
    clearTimeout(timer);
  }
}
