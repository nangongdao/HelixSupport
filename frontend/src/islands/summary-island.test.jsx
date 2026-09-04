/**
 * Helix Support — summary island component tests (D3 long tail slice 15)
 *
 * The summary banner is island-rendered but legacy-fed: js/renderSummaries
 * derives the model via js/summary.js and publishes helix-summary-state.
 * These tests lock the render mirror.
 */

import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup, act } from "@testing-library/react";
import React from "react";

import { SummaryIsland, SUMMARY_EVENT } from "../islands/summary-island.jsx";

function publish(detail) {
  act(() => {
    window.dispatchEvent(new CustomEvent(SUMMARY_EVENT, { detail }));
  });
}

afterEach(() => {
  cleanup();
});

describe("SummaryIsland", () => {
  it("renders a hidden banner before any publish", () => {
    render(<SummaryIsland />);
    const banner = document.querySelector(".summary-banner");
    expect(banner).toBeTruthy();
    expect(banner.hidden).toBe(true);
  });

  it("mirrors the legacy banner markup for a visible summary", () => {
    render(<SummaryIsland />);
    publish({
      visible: true,
      title: "前情摘要（接入参考）",
      text: "客户此前咨询过配送时效。（自动投影）",
    });
    const banner = document.querySelector(".summary-banner");
    expect(banner.hidden).toBe(false);
    expect(banner.querySelector(".csat-label span:last-child").textContent).toBe(
      "前情摘要（接入参考）",
    );
    expect(banner.querySelector(".summary-text").textContent).toBe(
      "客户此前咨询过配送时效。（自动投影）",
    );
  });

  it("hides again when legacy publishes an invisible model", () => {
    render(<SummaryIsland />);
    publish({ visible: true, title: "t", text: "x" });
    publish({ visible: false, title: "", text: "" });
    expect(document.querySelector(".summary-banner").hidden).toBe(true);
  });
});
