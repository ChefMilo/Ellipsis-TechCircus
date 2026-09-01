// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { extractArticle } from "./article-extractor";

const TEST_URL = "https://example.news/story/123";

function longParagraph(sentenceCount: number): string {
  const sentence =
    "This is a reasonably long sentence written to simulate real article prose. ";
  return sentence.repeat(sentenceCount);
}

function setPage(bodyHtml: string, headHtml = ""): void {
  document.head.innerHTML = headHtml;
  document.body.innerHTML = bodyHtml;
}

const PARAGRAPHS = Array.from({ length: 8 }, () => `<p>${longParagraph(3)}</p>`).join("\n");

describe("extractArticle", () => {
  it("extracts title, body text, author, and date from a well-formed article", () => {
    setPage(
      `
      <nav>Home / World / Singapore</nav>
      <article>
        <h1>Long headline about something newsworthy happening today</h1>
        <div class="byline">By Jane Tan</div>
        <time datetime="2026-09-01T08:00:00Z">1 Sep 2026</time>
        <img src="https://example.news/images/hero.jpg" width="800" height="450" />
        ${PARAGRAPHS}
      </article>
      <footer>Site footer with unrelated links</footer>
      `,
      `<title>Long headline about something newsworthy happening today</title>`
    );

    const result = extractArticle(document, TEST_URL, {
      whitelisted: false,
      articleConfidence: 0.9,
    });

    expect(result).not.toBeNull();
    expect(result?.url).toBe(TEST_URL);
    expect(result?.title).toContain("Long headline");
    expect(result?.bodyText.length).toBeGreaterThan(500);
    expect(result?.bodyText).not.toContain("Site footer");
    expect(result?.author).toBe("By Jane Tan");
    expect(result?.publishDate).toBe("2026-09-01T08:00:00Z");
    expect(result?.whitelisted).toBe(false);
    expect(result?.articleConfidence).toBe(0.9);
  });

  it("picks up author/date from JSON-LD when there's no visible byline element", () => {
    setPage(
      `<article>${PARAGRAPHS}</article>`,
      `
      <title>JSON-LD sourced article</title>
      <script type="application/ld+json">
        ${JSON.stringify({
          "@context": "https://schema.org",
          "@type": "NewsArticle",
          author: { "@type": "Person", name: "John Lim" },
          datePublished: "2026-08-30T10:00:00Z",
        })}
      </script>
      `
    );

    const result = extractArticle(document, TEST_URL, {
      whitelisted: false,
      articleConfidence: 0.8,
    });

    expect(result?.author).toBe("John Lim");
    expect(result?.publishDate).toBe("2026-08-30T10:00:00Z");
  });

  it("includes content images and filters out small icons and tracking pixels", () => {
    setPage(
      `
      <article>
        <img src="https://example.news/images/photo1.jpg" width="600" height="400" />
        <img src="https://example.news/images/icon.png" width="16" height="16" />
        <img src="https://example.news/pixel-tracking.gif" width="1" height="1" />
        <img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==" />
        ${PARAGRAPHS}
      </article>
      `
    );

    const result = extractArticle(document, TEST_URL, {
      whitelisted: false,
      articleConfidence: 0.8,
    });

    expect(result?.imageUrls).toEqual(["https://example.news/images/photo1.jpg"]);
  });

  it("filters out inline SVG icons (e.g. a theme's close.svg)", () => {
    setPage(
      `
      <article>
        <img src="https://example.news/images/photo1.jpg" width="600" height="400" />
        <img src="https://example.news/theme/icons/close.svg" />
        ${PARAGRAPHS}
      </article>
      `
    );

    const result = extractArticle(document, TEST_URL, {
      whitelisted: false,
      articleConfidence: 0.8,
    });

    expect(result?.imageUrls).toEqual(["https://example.news/images/photo1.jpg"]);
  });

  it("dedupes repeated image URLs", () => {
    setPage(
      `
      <article>
        <img src="https://example.news/images/photo1.jpg" width="600" height="400" />
        <figure><img src="https://example.news/images/photo1.jpg" width="600" height="400" /></figure>
        ${PARAGRAPHS}
      </article>
      `
    );

    const result = extractArticle(document, TEST_URL, {
      whitelisted: false,
      articleConfidence: 0.8,
    });

    expect(result?.imageUrls).toEqual(["https://example.news/images/photo1.jpg"]);
  });

  it("resolves a lazy-loaded image (data-src) into a real URL via Readability's built-in fixup", () => {
    setPage(
      `
      <article>
        <img data-src="https://example.news/images/lazy.jpg" class="lazyload" width="600" height="400" />
        ${PARAGRAPHS}
      </article>
      `
    );

    const result = extractArticle(document, TEST_URL, {
      whitelisted: false,
      articleConfidence: 0.8,
    });

    expect(result?.imageUrls).toContain("https://example.news/images/lazy.jpg");
  });

  it("resolves relative image URLs against the page URL", () => {
    // Readability resolves relative attrs against document.baseURI, which
    // jsdom derives from <base>/the page location rather than the `url`
    // argument -- set <base> so this test reflects real browser behavior,
    // where document.baseURI always matches the live page's own URL.
    setPage(
      `
      <article>
        <img src="/images/relative.jpg" width="600" height="400" />
        ${PARAGRAPHS}
      </article>
      `,
      `<base href="${TEST_URL}">`
    );

    const result = extractArticle(document, TEST_URL, {
      whitelisted: false,
      articleConfidence: 0.8,
    });

    expect(result?.imageUrls).toEqual(["https://example.news/images/relative.jpg"]);
  });

  it("returns null when the page has no extractable text at all", () => {
    setPage("");

    const result = extractArticle(document, TEST_URL, {
      whitelisted: false,
      articleConfidence: 0.1,
    });

    expect(result).toBeNull();
  });

  it("does not mutate the live document while parsing", () => {
    setPage(`<article><h1>Headline</h1>${PARAGRAPHS}</article>`);
    const originalHtml = document.body.innerHTML;

    extractArticle(document, TEST_URL, { whitelisted: false, articleConfidence: 0.8 });

    expect(document.body.innerHTML).toBe(originalHtml);
  });
});
