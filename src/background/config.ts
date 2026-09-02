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
 *   chrome.storage.local.set({ dasfaxUseFixture: false })
 *
 * Overrides persist in storage.local (survives the service worker being killed and
 * restarted -- see the note on getBackendConfig() below) until removed with
 * chrome.storage.local.remove([...]) or chrome.storage.local.clear().
 */

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";
const DEFAULT_TIMEOUT_MS = 15000;
/** Default true: a fresh unpacked-extension load must work with zero backend running. */
const DEFAULT_USE_FIXTURE = true;

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
