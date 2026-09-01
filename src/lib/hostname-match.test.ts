import { describe, expect, it } from "vitest";
import { classifyHostname } from "./hostname-match";

describe("classifyHostname", () => {
  it("matches an exact whitelisted domain", () => {
    expect(classifyHostname("https://straitstimes.com/article/1")).toBe(
      "whitelisted"
    );
  });

  it("matches a www subdomain of a whitelisted domain", () => {
    expect(classifyHostname("https://www.straitstimes.com/article/1")).toBe(
      "whitelisted"
    );
  });

  it("matches an arbitrary subdomain (m.) of a whitelisted domain", () => {
    expect(classifyHostname("https://m.straitstimes.com/article/1")).toBe(
      "whitelisted"
    );
  });

  it("matches a nested subdomain of a whitelisted domain", () => {
    expect(
      classifyHostname("https://amp.www.channelnewsasia.com/story")
    ).toBe("whitelisted");
  });

  it("is case-insensitive", () => {
    expect(classifyHostname("https://WWW.BBC.COM/news")).toBe("whitelisted");
  });

  it("rejects a spoofed domain where the trusted name is a prefix label", () => {
    expect(
      classifyHostname("https://straitstimes.com.evil.net/phish")
    ).toBe("not_whitelisted");
  });

  it("rejects a spoofed domain that merely contains the trusted name", () => {
    expect(classifyHostname("https://evilstraitstimes.com/phish")).toBe(
      "not_whitelisted"
    );
  });

  it("rejects a spoofed domain using userinfo to disguise the real host", () => {
    expect(
      classifyHostname("https://straitstimes.com@evil.net/phish")
    ).toBe("not_whitelisted");
  });

  it("rejects an unrelated, non-whitelisted news-like domain", () => {
    expect(classifyHostname("https://example.com/article")).toBe(
      "not_whitelisted"
    );
  });

  it("rejects a non-article site not on the whitelist", () => {
    expect(classifyHostname("https://shopee.sg/product/123")).toBe(
      "not_whitelisted"
    );
  });

  it("returns not_whitelisted for an invalid/unparseable URL", () => {
    expect(classifyHostname("not-a-valid-url")).toBe("not_whitelisted");
  });

  it("matches a second-level ccTLD whitelisted domain (.com.sg)", () => {
    expect(classifyHostname("https://www.businesstimes.com.sg/story")).toBe(
      "whitelisted"
    );
  });

  it("matches Straits Times' stomp. and tnp. subdomains", () => {
    expect(classifyHostname("https://stomp.straitstimes.com/story")).toBe(
      "whitelisted"
    );
    expect(classifyHostname("https://tnp.straitstimes.com/story")).toBe(
      "whitelisted"
    );
  });

  it("matches The New Paper's own domain (tnp.sg), distinct from tnp.straitstimes.com", () => {
    expect(classifyHostname("https://www.tnp.sg/story")).toBe("whitelisted");
  });

  it("matches the Yahoo News Singapore subdomain exactly, without whitelisting all of yahoo.com", () => {
    expect(classifyHostname("https://sg.news.yahoo.com/story")).toBe(
      "whitelisted"
    );
    expect(classifyHostname("https://finance.yahoo.com/quote/AAPL")).toBe(
      "not_whitelisted"
    );
    expect(classifyHostname("https://www.yahoo.com/")).toBe(
      "not_whitelisted"
    );
  });

  it("matches AsiaOne", () => {
    expect(classifyHostname("https://www.asiaone.com/singapore/story")).toBe(
      "whitelisted"
    );
  });
});
