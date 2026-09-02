// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { MOCK_ANALYSIS } from "./__mock__/mock-response";
import { mountFactCheckUI, type FactCheckController } from "./controller";
import { HOST_TAG } from "./shadow-root";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

function fireMouse(el: Element, type: string, init: MouseEventInit = {}): void {
  el.dispatchEvent(new MouseEvent(type, { bubbles: true, composed: true, ...init }));
}

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

  it("opens the hovercard next to a claim on hover, then hides it on mouseout", async () => {
    document.body.innerHTML = ARTICLE_HTML;
    controller = mountFactCheckUI({ articleRoot: document.body });
    controller.render(MOCK_ANALYSIS);

    const span = document.querySelector<HTMLElement>('[data-dasfax-claim-id="c1"]');
    expect(span).not.toBeNull();

    fireMouse(span!, "mouseover");
    await wait(140);
    const card = shadow().querySelector(".dasfax-hovercard");
    expect(card?.getAttribute("data-open")).toBe("true");
    expect(card?.querySelector(".dasfax-badge")?.getAttribute("data-status")).toBe(
      "contradicted",
    );
    // A hover (not a click) shows no dismiss button.
    expect(card?.querySelector(".dasfax-hovercard__close")).toBeNull();

    fireMouse(span!, "mouseout", { relatedTarget: document.body });
    await wait(300);
    expect(
      shadow().querySelector(".dasfax-hovercard")?.getAttribute("data-open"),
    ).toBe("false");
  });

  it("pins the hovercard open when a claim is clicked, and unpins on a second click", () => {
    document.body.innerHTML = ARTICLE_HTML;
    controller = mountFactCheckUI({ articleRoot: document.body });
    controller.render(MOCK_ANALYSIS);

    const span = document.querySelector<HTMLElement>('[data-dasfax-claim-id="c1"]');
    fireMouse(span!, "click");

    const card = shadow().querySelector(".dasfax-hovercard");
    expect(card?.getAttribute("data-open")).toBe("true");
    // Pinned => a dismiss button, and it survives a mouseout.
    expect(card?.querySelector(".dasfax-hovercard__close")).not.toBeNull();
    fireMouse(span!, "mouseout", { relatedTarget: document.body });
    expect(
      shadow().querySelector(".dasfax-hovercard")?.getAttribute("data-open"),
    ).toBe("true");

    fireMouse(span!, "click");
    expect(
      shadow().querySelector(".dasfax-hovercard")?.getAttribute("data-open"),
    ).toBe("false");
  });

  it("does not open the drawer on a claim click (drawer is pill-only now)", () => {
    document.body.innerHTML = ARTICLE_HTML;
    controller = mountFactCheckUI({ articleRoot: document.body });
    controller.render(MOCK_ANALYSIS);

    fireMouse(document.querySelector('[data-dasfax-claim-id="c1"]')!, "click");
    expect(shadow().querySelector(".dasfax-drawer")?.getAttribute("data-open")).toBe(
      "false",
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

  it("shows the unrated state when nothing on the page was fact-checked", () => {
    document.body.innerHTML = ARTICLE_HTML;
    controller = mountFactCheckUI({ articleRoot: document.body });
    const summary = "A quick scan found nothing that needed a full fact-check.";
    controller.render({
      ...MOCK_ANALYSIS,
      status: "complete",
      articleVerdict: { level: "unrated", summary },
      verifiedClaims: [],
    });
    const pill = shadow().querySelector(".dasfax-pill");
    expect(pill?.getAttribute("data-level")).toBe("unrated");
    expect(pill?.textContent).toContain("Not fact-checked");
    // The pill stays a terse label; the per-case reason (`summary`) shows in the panel.
    expect(pill?.querySelector(".dasfax-pill__button")?.tagName).toBe("SPAN");
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
