// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { MOCK_ANALYSIS } from "./__mock__/mock-response";
import { Panel } from "./panel";

function makePanel() {
  document.body.innerHTML = "";
  const layer = document.createElement("div");
  document.body.append(layer);
  const activated: string[] = [];
  const panel = new Panel(layer, { onClaimActivate: (id) => activated.push(id) });
  panel.setData(MOCK_ANALYSIS, ["c7"]);
  return { panel, layer, activated };
}

describe("Panel", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("lists every verified claim with a status badge when opened", () => {
    const { panel, layer } = makePanel();
    panel.open();
    const rows = layer.querySelectorAll(".dasfax-claim-row");
    expect(rows).toHaveLength(MOCK_ANALYSIS.verifiedClaims.length);
    const badges = layer.querySelectorAll(".dasfax-badge[data-status]");
    expect(badges.length).toBeGreaterThanOrEqual(rows.length);
  });

  it("renders a contradicted claim's explanation and citation", () => {
    const { panel, layer } = makePanel();
    panel.showClaim("c1");
    expect(layer.querySelector(".dasfax-badge")?.getAttribute("data-status")).toBe(
      "contradicted",
    );
    expect(layer.textContent).toContain("S$3.8 million");
    const link = layer.querySelector<HTMLAnchorElement>(".dasfax-source__title");
    expect(link?.href).toContain("press.example.org");
  });

  it("shows the 'not yet verified' treatment for a null assessment", () => {
    const { panel, layer } = makePanel();
    panel.showClaim("c5");
    expect(layer.querySelector(".dasfax-badge")?.getAttribute("data-status")).toBe(
      "unverified",
    );
  });

  it("flags an unanchored claim in its detail view", () => {
    const { panel, layer } = makePanel();
    panel.showClaim("c7");
    expect(layer.querySelector(".dasfax-claim-detail__unanchored")).not.toBeNull();
  });

  it("closes on Escape", () => {
    const { panel, layer } = makePanel();
    panel.open();
    expect(panel.open_).toBe(true);
    document.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(panel.open_).toBe(false);
    expect(layer.querySelector(".dasfax-drawer")?.getAttribute("data-open")).toBe(
      "false",
    );
  });

  it("fires onClaimActivate when a list row is chosen", () => {
    const { panel, layer, activated } = makePanel();
    panel.open();
    layer.querySelector<HTMLButtonElement>(".dasfax-claim-row")?.click();
    expect(activated.length).toBe(1);
  });
});
