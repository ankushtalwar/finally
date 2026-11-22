import { describe, expect, it } from "vitest";
import { revaluePortfolio } from "@/lib/portfolio";
import { portfolioFixture } from "./mocks";
import type { PriceMap } from "@/lib/types";

const tick = (ticker: string, price: number) => ({ ticker, price, previous_price: price, timestamp: 1 });

describe("revaluePortfolio", () => {
  it("keeps server values when no live price is streamed", () => {
    const p = revaluePortfolio(portfolioFixture, {});
    expect(p.total_value).toBeCloseTo(10000.0, 6);
    expect(p.positions[0].unrealized_pnl).toBeCloseTo(50.2, 6);
  });

  it("re-prices positions, totals and weights from live ticks", () => {
    const prices: PriceMap = { AAPL: tick("AAPL", 200) };
    const p = revaluePortfolio(portfolioFixture, prices);
    expect(p.positions[0].current_price).toBe(200);
    expect(p.positions[0].market_value).toBe(2000);
    expect(p.positions[0].unrealized_pnl).toBeCloseTo(100, 6);
    expect(p.positions[0].unrealized_pnl_percent).toBeCloseTo((100 / 1900) * 100, 6);
    expect(p.total_value).toBeCloseTo(8049.8 + 2000, 6);
    expect(p.positions[0].weight).toBeCloseTo(2000 / (8049.8 + 2000), 6);
    expect(p.unrealized_pnl).toBeCloseTo(100, 6);
  });

  it("sorts positions by market value and handles a loss", () => {
    const base = {
      ...portfolioFixture,
      positions: [
        { ...portfolioFixture.positions[0] },
        { ...portfolioFixture.positions[0], ticker: "TSLA", quantity: 20, avg_cost: 250, current_price: 250 },
      ],
    };
    const p = revaluePortfolio(base, { TSLA: tick("TSLA", 240) });
    expect(p.positions.map((x) => x.ticker)).toEqual(["TSLA", "AAPL"]);
    expect(p.positions[0].unrealized_pnl).toBeCloseTo(-200, 6);
    expect(p.positions[0].unrealized_pnl_percent).toBeCloseTo(-4, 6);
  });

  it("is just cash with no positions", () => {
    const p = revaluePortfolio({ ...portfolioFixture, cash: 10000, positions: [] }, {});
    expect(p.total_value).toBe(10000);
    expect(p.unrealized_pnl_percent).toBe(0);
  });
});
