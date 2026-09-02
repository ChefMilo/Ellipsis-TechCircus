// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MOCK_ANALYSIS } from "./__mock__/mock-response";
import { Hovercard } from "./hovercard";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

function rect(over: Partial<DOMRect> = {}): DOMRect {
  return {
    left: 100,
    top: 200,
    right: 300,
    bottom: 220,
    width: 200,
    height: 20,
    x: 100,
    y: 200,
    ...over,
    toJSON() {},
  } as DOMRect;
}

let layer: HTMLElement;
let card: Hovercard;

beforeEach(() => {
  document.body.innerHTML = "";
  layer = document.createElement("div");
  document.body.append(layer);
  card = new Hovercard(layer);
  card.setData(MOCK_ANALYSIS, ["c7"]);
});

afterEach(() => {
  card.destroy();
});

function cardEl(): HTMLElement | null {
  return layer.querySelector(".dasfax-hovercard");
}

describe("Hovercard", () => {
  it("stays hidden until scheduleShow's delay elapses, then renders the claim", async () => {
    card.scheduleShow("c1", rect());
    expect(cardEl()?.getAttribute("data-open")).toBe("false");

    await wait(140);
    expect(cardEl()?.getAttribute("data-open")).toBe("true");
    expect(cardEl()?.querySelector(".dasfax-badge")?.getAttribute("data-status")).toBe(
      "contradicted",
    );
    expect(cardEl()?.textContent).toContain("S$3.8 million");
    // Positioning ran against the anchor rect.
    expect(cardEl()?.style.left).toMatch(/px$/);
    expect(cardEl()?.style.top).toMatch(/px$/);
  });

  it("pin() shows immediately with a dismiss button and reports isPinned", () => {
    card.pin("c2", rect());
    expect(card.isPinned).toBe(true);
    expect(card.visibleClaimId).toBe("c2");
    expect(cardEl()?.getAttribute("data-open")).toBe("true");
    expect(cardEl()?.querySelector(".dasfax-hovercard__close")).not.toBeNull();
  });

  it("scheduleHide is a no-op while pinned, but hide() still closes it", async () => {
    card.pin("c1", rect());
    card.scheduleHide();
    await wait(300);
    expect(cardEl()?.getAttribute("data-open")).toBe("true");

    card.hide();
    expect(cardEl()?.getAttribute("data-open")).toBe("false");
    expect(card.isPinned).toBe(false);
    expect(card.visibleClaimId).toBeNull();
  });

  it("scheduleShow is ignored while a different claim is pinned", async () => {
    card.pin("c1", rect());
    card.scheduleShow("c3", rect());
    await wait(140);
    expect(card.visibleClaimId).toBe("c1");
  });

  it("flags an unanchored claim in its body", () => {
    card.pin("c7", rect());
    expect(cardEl()?.querySelector(".dasfax-hovercard__unanchored")).not.toBeNull();
  });

  it("contains() recognises its own subtree", () => {
    card.pin("c1", rect());
    const badge = cardEl()!.querySelector(".dasfax-badge")!;
    expect(card.contains(badge)).toBe(true);
    expect(card.contains(document.body)).toBe(false);
  });
});
