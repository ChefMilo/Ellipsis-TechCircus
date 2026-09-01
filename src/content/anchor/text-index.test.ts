// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { buildTextIndex, locateOffset } from "./text-index";

function root(html: string): HTMLElement {
  document.body.innerHTML = html;
  return document.body;
}

describe("buildTextIndex", () => {
  it("flattens text across elements and collapses inter-node whitespace", () => {
    const el = root(`<p>Singapore recorded  3,363
      cases</p><p>up from 1,504</p>`);
    const index = buildTextIndex(el);
    expect(index.flat).toBe("Singapore recorded 3,363 cases up from 1,504");
  });

  it("stitches a sentence split by an inline link", () => {
    const el = root(
      `<p>Victims lost <a href="/x">S$242.9 million</a> to these scams.</p>`,
    );
    const index = buildTextIndex(el);
    expect(index.flat).toBe("Victims lost S$242.9 million to these scams.");
  });

  it("excludes script/style/nav/figcaption subtrees", () => {
    const el = root(`
      <nav>Home World Singapore</nav>
      <article><p>The real body text.</p><figcaption>A caption</figcaption></article>
      <script>var x = "not text";</script>
    `);
    const index = buildTextIndex(el);
    expect(index.flat).toBe("The real body text.");
  });

  it("maps a flat offset back to the originating text node", () => {
    const el = root(`<p>alpha <b>beta</b> gamma</p>`);
    const index = buildTextIndex(el);
    const at = index.flat.indexOf("beta");
    const loc = locateOffset(index, at, "start");
    expect(loc?.node.data).toBe("beta");
    expect(loc?.offset).toBe(0);
  });

  it("maps an exclusive end offset to just past the last character", () => {
    const el = root(`<p>alpha beta</p>`);
    const index = buildTextIndex(el);
    const end = index.flat.length; // just past "alpha beta"
    const loc = locateOffset(index, end, "end");
    expect(loc?.node.data).toBe("alpha beta");
    expect(loc?.offset).toBe("alpha beta".length);
  });

  it("has no leading or trailing whitespace", () => {
    const el = root(`<p>   padded   </p>`);
    expect(buildTextIndex(el).flat).toBe("padded");
  });
});
