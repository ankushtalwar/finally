"use client";

import { useEffect, useRef, useState } from "react";
import type { ConnectionStatus, PriceMap, PriceTick } from "@/lib/types";

export interface Point {
  time: number; // unix seconds (integer)
  value: number;
}

export type History = Record<string, Point[]>;

export const MAX_POINTS = 1200;
export const RECONNECT_BASE_MS = 1_000;
export const RECONNECT_MAX_MS = 15_000;

/** Append a tick to a per-ticker series, one point per second (latest wins). */
export function appendPoint(series: Point[] | undefined, tick: PriceTick, max = MAX_POINTS): Point[] {
  const time = Math.floor(tick.timestamp);
  const next = series ? series.slice() : [];
  const last = next[next.length - 1];
  if (last && last.time === time) {
    next[next.length - 1] = { time, value: tick.price };
  } else if (!last || time > last.time) {
    next.push({ time, value: tick.price });
  } else {
    return series ?? next; // out-of-order tick: ignore
  }
  return next.length > max ? next.slice(next.length - max) : next;
}

/** Merge one SSE batch into prices + history. Tickers missing from the batch are dropped. */
export function applyBatch(
  prevHistory: History,
  batch: PriceMap,
): { prices: PriceMap; history: History } {
  const history: History = {};
  for (const [ticker, tick] of Object.entries(batch)) {
    const prev = prevHistory[ticker];
    const last = prev?.[prev.length - 1];
    const unchanged = last && last.time === Math.floor(tick.timestamp) && last.value === tick.price;
    history[ticker] = unchanged ? prev : appendPoint(prev, tick);
  }
  return { prices: batch, history };
}

export function usePriceStream(url = "/api/stream/prices") {
  const [prices, setPrices] = useState<PriceMap>({});
  const [history, setHistory] = useState<History>({});
  // "reconnecting" until the first open/message; same on server and client to avoid hydration mismatch.
  const [status, setStatus] = useState<ConnectionStatus>("reconnecting");
  const historyRef = useRef<History>({});

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let disposed = false;

    const connect = () => {
      timer = null;
      if (disposed) return;
      const source = new EventSource(url);
      es = source;
      source.onopen = () => {
        attempt = 0;
        setStatus("connected");
      };
      source.onerror = () => {
        if (source.readyState !== EventSource.CLOSED) {
          // Native retry in progress (server sent `retry: 1000`).
          setStatus("reconnecting");
          return;
        }
        // The browser gave up (e.g. a non-200 response while the server restarts):
        // retry ourselves with capped exponential backoff.
        setStatus("disconnected");
        source.close();
        if (!disposed && timer === null) {
          const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
          attempt += 1;
          timer = setTimeout(connect, delay);
        }
      };
      source.onmessage = (ev: MessageEvent) => {
        let batch: PriceMap;
        try {
          batch = JSON.parse(ev.data) as PriceMap;
        } catch {
          return;
        }
        if (!batch || typeof batch !== "object" || Array.isArray(batch)) return;
        const next = applyBatch(historyRef.current, batch);
        historyRef.current = next.history;
        setPrices(next.prices);
        setHistory(next.history);
        setStatus("connected");
      };
    };

    connect();
    return () => {
      disposed = true;
      if (timer !== null) clearTimeout(timer);
      es?.close();
    };
  }, [url]);

  return { prices, history, status };
}
