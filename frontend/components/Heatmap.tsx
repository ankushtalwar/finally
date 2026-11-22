"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Panel from "./Panel";
import { squarify, pnlColor } from "@/lib/treemap";
import { formatPercent, formatCurrency } from "@/lib/format";
import type { Position } from "@/lib/types";

interface HeatmapProps {
  positions: Position[];
  onSelect?: (ticker: string) => void;
}

export default function Heatmap({ positions, onSelect }: HeatmapProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 400, h: 200 });

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r && r.width > 0 && r.height > 0) setSize({ w: r.width, h: r.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const rects = useMemo(
    () =>
      squarify(
        positions.map((p) => ({ id: p.ticker, value: p.market_value, data: p })),
        size.w,
        size.h,
      ),
    [positions, size],
  );

  return (
    <Panel title="Holdings heatmap" testId="heatmap" className="h-full" bodyClassName="relative">
      <div ref={ref} className="absolute inset-1">
        {rects.length === 0 ? (
          <p className="p-3 text-[12px] text-muted">No positions yet. Buy a stock to see it sized by weight here.</p>
        ) : (
          rects.map((r) => {
            const p = r.data!;
            const showDetail = r.w > 56 && r.h > 34;
            return (
              <button
                type="button"
                key={r.id}
                data-testid={`heatmap-tile-${r.id}`}
                onClick={() => onSelect?.(r.id)}
                title={`${p.ticker}: ${formatCurrency(p.market_value)} (${(p.weight * 100).toFixed(1)}% of portfolio), P&L ${formatPercent(p.unrealized_pnl_percent)}`}
                className="absolute flex flex-col items-center justify-center overflow-hidden border border-bg text-center hover:brightness-125"
                style={{
                  left: r.x,
                  top: r.y,
                  width: r.w,
                  height: r.h,
                  backgroundColor: pnlColor(p.unrealized_pnl_percent),
                }}
              >
                {r.w > 26 && r.h > 16 && <span className="text-[12px] font-semibold text-white/95">{p.ticker}</span>}
                {showDetail && (
                  <span className="num text-[11px] text-white/80">{formatPercent(p.unrealized_pnl_percent)}</span>
                )}
              </button>
            );
          })
        )}
      </div>
    </Panel>
  );
}
