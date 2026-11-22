import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Canvas charts can't run in jsdom; stub the library with a recording fake.
vi.mock("lightweight-charts", () => {
  const series = { setData: vi.fn(), update: vi.fn() };
  const chart = {
    addSeries: vi.fn(() => series),
    remove: vi.fn(),
    timeScale: () => ({ fitContent: vi.fn() }),
  };
  return {
    createChart: vi.fn(() => chart),
    AreaSeries: "Area",
    LineSeries: "Line",
    ColorType: { Solid: "solid" },
    CrosshairMode: { Normal: 0 },
    __chart: chart,
    __series: series,
  };
});

afterEach(() => {
  cleanup();
});
