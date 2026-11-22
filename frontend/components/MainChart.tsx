"use client";

import Panel from "./Panel";
import TimeSeriesChart from "./TimeSeriesChart";
import { formatPercent, formatPrice, pnlClass } from "@/lib/format";
import type { Point } from "@/hooks/usePriceStream";

interface MainChartProps {
  ticker: string | null;
  points: Point[];
  price: number | null;
  dayChangePercent: number;
}

export default function MainChart({ ticker, points, price, dayChangePercent }: MainChartProps) {
  return (
    <Panel
      title="Price"
      testId="main-chart"
      className="h-full"
      bodyClassName="relative"
      right={
        <div className="flex items-baseline gap-3">
          <span data-testid="selected-ticker" className="text-[13px] font-semibold text-accent">
            {ticker ?? "—"}
          </span>
          <span className="num text-[13px] text-text">{formatPrice(price)}</span>
          <span className={`num text-[11px] ${pnlClass(dayChangePercent)}`}>{formatPercent(dayChangePercent)}</span>
        </div>
      }
    >
      {ticker ? (
        <>
          <div className="absolute inset-0 p-1">
            <TimeSeriesChart key={ticker} points={points} color="#209dd7" fill="rgba(32,157,215,0.22)" />
          </div>
          {points.length < 2 && (
            <p className="pointer-events-none absolute inset-x-0 top-1/2 text-center text-[12px] text-muted">
              Collecting ticks for {ticker}…
            </p>
          )}
        </>
      ) : (
        <p className="p-4 text-[12px] text-muted">Select a ticker in the watchlist to chart it.</p>
      )}
    </Panel>
  );
}
