import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Terminal from "@/components/Terminal";
import { FakeEventSource, jsonResponse, portfolioFixture } from "./mocks";

const watchItem = (ticker: string, price: number) => ({
  ticker,
  price,
  previous_price: price,
  change_percent: 0,
  day_change_percent: 0.5,
  direction: "flat",
  timestamp: 1000,
});

let fetchMock: ReturnType<typeof vi.fn>;
let watchlist: ReturnType<typeof watchItem>[];

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  watchlist = [watchItem("AAPL", 190), watchItem("MSFT", 410)];
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url === "/api/portfolio") return jsonResponse(portfolioFixture);
    if (url === "/api/portfolio/history")
      return jsonResponse({ snapshots: [{ total_value: 10000, recorded_at: "2026-11-13T10:00:00+00:00" }] });
    if (url === "/api/watchlist" && method === "GET") return jsonResponse({ tickers: watchlist });
    if (url === "/api/watchlist" && method === "POST") {
      const { ticker } = JSON.parse(String(init!.body));
      if (ticker === "ZZZZ") return jsonResponse({ detail: "Invalid ticker symbol" }, 400);
      const item = watchItem(ticker, 60);
      watchlist = [...watchlist, item];
      return jsonResponse(item, 201);
    }
    if (url.startsWith("/api/watchlist/") && method === "DELETE") {
      const t = url.split("/").pop();
      watchlist = watchlist.filter((w) => w.ticker !== t);
      return jsonResponse(null, 204);
    }
    if (url === "/api/portfolio/trade") {
      const body = JSON.parse(String(init!.body));
      return jsonResponse({
        trade: { id: "t", ticker: body.ticker, side: body.side, quantity: body.quantity, price: 410, executed_at: "" },
        portfolio: {
          ...portfolioFixture,
          cash: portfolioFixture.cash - 410,
          positions: [
            ...portfolioFixture.positions,
            {
              ticker: "MSFT",
              quantity: 1,
              avg_cost: 410,
              current_price: 410,
              market_value: 410,
              unrealized_pnl: 0,
              unrealized_pnl_percent: 0,
              weight: 0.04,
            },
          ],
        },
      });
    }
    if (url === "/api/chat/history") return jsonResponse({ messages: [] });
    if (url === "/api/chat")
      return jsonResponse({
        id: "a",
        role: "assistant",
        content: "Mock: executing buy 1 AAPL.",
        created_at: "",
        actions: {
          trades: [{ ticker: "AAPL", side: "buy", quantity: 1, status: "executed", price: 190, error: null }],
          watchlist_changes: [],
        },
      });
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Terminal", () => {
  it("loads data, streams prices and recomputes the header total live", async () => {
    render(<Terminal />);
    expect(await screen.findByTestId("watchlist-row-AAPL")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("total-value")).toHaveTextContent("$10,000.00"));
    expect(screen.getByTestId("cash-balance")).toHaveTextContent("$8,049.80");
    expect(screen.getByTestId("selected-ticker")).toHaveTextContent("AAPL");

    const es = FakeEventSource.instances[0];
    act(() => es.open());
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "connected");
    act(() =>
      es.emit({
        AAPL: { ticker: "AAPL", price: 200, previous_price: 190, timestamp: 1001, day_change_percent: 1.25 },
        MSFT: { ticker: "MSFT", price: 411, previous_price: 410, timestamp: 1001, day_change_percent: 0.1 },
      }),
    );
    expect(screen.getByTestId("watchlist-price-AAPL")).toHaveTextContent("200.00");
    expect(screen.getByTestId("watchlist-price-AAPL").className).toMatch(/flash-up/);
    // 8049.80 cash + 10 * 200
    expect(screen.getByTestId("total-value")).toHaveTextContent("$10,049.80");
    expect(screen.getByTestId("position-row-AAPL")).toHaveTextContent("200.00");
  });

  it("selects a ticker for the main chart", async () => {
    render(<Terminal />);
    await userEvent.click(await screen.findByTestId("watchlist-row-MSFT"));
    expect(screen.getByTestId("selected-ticker")).toHaveTextContent("MSFT");
    expect(screen.getByTestId("trade-ticker")).toHaveValue("MSFT");
  });

  it("adds and removes watchlist tickers, surfacing errors", async () => {
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    await userEvent.type(screen.getByTestId("watchlist-add-input"), "pypl");
    await userEvent.click(screen.getByTestId("watchlist-add-button"));
    expect(await screen.findByTestId("watchlist-row-PYPL")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/watchlist", expect.objectContaining({ method: "POST" }));

    await userEvent.type(screen.getByTestId("watchlist-add-input"), "zzzz");
    await userEvent.click(screen.getByTestId("watchlist-add-button"));
    expect(await screen.findByTestId("watchlist-error")).toHaveTextContent("Invalid ticker symbol");

    await userEvent.click(screen.getByTestId("watchlist-remove-PYPL"));
    await waitFor(() => expect(screen.queryByTestId("watchlist-row-PYPL")).not.toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith("/api/watchlist/PYPL", expect.objectContaining({ method: "DELETE" }));
  });

  it("executes a trade and updates cash and positions", async () => {
    render(<Terminal />);
    await userEvent.click(await screen.findByTestId("watchlist-row-MSFT"));
    await userEvent.type(screen.getByTestId("trade-quantity"), "1");
    await userEvent.click(screen.getByTestId("trade-buy"));
    expect(await screen.findByTestId("trade-success")).toHaveTextContent("Bought 1 MSFT");
    expect(await screen.findByTestId("position-row-MSFT")).toHaveTextContent("1");
    expect(screen.getByTestId("cash-balance")).toHaveTextContent("$7,639.80");
    expect(screen.getByTestId("heatmap-tile-MSFT")).toBeInTheDocument();
  });

  it("sends a chat message and refreshes the portfolio after actions", async () => {
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    const before = fetchMock.mock.calls.filter(([u]) => u === "/api/portfolio").length;
    const panel = screen.getByTestId("chat-panel");
    await userEvent.type(within(panel).getByTestId("chat-input"), "buy 1 AAPL{enter}");
    expect(await screen.findByTestId("chat-action")).toHaveTextContent("BUY 1 AAPL @ $190.00");
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([u]) => u === "/api/portfolio").length).toBeGreaterThan(before),
    );
  });

  it("polls the portfolio every 5 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<Terminal />);
      await screen.findByTestId("watchlist-row-AAPL");
      const count = () => fetchMock.mock.calls.filter(([u]) => u === "/api/portfolio").length;
      const start = count();
      await act(() => vi.advanceTimersByTimeAsync(5_000));
      expect(count()).toBe(start + 1);
      await act(() => vi.advanceTimersByTimeAsync(10_000));
      expect(count()).toBe(start + 3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a dash for tickers that have no price yet", async () => {
    watchlist = [{ ...watchItem("NEWT", 0), price: null as unknown as number, previous_price: null as unknown as number }];
    render(<Terminal />);
    expect(await screen.findByTestId("watchlist-price-NEWT")).toHaveTextContent("—");
    expect(screen.getByTestId("selected-ticker")).toHaveTextContent("NEWT");
  });

  it("loads chat history on mount", async () => {
    const base = fetchMock.getMockImplementation() as (url: string, init?: RequestInit) => Promise<Response>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
      url === "/api/chat/history"
        ? jsonResponse({
            messages: [
              { id: "u1", role: "user", content: "hello", actions: null, created_at: "" },
              {
                id: "a1",
                role: "assistant",
                content: "Mock: updating watchlist for PYPL.",
                actions: { trades: [], watchlist_changes: [{ ticker: "PYPL", action: "add", status: "executed", error: null }] },
                created_at: "",
              },
            ],
          })
        : base(url, init),
    );
    render(<Terminal />);
    expect(await screen.findByTestId("chat-message-user")).toHaveTextContent("hello");
    expect(screen.getByTestId("chat-message-assistant")).toHaveTextContent("PYPL");
    expect(screen.getByTestId("chat-action")).toHaveTextContent("WATCHLIST ADD PYPL");
  });

  describe("on tablet widths", () => {
    beforeEach(() => {
      vi.stubGlobal(
        "matchMedia",
        vi.fn((query: string) => ({
          matches: query.includes("max-width"),
          media: query,
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
        })),
      );
    });

    it("starts with the chat collapsed and lets the user open it", async () => {
      render(<Terminal />);
      await screen.findByTestId("watchlist-row-AAPL");
      expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Open AI assistant" }));
      expect(screen.getByTestId("chat-input")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Collapse AI assistant" }));
      expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument();
    });
  });
});
