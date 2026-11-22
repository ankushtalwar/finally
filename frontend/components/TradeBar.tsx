"use client";

import { useState } from "react";
import { formatCurrency, formatQuantity } from "@/lib/format";
import type { Trade } from "@/lib/types";

interface TradeBarProps {
  selectedTicker: string | null;
  onTrade: (ticker: string, quantity: number, side: "buy" | "sell") => Promise<Trade>;
}

export default function TradeBar({ selectedTicker, onTrade }: TradeBarProps) {
  const [ticker, setTicker] = useState(selectedTicker ?? "");
  const [lastSelected, setLastSelected] = useState(selectedTicker);
  const [quantity, setQuantity] = useState("");
  const [busy, setBusy] = useState<"buy" | "sell" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Follow the watchlist selection into the ticker field.
  if (selectedTicker !== lastSelected) {
    setLastSelected(selectedTicker);
    if (selectedTicker) setTicker(selectedTicker);
  }

  const submit = async (side: "buy" | "sell") => {
    setError(null);
    setSuccess(null);
    const t = ticker.trim().toUpperCase();
    const q = Number(quantity);
    if (!t) {
      setError("Enter a ticker to trade.");
      return;
    }
    if (!quantity.trim() || !Number.isFinite(q) || q <= 0) {
      setError("Quantity must be greater than 0.");
      return;
    }
    setBusy(side);
    try {
      const trade = await onTrade(t, q, side);
      const verb = trade.side === "buy" ? "Bought" : "Sold";
      setSuccess(`${verb} ${formatQuantity(trade.quantity)} ${trade.ticker} at ${formatCurrency(trade.price)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Trade failed.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <form
      className="flex shrink-0 flex-wrap items-center gap-2 border border-line bg-panel px-2.5 py-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        submit("buy");
      }}
    >
      <span className="text-[11px] font-semibold text-muted">Market order</span>
      <input
        data-testid="trade-ticker"
        aria-label="Ticker"
        value={ticker}
        onChange={(e) => setTicker(e.target.value.toUpperCase())}
        placeholder="Ticker"
        maxLength={10}
        className="num w-24 rounded-sm border border-line bg-bg px-2 py-1 text-[12px] uppercase placeholder:normal-case placeholder:text-faint focus:border-blue focus:outline-none"
      />
      <input
        data-testid="trade-quantity"
        aria-label="Quantity"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        placeholder="Qty"
        inputMode="decimal"
        className="num w-20 rounded-sm border border-line bg-bg px-2 py-1 text-[12px] placeholder:text-faint focus:border-blue focus:outline-none"
      />
      <button
        type="button"
        data-testid="trade-buy"
        disabled={busy !== null}
        onClick={() => submit("buy")}
        className="rounded-sm bg-purple px-4 py-1 text-[12px] font-semibold text-white hover:bg-purple-hi disabled:opacity-50"
      >
        {busy === "buy" ? "Buying…" : "Buy"}
      </button>
      <button
        type="button"
        data-testid="trade-sell"
        disabled={busy !== null}
        onClick={() => submit("sell")}
        className="rounded-sm border border-purple px-4 py-1 text-[12px] font-semibold text-text hover:bg-purple/30 disabled:opacity-50"
      >
        {busy === "sell" ? "Selling…" : "Sell"}
      </button>
      <div className="ml-1 min-w-0 flex-1 truncate text-[12px]" aria-live="polite">
        {error && (
          <span data-testid="trade-error" role="alert" className="text-down">
            {error}
          </span>
        )}
        {success && (
          <span data-testid="trade-success" className="text-up">
            {success}
          </span>
        )}
      </div>
    </form>
  );
}
