// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { showCheckAnywayButton, showTrustedBadge, teardownWs1Ui } from "./trusted-ui";

const HOST_TAG = "ws1-trust-ui";

function setPage(): void {
  document.body.innerHTML = "";
  teardownWs1Ui(document);
}

describe("trusted-ui", () => {
  it("renders a trusted-source badge", () => {
    setPage();
    showTrustedBadge(document);

    const host = document.querySelector(HOST_TAG);
    expect(host).not.toBeNull();
    expect(host?.shadowRoot?.textContent).toContain("Trusted source");
  });

  it("renders a check-anyway button without calling the callback automatically", () => {
    setPage();
    const onCheckAnyway = vi.fn();
    showCheckAnywayButton(onCheckAnyway, document);

    const host = document.querySelector(HOST_TAG);
    expect(host).not.toBeNull();
    expect(host?.shadowRoot?.textContent).toContain("Check this page anyway");
    // The whole point of this UI: zero automatic calls, only user-triggered.
    expect(onCheckAnyway).not.toHaveBeenCalled();
  });

  it("calls the callback exactly once when the check-anyway button is clicked", () => {
    setPage();
    const onCheckAnyway = vi.fn();
    showCheckAnywayButton(onCheckAnyway, document);

    const host = document.querySelector(HOST_TAG);
    const button = host?.shadowRoot?.querySelector("button");
    button?.dispatchEvent(new window.Event("click", { bubbles: true }));

    expect(onCheckAnyway).toHaveBeenCalledTimes(1);
  });

  it("removes the button from the page once clicked", () => {
    setPage();
    showCheckAnywayButton(vi.fn(), document);

    const button = document.querySelector(HOST_TAG)?.shadowRoot?.querySelector("button");
    button?.dispatchEvent(new window.Event("click", { bubbles: true }));

    expect(document.querySelector(HOST_TAG)).toBeNull();
  });

  it("showing the badge replaces a previously shown button (only one WS1 UI element at a time)", () => {
    setPage();
    showCheckAnywayButton(vi.fn(), document);
    showTrustedBadge(document);

    expect(document.querySelectorAll(HOST_TAG).length).toBe(1);
    expect(document.querySelector(HOST_TAG)?.shadowRoot?.textContent).toContain(
      "Trusted source"
    );
  });

  it("teardownWs1Ui removes any mounted WS1 UI", () => {
    setPage();
    showTrustedBadge(document);
    expect(document.querySelector(HOST_TAG)).not.toBeNull();

    teardownWs1Ui(document);

    expect(document.querySelector(HOST_TAG)).toBeNull();
  });

  it("teardownWs1Ui is a no-op when nothing is mounted", () => {
    setPage();
    expect(() => teardownWs1Ui(document)).not.toThrow();
  });
});
