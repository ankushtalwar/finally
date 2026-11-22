// Shapes mirror planning/CONTRACT.md §4.

export type Direction = "up" | "down" | "flat";

export interface PriceTick {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number; // unix seconds (float)
  change?: number;
  change_percent?: number;
  direction?: Direction;
  day_open?: number;
  day_change_percent?: number;
}

export type PriceMap = Record<string, PriceTick>;

export interface WatchlistItem {
  ticker: string;
  price: number | null;
  previous_price: number | null;
  change_percent: number;
  day_change_percent: number;
  direction: Direction;
  timestamp: number | null;
}

export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number;
  weight: number;
}

export interface Portfolio {
  cash: number;
  positions_value: number;
  total_value: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number;
  positions: Position[];
}

export interface Trade {
  id: string;
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  executed_at: string;
}

export interface Snapshot {
  total_value: number;
  recorded_at: string;
}

export interface TradeAction {
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  status: "executed" | "failed";
  price?: number | null;
  error?: string | null;
}

export interface WatchlistAction {
  ticker: string;
  action: "add" | "remove";
  status: "executed" | "failed";
  error?: string | null;
}

export interface ChatActions {
  trades?: TradeAction[];
  watchlist_changes?: WatchlistAction[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions: ChatActions | null;
  created_at: string;
}

export type ConnectionStatus = "connected" | "reconnecting" | "disconnected";
