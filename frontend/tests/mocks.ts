import { vi } from "vitest";
import type { Portfolio } from "@/lib/types";

export const portfolioFixture: Portfolio = {
  cash: 8049.8,
  positions_value: 1950.2,
  total_value: 10000.0,
  unrealized_pnl: 50.2,
  unrealized_pnl_percent: 2.64,
  positions: [
    {
      ticker: "AAPL",
      quantity: 10,
      avg_cost: 190.0,
      current_price: 195.02,
      market_value: 1950.2,
      unrealized_pnl: 50.2,
      unrealized_pnl_percent: 2.64,
      weight: 0.195,
    },
  ],
};

/** Minimal controllable EventSource. */
export class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];
  url: string;
  readyState = 0;
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  close = vi.fn(() => {
    this.readyState = 2;
  });
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }
  emit(data: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(data) }));
  }
  fail(closed = false) {
    this.readyState = closed ? 2 : 0;
    this.onerror?.(new Event("error"));
  }
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
