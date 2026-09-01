/**
 * Tier 0 whitelist store.
 *
 * Starter set of trusted Singapore and major international news domains.
 * This is a draft list for WS1 triage — refine/extend as needed.
 */
export const WHITELISTED_DOMAINS: readonly string[] = [
  // Singapore — IMDA-licensed online news sites
  // (straitstimes.com covers www./stomp./tnp.straitstimes.com via subdomain matching)
  "straitstimes.com",
  "businesstimes.com.sg",
  "tnp.sg",
  "zaobao.com.sg",
  "channelnewsasia.com",
  "todayonline.com",
  "sg.news.yahoo.com",
  "asiaone.com",
  "mothership.sg",
  // Major international
  "bbc.com",
  "reuters.com",
  "apnews.com",
  "nytimes.com",
  "theguardian.com",
  "bloomberg.com",
  "wsj.com",
];
