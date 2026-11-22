import type { ChatMessage, Portfolio, Snapshot, Trade, WatchlistItem } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  } catch {
    /* not JSON */
  }
  return `Request failed (${res.status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new ApiError(await errorMessage(res), res.status);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  getPortfolio: () => request<Portfolio>("/api/portfolio"),
  getHistory: () => request<{ snapshots: Snapshot[] }>("/api/portfolio/history"),
  trade: (ticker: string, quantity: number, side: "buy" | "sell") =>
    request<{ trade: Trade; portfolio: Portfolio }>("/api/portfolio/trade", {
      method: "POST",
      body: JSON.stringify({ ticker, quantity, side }),
    }),
  getWatchlist: () => request<{ tickers: WatchlistItem[] }>("/api/watchlist"),
  addTicker: (ticker: string) =>
    request<WatchlistItem>("/api/watchlist", { method: "POST", body: JSON.stringify({ ticker }) }),
  removeTicker: (ticker: string) =>
    request<void>(`/api/watchlist/${encodeURIComponent(ticker)}`, { method: "DELETE" }),
  sendChat: (message: string) =>
    request<ChatMessage>("/api/chat", { method: "POST", body: JSON.stringify({ message }) }),
  getChatHistory: () => request<{ messages: ChatMessage[] }>("/api/chat/history"),
};
