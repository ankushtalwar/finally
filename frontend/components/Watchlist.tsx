"use client";

import { useState, type FormEvent } from "react";
import Panel from "./Panel";
import Sparkline from "./Sparkline";
import { formatPercent, formatPrice, pnlClass } from "@/lib/format";
import type { Point } from "@/hooks/usePriceStream";

export interface WatchlistRowData {
  ticker: string;
  price: number | null;
  dayChangePercent: number;
}

interface FlashState {
  dir: "up" | "down" | null;
  n: number;
}

/** Track price changes and produce a flash direction (derived during render, no effects). */
export function usePriceFlash(price: number | null) {
  const [prev, setPrev] = useState(price);
  const [flash, setFlash] = useState<FlashState>({ dir: null, n: 0 });
  if (price !== prev) {
    setPrev(price);
    if (prev != null && price != null) {
      setFlash({ dir: price > prev ? "up" : "down", n: flash.n + 1 });
    }
  }
  const className = flash.dir ? `flash-${flash.dir} ${flash.n % 2 ? "flash-a" : "flash-b"}` : "";
  const clear = () => setFlash((f) => ({ dir: null, n: f.n }));
  return { className, clear };
}

interface RowProps {
  row: WatchlistRowData;
  points: Point[] | undefined;
  selected: boolean;
  onSelect: (ticker: string) => void;
  onRemove: (ticker: string) => void;
}

export function WatchlistRow({ row, points, selected, onSelect, onRemove }: RowProps) {
  const { className: flashClass, clear } = usePriceFlash(row.price);
  const values = points?.map((p) => p.value) ?? [];
  return (
    <div
      role="button"
      tabIndex={0}
      data-testid={`watchlist-row-${row.ticker}`}
      aria-pressed={selected}
      onClick={() => onSelect(row.ticker)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(row.ticker);
        }
      }}
      className={`group grid cursor-pointer grid-cols-[1fr_auto_auto_auto] items-center gap-2 border-b border-l-2 border-b-line-soft py-1 pl-2 pr-1 hover:bg-raised ${
        selected ? "border-l-accent bg-raised" : "border-l-transparent"
      }`}
    >
      <span className={`font-semibold ${selected ? "text-accent" : "text-text"}`}>{row.ticker}</span>
      <Sparkline values={values} />
      <div className="flex w-[78px] flex-col items-end leading-tight">
        <span
          data-testid={`watchlist-price-${row.ticker}`}
          onAnimationEnd={clear}
          className={`num rounded-sm px-1 text-[13px] text-text ${flashClass}`}
        >
          {formatPrice(row.price)}
        </span>
        <span className={`num px-1 text-[11px] ${pnlClass(row.dayChangePercent)}`}>
          {formatPercent(row.dayChangePercent)}
        </span>
      </div>
      <button
        type="button"
        data-testid={`watchlist-remove-${row.ticker}`}
        aria-label={`Remove ${row.ticker} from watchlist`}
        onClick={(e) => {
          e.stopPropagation();
          onRemove(row.ticker);
        }}
        className="flex h-5 w-5 items-center justify-center rounded-sm text-faint opacity-60 hover:bg-line hover:text-down group-hover:opacity-100"
      >
        ×
      </button>
    </div>
  );
}

interface WatchlistProps {
  rows: WatchlistRowData[];
  history: Record<string, Point[]>;
  selected: string | null;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => Promise<void>;
  onRemove: (ticker: string) => Promise<void>;
}

export default function Watchlist({ rows, history, selected, onSelect, onAdd, onRemove }: WatchlistProps) {
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ticker = input.trim().toUpperCase();
    if (!ticker) {
      setError("Enter a ticker symbol, like PYPL.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onAdd(ticker);
      setInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add that ticker.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (ticker: string) => {
    setError(null);
    try {
      await onRemove(ticker);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Couldn't remove ${ticker}.`);
    }
  };

  return (
    <Panel
      title="Watchlist"
      testId="watchlist"
      right={<span className="num text-[10px] text-faint">{rows.length}</span>}
      className="h-full"
      bodyClassName="flex flex-col"
    >
      <form onSubmit={submit} className="flex gap-1 border-b border-line-soft p-1.5">
        <input
          data-testid="watchlist-add-input"
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          placeholder="Add ticker"
          aria-label="Ticker to add"
          maxLength={10}
          className="num min-w-0 flex-1 rounded-sm border border-line bg-bg px-2 py-1 text-[12px] uppercase placeholder:normal-case placeholder:text-faint focus:border-blue focus:outline-none"
        />
        <button
          type="submit"
          data-testid="watchlist-add-button"
          disabled={busy}
          className="rounded-sm border border-line px-2.5 text-[12px] text-blue hover:border-blue disabled:opacity-50"
        >
          Add
        </button>
      </form>
      {error && (
        <p data-testid="watchlist-error" role="alert" className="border-b border-line-soft px-2 py-1 text-[11px] text-down">
          {error}
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.length === 0 ? (
          <p className="p-3 text-[12px] text-muted">Your watchlist is empty. Add a ticker above to start streaming prices.</p>
        ) : (
          rows.map((row) => (
            <WatchlistRow
              key={row.ticker}
              row={row}
              points={history[row.ticker]}
              selected={row.ticker === selected}
              onSelect={onSelect}
              onRemove={remove}
            />
          ))
        )}
      </div>
    </Panel>
  );
}
