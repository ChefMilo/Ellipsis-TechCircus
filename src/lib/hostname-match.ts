import { WHITELISTED_DOMAINS } from "./whitelist";

export type TriageResult = "whitelisted" | "not_whitelisted";

/**
 * Returns true if `hostname` is exactly `domain` or a subdomain of it
 * (e.g. "www.straitstimes.com" matches "straitstimes.com", but
 * "straitstimes.com.evil.net" does not).
 */
function isSameOrSubdomain(hostname: string, domain: string): boolean {
  return hostname === domain || hostname.endsWith(`.${domain}`);
}

/**
 * Classifies a tab URL as "whitelisted" or "not_whitelisted" based on its
 * hostname. Matches the exact registered domain and any of its subdomains,
 * but never a domain that merely contains the whitelisted string (e.g. as a
 * suffix-spoofed subdomain of an unrelated attacker-controlled domain).
 */
export function classifyHostname(url: string): TriageResult {
  let hostname: string;
  try {
    hostname = new URL(url).hostname.toLowerCase();
  } catch {
    return "not_whitelisted";
  }

  // Strip a trailing FQDN dot (e.g. "straitstimes.com.") before comparing.
  hostname = hostname.replace(/\.$/, "");

  if (!hostname) {
    return "not_whitelisted";
  }

  const isWhitelisted = WHITELISTED_DOMAINS.some((domain) =>
    isSameOrSubdomain(hostname, domain)
  );

  return isWhitelisted ? "whitelisted" : "not_whitelisted";
}
