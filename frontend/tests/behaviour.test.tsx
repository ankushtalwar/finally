import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Watchlist, { WatchlistRow, type WatchlistRowData } from "@/components/Watchlist";
import ChatPanel from "@/components/ChatPanel";
import Header from "@/components/Header";
import Heatmap from "@/components/Heatmap";
import PnlChart from "@/components/PnlChart";
import TradeBar from "@/components/TradeBar";
import { revaluePortfolio } from "@/lib/portfolio";
import { usePriceStream } from "@/hooks/usePriceStream";
import type { ChatMessage, Portfolio, Position } from "@/lib/types";
import { FakeEventSource, portfolioFixture } from "./mocks";

const pos = (over: Partial<Position>): Position => ({ ...portfolioFixture.positions[0], ...over });

// ---------------------------------------------------------------- price flash

describe("price flash lifecycle", () => {
  const row: WatchlistRowData = { ticker: "AAPL", price: null, dayChangePercent: 0 };
  const renderRow = (price: number | null) => (
    <WatchlistRow row={{ ...row, price }} points={undefined} selected={false} onSelect={vi.fn()} onRemove={vi.fn()} />
  );

  it("does not flash when the first price arrives", () => {
    const { rerender } = render(renderRow(null));
    rerender(renderRow(190));
    expect(screen.getByTestId("watchlist-price-AAPL").className).not.toMatch(/flash-/);
  });

  it("does not flash on a re-render with the same price", () => {
    const { rerender } = render(renderRow(190));
    rerender(renderRow(190));
    expect(screen.getByTestId("watchlist-price-AAPL").className).not.toMatch(/flash-/);
  });

  it("removes the flash class when the fade animation ends", () => {
    const { rerender } = render(renderRow(190));
    rerender(renderRow(189.5));
    const el = screen.getByTestId("watchlist-price-AAPL");
    expect(el.className).toMatch(/flash-down/);
    fireEvent.animationEnd(el);
    expect(el.className).not.toMatch(/flash-/);
    // and it can flash again afterwards
    rerender(renderRow(191));
    expect(el.className).toMatch(/flash-up/);
  });
});

// ---------------------------------------------------------- portfolio maths

describe("portfolio calculations", () => {
  it("weights sum with cash share to 1 and use the live total", () => {
    const p: Portfolio = {
      ...portfolioFixture,
      cash: 1000,
      positions: [pos({ ticker: "AAPL", quantity: 10, avg_cost: 100 }), pos({ ticker: "MSFT", quantity: 5, avg_cost: 400 })],
    };
    const live = revaluePortfolio(p, {
      AAPL: { ticker: "AAPL", price: 110, previous_price: 109, timestamp: 1 },
      MSFT: { ticker: "MSFT", price: 380, previous_price: 381, timestamp: 1 },
    });
    expect(live.positions_value).toBeCloseTo(1100 + 1900, 6);
    expect(live.total_value).toBeCloseTo(4000, 6);
    expect(live.unrealized_pnl).toBeCloseTo(100 - 100, 6);
    const weights = live.positions.reduce((s, x) => s + x.weight, 0);
    expect(weights + live.cash / live.total_value).toBeCloseTo(1, 9);
    expect(live.positions[0].ticker).toBe("MSFT");
  });

  it("ignores non-finite streamed prices and keeps the server price", () => {
    const live = revaluePortfolio(portfolioFixture, {
      AAPL: { ticker: "AAPL", price: Number.NaN, previous_price: 1, timestamp: 1 },
    });
    expect(live.positions[0].current_price).toBe(195.02);
  });

  it("reports zero percent when there is no cost basis or total", () => {
    const live = revaluePortfolio({ ...portfolioFixture, cash: 0, positions: [] }, {});
    expect(live.total_value).toBe(0);
    expect(live.unrealized_pnl_percent).toBe(0);
  });

  it("does not mutate the server portfolio", () => {
    const copy = structuredClone(portfolioFixture);
    revaluePortfolio(portfolioFixture, { AAPL: { ticker: "AAPL", price: 1, previous_price: 1, timestamp: 1 } });
    expect(portfolioFixture).toEqual(copy);
  });
});

// ------------------------------------------------------------- heatmap / pnl

describe("heatmap sizing and colour", () => {
  it("sizes tiles by market value and colours them by P&L sign", () => {
    render(
      <Heatmap
        positions={[
          pos({ ticker: "AAPL", market_value: 3000, unrealized_pnl_percent: 4 }),
          pos({ ticker: "TSLA", market_value: 1000, unrealized_pnl_percent: -4 }),
        ]}
      />,
    );
    const area = (id: string) => {
      const s = screen.getByTestId(`heatmap-tile-${id}`).style;
      return parseFloat(s.width) * parseFloat(s.height);
    };
    expect(area("AAPL") / area("TSLA")).toBeCloseTo(3, 5);
    const rgb = (id: string) =>
      screen.getByTestId(`heatmap-tile-${id}`).style.backgroundColor.match(/\d+/g)!.map(Number);
    const [ar, ag] = rgb("AAPL");
    const [tr, tg] = rgb("TSLA");
    expect(ag).toBeGreaterThan(ar); // green-dominant
    expect(tr).toBeGreaterThan(tg); // red-dominant
  });

  it("selects a ticker when a tile is clicked", async () => {
    const onSelect = vi.fn();
    render(<Heatmap positions={[pos({ ticker: "AAPL" })]} onSelect={onSelect} />);
    await userEvent.click(screen.getByTestId("heatmap-tile-AAPL"));
    expect(onSelect).toHaveBeenCalledWith("AAPL");
  });
});

describe("P&L chart", () => {
  it("renders snapshot points and the change since the first snapshot", () => {
    render(
      <PnlChart
        snapshots={[
          { total_value: 10000, recorded_at: "2026-11-13T10:00:00+00:00" },
          { total_value: 10125.5, recorded_at: "2026-11-13T10:00:30+00:00" },
        ]}
      />,
    );
    const chart = screen.getByTestId("pnl-chart");
    expect(chart.querySelector("[data-points]")).toHaveAttribute("data-points", "2");
    expect(chart).toHaveTextContent("+$125.50");
    expect(chart).not.toHaveTextContent("Value history fills in");
  });

  it("shows a hint until there are two snapshots", () => {
    render(<PnlChart snapshots={[{ total_value: 10000, recorded_at: "2026-11-13T10:00:00+00:00" }]} />);
    expect(screen.getByTestId("pnl-chart")).toHaveTextContent("Value history fills in");
  });
});

// ----------------------------------------------------------- connection dot

describe("connection status indicator", () => {
  it.each([
    ["connected", "bg-up", "Live"],
    ["reconnecting", "bg-accent", "Reconnecting"],
    ["disconnected", "bg-down", "Offline"],
  ] as const)("%s → %s dot", (status, cls, label) => {
    render(<Header totalValue={1} cash={1} unrealizedPnl={0} unrealizedPnlPercent={0} status={status} />);
    const dot = screen.getByTestId("connection-status");
    expect(dot).toHaveAttribute("data-status", status);
    expect(dot.className).toContain(cls);
    expect(dot).toHaveAccessibleName(`Price stream ${label}`);
  });

  describe("stream hook", () => {
    function Probe() {
      const { status } = usePriceStream();
      return <span data-testid="s">{status}</span>;
    }
    beforeEach(() => {
      FakeEventSource.instances = [];
      vi.stubGlobal("EventSource", FakeEventSource);
    });
    afterEach(() => vi.unstubAllGlobals());

    it("goes back to connected when a message arrives after a native retry", () => {
      render(<Probe />);
      const es = FakeEventSource.instances[0];
      act(() => es.open());
      act(() => es.fail(false));
      expect(screen.getByTestId("s")).toHaveTextContent("reconnecting");
      expect(FakeEventSource.instances).toHaveLength(1); // browser handles the retry
      act(() => es.emit({ AAPL: { ticker: "AAPL", price: 1, previous_price: 1, timestamp: 1 } }));
      expect(screen.getByTestId("s")).toHaveTextContent("connected");
    });

    it("ignores non-object payloads", () => {
      render(<Probe />);
      const es = FakeEventSource.instances[0];
      act(() => es.emit([1, 2, 3]));
      act(() => es.emit(null));
      expect(screen.getByTestId("s")).toHaveTextContent("reconnecting");
    });
  });
});

// ------------------------------------------------------------------- chat

describe("chat input behaviour", () => {
  const reply: ChatMessage = {
    id: "a1",
    role: "assistant",
    content: "Mock: executing buy 2 NVDA.",
    created_at: "",
    actions: { trades: [{ ticker: "NVDA", side: "buy", quantity: 2, status: "executed", price: 800, error: null }] },
  };

  function setup(send = vi.fn().mockResolvedValue(reply)) {
    const onActions = vi.fn();
    render(
      <ChatPanel loadHistory={() => Promise.resolve([])} send={send} onActions={onActions} collapsed={false} onToggle={vi.fn()} />,
    );
    return { send, onActions, input: screen.getByTestId("chat-input") };
  }

  it("sends on Enter, clears the input and reports actions", async () => {
    const { send, onActions, input } = setup();
    await userEvent.type(input, "buy 2 NVDA{Enter}");
    expect(send).toHaveBeenCalledWith("buy 2 NVDA");
    expect(input).toHaveValue("");
    expect(await screen.findByTestId("chat-message-assistant")).toHaveTextContent("Mock: executing buy 2 NVDA.");
    expect(screen.getByTestId("chat-action")).toHaveTextContent("BUY 2 NVDA @ $800.00");
    expect(onActions).toHaveBeenCalledWith(reply.actions);
  });

  it("does not send blank messages and disables send while empty", async () => {
    const { send, input } = setup();
    expect(screen.getByTestId("chat-send")).toBeDisabled();
    await userEvent.type(input, "   {Enter}");
    expect(send).not.toHaveBeenCalled();
    expect(screen.queryByTestId("chat-message-user")).not.toBeInTheDocument();
  });

  it("does not send a second message while one is in flight", async () => {
    let resolve!: (m: ChatMessage) => void;
    const send = vi.fn(() => new Promise<ChatMessage>((r) => (resolve = r)));
    const { input } = setup(send);
    await userEvent.type(input, "first{Enter}");
    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();
    await userEvent.type(input, "second{Enter}");
    expect(send).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("chat-send")).toBeDisabled();
    await act(async () => resolve(reply));
    await waitFor(() => expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument());
  });

  it("styles failed actions distinctly", async () => {
    const failed: ChatMessage = {
      ...reply,
      actions: {
        trades: [{ ticker: "NVDA", side: "buy", quantity: 999, status: "failed", price: null, error: "Insufficient cash" }],
        watchlist_changes: [{ ticker: "ZZZZ", action: "remove", status: "failed", error: "not watched" }],
      },
    };
    const { input } = setup(vi.fn().mockResolvedValue(failed));
    await userEvent.type(input, "go{Enter}");
    const actions = await screen.findAllByTestId("chat-action");
    expect(actions.map((a) => a.textContent)).toEqual([
      "FAILED WATCHLIST REMOVE ZZZZ: not watched",
      "FAILED BUY 999 NVDA: Insufficient cash",
    ]);
    for (const a of actions) expect(a.className).toContain("text-down");
  });
});

// -------------------------------------------------------------- watchlist

describe("watchlist CRUD edge cases", () => {
  const rows: WatchlistRowData[] = [{ ticker: "AAPL", price: 190, dayChangePercent: 0 }];
  const base = {
    rows,
    history: {},
    selected: null,
    onSelect: vi.fn(),
    onAdd: vi.fn().mockResolvedValue(undefined),
    onRemove: vi.fn().mockResolvedValue(undefined),
  };

  it("rejects an empty symbol locally without calling the API", async () => {
    const onAdd = vi.fn();
    render(<Watchlist {...base} onAdd={onAdd} />);
    await userEvent.click(screen.getByTestId("watchlist-add-button"));
    expect(onAdd).not.toHaveBeenCalled();
    expect(screen.getByTestId("watchlist-error")).toHaveTextContent(/Enter a ticker/);
  });

  it("submits on Enter and keeps the input when the add fails", async () => {
    const onAdd = vi.fn().mockRejectedValue(new Error("PYPL is already on your watchlist"));
    render(<Watchlist {...base} onAdd={onAdd} />);
    await userEvent.type(screen.getByTestId("watchlist-add-input"), "pypl{Enter}");
    expect(onAdd).toHaveBeenCalledWith("PYPL");
    expect(await screen.findByTestId("watchlist-error")).toHaveTextContent("already on your watchlist");
    expect(screen.getByTestId("watchlist-add-input")).toHaveValue("PYPL");
  });

  it("shows an error when removal fails and clears it on the next action", async () => {
    const onRemove = vi.fn().mockRejectedValueOnce(new Error("AAPL is not on your watchlist"));
    render(<Watchlist {...base} onRemove={onRemove} />);
    await userEvent.click(screen.getByTestId("watchlist-remove-AAPL"));
    expect(await screen.findByTestId("watchlist-error")).toHaveTextContent("not on your watchlist");
    await userEvent.click(screen.getByTestId("watchlist-remove-AAPL"));
    await waitFor(() => expect(screen.queryByTestId("watchlist-error")).not.toBeInTheDocument());
  });

  it("selects a row with the keyboard", async () => {
    const onSelect = vi.fn();
    render(<Watchlist {...base} onSelect={onSelect} />);
    screen.getByTestId("watchlist-row-AAPL").focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith("AAPL");
  });

  it("shows an empty state", () => {
    render(<Watchlist {...base} rows={[]} />);
    expect(screen.getByTestId("watchlist")).toHaveTextContent("Your watchlist is empty");
  });
});

// -------------------------------------------------------------- trade bar

describe("trade bar", () => {
  it("sells and reports success", async () => {
    const onTrade = vi.fn().mockResolvedValue({
      id: "t", ticker: "AAPL", side: "sell", quantity: 1.5, price: 190.12, executed_at: "",
    });
    render(<TradeBar selectedTicker="AAPL" onTrade={onTrade} />);
    await userEvent.type(screen.getByTestId("trade-quantity"), "1.5");
    await userEvent.click(screen.getByTestId("trade-sell"));
    expect(onTrade).toHaveBeenCalledWith("AAPL", 1.5, "sell");
    expect(await screen.findByTestId("trade-success")).toHaveTextContent("Sold 1.5 AAPL at $190.12");
    expect(screen.queryByTestId("trade-error")).not.toBeInTheDocument();
  });

  it("requires a ticker and clears an old success on error", async () => {
    const onTrade = vi
      .fn()
      .mockResolvedValueOnce({ id: "t", ticker: "AAPL", side: "buy", quantity: 1, price: 1, executed_at: "" })
      .mockRejectedValueOnce(new Error("Insufficient shares: have 0, need 5"));
    render(<TradeBar selectedTicker={null} onTrade={onTrade} />);
    await userEvent.type(screen.getByTestId("trade-quantity"), "1");
    await userEvent.click(screen.getByTestId("trade-buy"));
    expect(screen.getByTestId("trade-error")).toHaveTextContent("Enter a ticker");
    expect(onTrade).not.toHaveBeenCalled();

    await userEvent.type(screen.getByTestId("trade-ticker"), "aapl");
    await userEvent.click(screen.getByTestId("trade-buy"));
    expect(await screen.findByTestId("trade-success")).toBeInTheDocument();

    await userEvent.clear(screen.getByTestId("trade-quantity"));
    await userEvent.type(screen.getByTestId("trade-quantity"), "5");
    await userEvent.click(screen.getByTestId("trade-sell"));
    expect(await screen.findByTestId("trade-error")).toHaveTextContent(/Insufficient shares/);
    expect(screen.queryByTestId("trade-success")).not.toBeInTheDocument();
  });
});
