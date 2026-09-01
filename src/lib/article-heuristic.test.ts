// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { ARTICLE_CONFIDENCE_THRESHOLD, isProbablyArticle } from "./article-heuristic";

function longParagraph(sentenceCount: number): string {
  const sentence =
    "This is a reasonably long sentence written to simulate real article prose. ";
  return sentence.repeat(sentenceCount);
}

function setBody(html: string): void {
  document.body.innerHTML = html;
}

describe("isProbablyArticle", () => {
  it("scores a well-formed news article highly", () => {
    setBody(`
      <header><nav>Home / World / Singapore</nav></header>
      <article>
        <h1>Long headline about something newsworthy happening today</h1>
        <div class="byline">By Jane Tan, Senior Correspondent</div>
        <time datetime="2026-09-01">1 Sep 2026</time>
        ${Array.from({ length: 8 }, () => `<p>${longParagraph(3)}</p>`).join("\n")}
      </article>
      <footer>Site footer</footer>
    `);

    const result = isProbablyArticle(document);
    expect(result.isArticle).toBe(true);
    expect(result.confidence).toBeGreaterThanOrEqual(ARTICLE_CONFIDENCE_THRESHOLD);
  });

  it("returns a confidence score, not just a boolean", () => {
    setBody("<p>short</p>");
    const result = isProbablyArticle(document);
    expect(typeof result.confidence).toBe("number");
    expect(result.confidence).toBeGreaterThanOrEqual(0);
    expect(result.confidence).toBeLessThanOrEqual(1);
  });

  it("scores a mostly-empty page as not an article", () => {
    setBody(`<div class="hero"><h1>Welcome</h1></div>`);
    const result = isProbablyArticle(document);
    expect(result.isArticle).toBe(false);
    expect(result.confidence).toBeLessThan(ARTICLE_CONFIDENCE_THRESHOLD);
  });

  it("penalizes a product-grid layout even with some paragraph text", () => {
    const products = Array.from(
      { length: 12 },
      (_, i) => `
        <div class="product-card">
          <div class="product-title">Item ${i}</div>
          <div class="price">$${10 + i}.99</div>
          <button class="add-to-cart">Add to cart</button>
        </div>`
    ).join("\n");
    setBody(`<div class="product-grid">${products}</div><p>${longParagraph(2)}</p>`);

    const result = isProbablyArticle(document);
    expect(result.isArticle).toBe(false);
  });

  it("penalizes a video-player-dominant layout with little body copy", () => {
    setBody(`
      <div class="player-wrap">
        <iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>
      </div>
      <p>Short caption.</p>
    `);

    const result = isProbablyArticle(document);
    expect(result.isArticle).toBe(false);
  });

  it("penalizes a chat/inbox UI layout", () => {
    const threads = Array.from(
      { length: 10 },
      (_, i) => `<div class="thread-list-item">Conversation ${i}</div>`
    ).join("\n");
    setBody(`<div class="inbox"><div class="conversation-list">${threads}</div></div>`);

    const result = isProbablyArticle(document);
    expect(result.isArticle).toBe(false);
  });

  it("does not require an <article> tag if other signals are strong", () => {
    setBody(`
      <div class="byline">By John Lim</div>
      <time datetime="2026-09-01">1 Sep 2026</time>
      ${Array.from({ length: 8 }, () => `<p>${longParagraph(3)}</p>`).join("\n")}
    `);

    const result = isProbablyArticle(document);
    expect(result.isArticle).toBe(true);
  });

  it("gives partial credit to a page with a couple of short paragraphs", () => {
    setBody(`<p>${longParagraph(2)}</p><p>${longParagraph(2)}</p>`);
    const result = isProbablyArticle(document);
    expect(result.confidence).toBeGreaterThan(0);
    expect(result.confidence).toBeLessThan(ARTICLE_CONFIDENCE_THRESHOLD);
  });
});
