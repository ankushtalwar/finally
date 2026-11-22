import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { appendPoint, applyBatch, usePriceStream } from "@/hooks/usePriceStream";
import { FakeEventSource } from "./mocks";

const tick = (ticker: string, price: number, timestamp: number) => ({
  ticker,
  price,
  previous_price: price,
  timestamp,
});

describe("appendPoint", () => {
  it("appends new seconds and replaces within the same second", () => {
    let s = appendPoint(undefined, tick("A", 1, 100.2));
    s = appendPoint(s, tick("A", 2, 100.7));
    expect(s).toEqual([{ time: 100, value: 2 }]);
    s = appendPoint(s, tick("A", 3, 101.1));
    expect(s).toEqual([
      { time: 100, value: 2 },
      { time: 101, value: 3 },
    ]);
  });
  it("ignores out-of-order ticks and caps length", () => {
    let s = appendPoint(undefined, tick("A", 1, 105));
    s = appendPoint(s, tick("A", 9, 100));
    expect(s).toEqual([{ time: 105, value: 1 }]);
    for (let i = 0; i < 10; i++) s = appendPoint(s, tick("A", i, 200 + i), 5);
    expect(s).toHaveLength(5);
    expect(s[4]).toEqual({ time: 209, value: 9 });
  });
});

describe("applyBatch", () => {
  it("drops tickers missing from the batch", () => {
    const first = applyBatch({}, { A: tick("A", 1, 1), B: tick("B", 2, 1) });
    const second = applyBatch(first.history, { A: tick("A", 1.5, 2) });
    expect(Object.keys(second.history)).toEqual(["A"]);
    expect(second.history.A).toHaveLength(2);
    expect(second.prices.B).toBeUndefined();
  });
});

function Probe() {
  const { prices, status, history } = usePriceStream();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="aapl">{prices.AAPL?.price ?? "none"}</span>
      <span data-testid="len">{history.AAPL?.length ?? 0}</span>
    </div>
  );
}

describe("usePriceStream", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("tracks connection status and prices", () => {
    const { unmount } = render(<Probe />);
    const es = FakeEventSource.instances[0];
    expect(es.url).toBe("/api/stream/prices");
    expect(screen.getByTestId("status")).toHaveTextContent("reconnecting");
    act(() => es.open());
    expect(screen.getByTestId("status")).toHaveTextContent("connected");
    act(() => es.emit({ AAPL: tick("AAPL", 190.12, 1000) }));
    act(() => es.emit({ AAPL: tick("AAPL", 190.5, 1001) }));
    expect(screen.getByTestId("aapl")).toHaveTextContent("190.5");
    expect(screen.getByTestId("len")).toHaveTextContent("2");
    act(() => es.fail(false));
    expect(screen.getByTestId("status")).toHaveTextContent("reconnecting");
    act(() => es.fail(true));
    expect(screen.getByTestId("status")).toHaveTextContent("disconnected");
    unmount();
    expect(es.close).toHaveBeenCalled();
  });

  it("reopens the stream with backoff after the browser gives up", () => {
    vi.useFakeTimers();
    try {
      const { unmount } = render(<Probe />);
      const first = FakeEventSource.instances[0];
      act(() => first.open());
      act(() => first.fail(true));
      expect(screen.getByTestId("status")).toHaveTextContent("disconnected");
      expect(first.close).toHaveBeenCalled();
      expect(FakeEventSource.instances).toHaveLength(1);

      act(() => vi.advanceTimersByTime(1_000));
      expect(FakeEventSource.instances).toHaveLength(2);
      const second = FakeEventSource.instances[1];

      // Fails again before opening: the next delay doubles.
      act(() => second.fail(true));
      act(() => vi.advanceTimersByTime(1_999));
      expect(FakeEventSource.instances).toHaveLength(2);
      act(() => vi.advanceTimersByTime(1));
      expect(FakeEventSource.instances).toHaveLength(3);

      const third = FakeEventSource.instances[2];
      act(() => third.open());
      expect(screen.getByTestId("status")).toHaveTextContent("connected");

      // A pending retry is cancelled on unmount.
      act(() => third.fail(true));
      unmount();
      act(() => vi.advanceTimersByTime(60_000));
      expect(FakeEventSource.instances).toHaveLength(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores malformed messages", () => {
    render(<Probe />);
    const es = FakeEventSource.instances[0];
    act(() => es.onmessage?.(new MessageEvent("message", { data: "not json" })));
    expect(screen.getByTestId("aapl")).toHaveTextContent("none");
  });
});
