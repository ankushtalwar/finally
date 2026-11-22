import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Header from "@/components/Header";
import PositionsTable from "@/components/PositionsTable";
import Heatmap from "@/components/Heatmap";
import TradeBar from "@/components/TradeBar";
import Sparkline, { sparklinePoints } from "@/components/Sparkline";
import { snapshotsToPoints } from "@/components/PnlChart";
import { portfolioFixture } from "./mocks";

describe("Header", () => {
  it("shows formatted totals and connection status", () => {
    render(
      <Header totalValue={10000} cash={8049.8} unrealizedPnl={50.2} unrealizedPnlPercent={2.64} status="connected" />,
    );
    expect(screen.getByTestId("total-value")).toHaveTextContent("$10,000.00");
    expect(screen.getByTestId("cash-balance")).toHaveTextContent("$8,049.80");
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "connected");
  });
  it("reflects reconnecting and disconnected states", () => {
    const { rerender } = render(
      <Header totalValue={null} cash={null} unrealizedPnl={null} unrealizedPnlPercent={null} status="reconnecting" />,
    );
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "reconnecting");
    rerender(
      <Header totalValue={null} cash={null} unrealizedPnl={null} unrealizedPnlPercent={null} status="disconnected" />,
    );
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "disconnected");
  });
});

describe("PositionsTable", () => {
  it("renders a row per position with quantity and P&L", () => {
    render(<PositionsTable positions={portfolioFixture.positions} />);
    const row = screen.getByTestId("position-row-AAPL");
    expect(row).toHaveTextContent("10");
    expect(row).toHaveTextContent("190.00");
    expect(row).toHaveTextContent("195.02");
    expect(row).toHaveTextContent("+$50.20");
    expect(row).toHaveTextContent("+2.64%");
  });
  it("shows an empty state", () => {
    render(<PositionsTable positions={[]} />);
    expect(screen.getByTestId("positions-table")).toHaveTextContent("No open positions");
  });
});

describe("Heatmap", () => {
  it("renders a tile per position and an empty state", () => {
    const positions = [
      portfolioFixture.positions[0],
      { ...portfolioFixture.positions[0], ticker: "TSLA", market_value: 500, unrealized_pnl_percent: -3 },
    ];
    const { rerender } = render(<Heatmap positions={positions} />);
    expect(screen.getByTestId("heatmap")).toBeInTheDocument();
    expect(screen.getByTestId("heatmap-tile-AAPL")).toBeInTheDocument();
    const tsla = screen.getByTestId("heatmap-tile-TSLA");
    expect(tsla.style.backgroundColor).toMatch(/rgb/);
    rerender(<Heatmap positions={[]} />);
    expect(screen.queryByTestId("heatmap-tile-AAPL")).not.toBeInTheDocument();
    expect(screen.getByTestId("heatmap")).toHaveTextContent("No positions yet");
  });
});

describe("TradeBar", () => {
  it("prefills the selected ticker and submits a buy", async () => {
    const onTrade = vi.fn().mockResolvedValue({
      id: "t1",
      ticker: "AAPL",
      side: "buy",
      quantity: 5,
      price: 190.12,
      executed_at: "",
    });
    render(<TradeBar selectedTicker="AAPL" onTrade={onTrade} />);
    expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL");
    await userEvent.type(screen.getByTestId("trade-quantity"), "5");
    await userEvent.click(screen.getByTestId("trade-buy"));
    expect(onTrade).toHaveBeenCalledWith("AAPL", 5, "buy");
    expect(await screen.findByTestId("trade-success")).toHaveTextContent("Bought 5 AAPL at $190.12");
  });

  it("validates quantity locally and shows backend errors", async () => {
    const onTrade = vi.fn().mockRejectedValue(new Error("Insufficient shares: have 0, need 2"));
    render(<TradeBar selectedTicker={null} onTrade={onTrade} />);
    await userEvent.type(screen.getByTestId("trade-ticker"), "tsla");
    await userEvent.click(screen.getByTestId("trade-sell"));
    expect(screen.getByTestId("trade-error")).toHaveTextContent("Quantity must be greater than 0");
    expect(onTrade).not.toHaveBeenCalled();
    await userEvent.type(screen.getByTestId("trade-quantity"), "2");
    await userEvent.click(screen.getByTestId("trade-sell"));
    expect(onTrade).toHaveBeenCalledWith("TSLA", 2, "sell");
    await waitFor(() => expect(screen.getByTestId("trade-error")).toHaveTextContent("Insufficient shares"));
  });

  it("follows a new watchlist selection", () => {
    const { rerender } = render(<TradeBar selectedTicker="AAPL" onTrade={vi.fn()} />);
    rerender(<TradeBar selectedTicker="NVDA" onTrade={vi.fn()} />);
    expect(screen.getByTestId("trade-ticker")).toHaveValue("NVDA");
  });
});

describe("Sparkline and P&L helpers", () => {
  it("scales points into the box", () => {
    expect(sparklinePoints([1], 10, 10)).toBe("");
    const pts = sparklinePoints([1, 2, 3], 10, 10, 0).split(" ");
    expect(pts).toEqual(["0.0,10.0", "5.0,5.0", "10.0,0.0"]);
    const { container } = render(<Sparkline values={[1, 2]} />);
    expect(container.querySelector("polyline")).not.toBeNull();
  });
  it("converts snapshots to ascending unique-second points", () => {
    const pts = snapshotsToPoints([
      { total_value: 10010, recorded_at: "2026-11-13T10:00:30.000+00:00" },
      { total_value: 10000, recorded_at: "2026-11-13T10:00:00.100+00:00" },
      { total_value: 10001, recorded_at: "2026-11-13T10:00:00.900+00:00" },
      { total_value: 1, recorded_at: "garbage" },
    ]);
    expect(pts.map((p) => p.value)).toEqual([10001, 10010]);
    expect(pts[1].time - pts[0].time).toBe(30);
  });
});
