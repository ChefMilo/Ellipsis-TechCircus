// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { MOCK_ANALYSIS } from "./__mock__/mock-response";
import { mountFactCheckUI, type FactCheckController } from "./controller";
import { HOST_TAG } from "./shadow-root";

const ARTICLE_HTML = `
  <article>
    <p>Singapore reported a sharp rise in impersonation scam losses in 2025.</p>
    <p>In one widely reported case, a Singapore businessman lost S$4.9 million after joining a Zoom call in which artificial intelligence was used to fabricate the likenesses of senior officials.</p>
    <p>According to a study, 79 per cent of Singaporeans are confident they can spot fake news.</p>
    <p>Victims lost a total of S$242.9 million to these scams last year, with an average loss of S$72,229 per victim.</p>
    <p>The Singapore Police Force warned that deepfake AI fabrications can be sophisticated and difficult to distinguish from authentic content.</p>
    <p>Honestly, this is the most alarming trend I have seen in years, and the authorities should do much more to stop it.</p>
  </article>`;

let controller: FactCheckController | null = null;

afterEach(() => {
  controller?.teardown();
  controller = null;
  document.body.innerHTML = "";
  document
    .querySelectorAll("#dasfax-highlight-style")
    .forEach((n) => n.remove());
});

function shadow(): ShadowRoot {
  const host = document.querySelector(HOST_TAG.toLowerCase());
  if (!host?.shadowRoot) throw new Error("no shadow host");
  return host.shadowRoot;
}

describe("mountFactCheckUI", () => {
  it("shows a loading pill before results arrive", () => {
    document.body.innerHTML = ARTICLE_HTML;
    controller = mountFactCheckUI({ articleRoot: document.body });
    controller.showLoading();
    expect(shadow().querySelector(".dasfax-spinner")).not.toBeNull();
  });

  it("paints highlights for the claims it can anchor and counts them in the pill", () => {
    document.body.innerHTML = ARTICLE_HTML;
    controller = mountFactCheckUI({ articleRoot: document.body });
    controller.render(MOCK_ANALYSIS);

    // span-fallback path in jsdom: one <span data-dasfax-claim-id> per anchored claim
    const anchored = new Set(
      Array.from(
        document.querySelectorAll<HTMLElement>("[data-dasfax-claim-id]"),
      ).map((el) => el.dataset.dasfaxClaimId),
    );
    // c1, c2, c3, c5, c6 are sentences in ARTICLE_HTML; c7 is not.
    expect(anchored.has("c1")).toBe(true);
    expect(anchored.has("c6")).toBe(true);
    expect(anchored.has("c7")).toBe(false);

    expect(shadow().querySelector(".dasfax-pill")?.textContent).toMatch(/claim/i);
  });

  it("opens the drawer to a claim when its highlight is clicked", () => {
    document.body.innerHTML = ARTICLE_HTML;
    controller = mountFactCheckUI({ articleRoot: document.body });
    controller.render(MOCK_ANALYSIS);

    const span = document.querySelector<HTMLElement>('[data-dasfax-claim-id="c1"]');
    expect(span).not.toBeNull();
    span!.dispatchEvent(new MouseEvent("click", { bubbles: true, composed: true }));

    const drawer = shadow().querySelector(".dasfax-drawer");
    expect(drawer?.getAttribute("data-open")).toBe("true");
    expect(shadow().querySelector(".dasfax-badge")?.getAttribute("data-status")).toBe(
      "contradicted",
    );
  });

  it("teardown removes the shadow host and the page style", () => {
    document.body.innerHTML = ARTICLE_HTML;
    controller = mountFactCheckUI({ articleRoot: document.body });
    controller.render(MOCK_ANALYSIS);
    controller.teardown();
    controller = null;
    expect(document.querySelector(HOST_TAG.toLowerCase())).toBeNull();
    expect(document.querySelectorAll("[data-dasfax-claim-id]")).toHaveLength(0);
  });

  it("shows the trusted state for a skipped response", () => {
    document.body.innerHTML = ARTICLE_HTML;
    controller = mountFactCheckUI({ articleRoot: document.body });
    controller.render({
      ...MOCK_ANALYSIS,
      status: "skipped",
      articleVerdict: { level: "trusted", summary: "Trusted source" },
    });
    expect(shadow().querySelector(".dasfax-pill")?.textContent).toContain(
      "Trusted source",
    );
    expect(document.querySelectorAll("[data-dasfax-claim-id]")).toHaveLength(0);
  });
});
