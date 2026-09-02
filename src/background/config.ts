/**
 * Background/service-worker configuration.
 *
 * Every knob here has a hardcoded default and is independently overridable at RUNTIME
 * via chrome.storage.local -- no rebuild, no code edit, no env var. From the service
 * worker's own DevTools console (chrome://extensions -> this extension -> "service
 * worker" link):
 *
 *   chrome.storage.local.set({ dasfaxBackendBaseUrl: "http://localhost:9000" })
 *   chrome.storage.local.set({ dasfaxRequestTimeoutMs: 30000 })
 *   chrome.storage.local.set({ dasfaxUseFixture: true })   // offline fixture path
 *
 * Overrides persist in storage.local (survives the service worker being killed and
 * restarted -- see the note on getBackendConfig() below) until removed with
 * chrome.storage.local.remove([...]) or chrome.storage.local.clear().
 */

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";
/**
 * Client-side fetch budget. Deliberately ABOVE the backend's own Tier 3 budget
 * (backend/app/orchestrator/config.py's tier3_timeout_s, 120s) so the BACKEND gives up
 * first and answers with errors[{ code: "tier3_timeout" }] -- a cause you can read --
 * instead of this AbortController firing blind and reporting "backend_unreachable",
 * which looks identical to the server being down. If you lower the backend budget,
 * lower this too, keeping this one higher.
 */
const DEFAULT_TIMEOUT_MS = 130000;
/**
 * Default false: a normal load calls the real backend, which is what a live demo needs.
 *
 * The fixture path is NOT removed -- it is still the offline fallback for a venue with
 * no network, and is one command away in the service worker's console (no rebuild):
 *
 *   chrome.storage.local.set({ dasfaxUseFixture: true })
 *
 * It defaulted to true while the backend hop was being built, when "works with zero
 * backend running" was the point. That default now costs more than it saves: the
 * fixture is contract-valid and renders convincingly, so a run that silently served it
 * looks exactly like a successful end-to-end run against the real pipeline.
 */
const DEFAULT_USE_FIXTURE = false;

const STORAGE_KEYS = {
  baseUrl: "dasfaxBackendBaseUrl",
  timeoutMs: "dasfaxRequestTimeoutMs",
  useFixture: "dasfaxUseFixture",
} as const;

export interface BackendConfig {
  baseUrl: string;
  timeoutMs: number;
  useFixture: boolean;
}

/**
 * Reads the current config fresh from chrome.storage.local on every call. Deliberately
 * NOT cached in a module-level variable: the MV3 service worker can be torn down and
 * restarted between messages (or, per Chrome's docs, even mid-request under memory
 * pressure), so anything cached at module scope is not guaranteed to survive to the
 * next call. storage.local is the one thing here that reliably does.
 */
export async function getBackendConfig(): Promise<BackendConfig> {
  let stored: Record<string, unknown> = {};
  try {
    stored = await chrome.storage.local.get([
      STORAGE_KEYS.baseUrl,
      STORAGE_KEYS.timeoutMs,
      STORAGE_KEYS.useFixture,
    ]);
  } catch {
    // storage unavailable for some reason -- fall back to defaults rather than
    // failing the whole request over a config read.
  }

  const storedBaseUrl = stored[STORAGE_KEYS.baseUrl];
  const storedTimeout = stored[STORAGE_KEYS.timeoutMs];
  const storedUseFixture = stored[STORAGE_KEYS.useFixture];

  return {
    baseUrl: typeof storedBaseUrl === "string" && storedBaseUrl ? storedBaseUrl : DEFAULT_BASE_URL,
    timeoutMs:
      typeof storedTimeout === "number" && storedTimeout > 0 ? storedTimeout : DEFAULT_TIMEOUT_MS,
    useFixture: typeof storedUseFixture === "boolean" ? storedUseFixture : DEFAULT_USE_FIXTURE,
  };
}
