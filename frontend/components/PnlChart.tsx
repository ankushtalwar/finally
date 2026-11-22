"use client";

import { useMemo } from "react";
import Panel from "./Panel";
import TimeSeriesChart from "./TimeSeriesChart";
import type { Snapshot } from "@/lib/types";
import type { Point } from "@/hooks/usePriceStream";
import { formatSignedCurrency, pnlClass } from "@/lib/format";

/** Snapshots → ascending, one-per-second points (latest wins). */
export function snapshotsToPoints(snapshots: Snapshot[]): Point[] {
  const bySecond = new Map<number, number>();
  for (const s of snapshots) {
    const ms = Date.parse(s.recorded_at);
    if (!Number.isFinite(ms) || !Number.isFinite(s.total_value)) continue;
    bySecond.set(Math.floor(ms / 1000), s.total_value);
  }
  return [...bySecond.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time, value }));
}

export default function PnlChart({ snapshots }: { snapshots: Snapshot[] }) {
  const points = useMemo(() => snapshotsToPoints(snapshots), [snapshots]);
  const change = points.length > 1 ? points[points.length - 1].value - points[0].value : 0;
  const up = change >= 0;
  return (
    <Panel
      title="Portfolio value"
      testId="pnl-chart"
      className="h-full"
      bodyClassName="relative"
      right={
        <span className={`num text-[11px] ${pnlClass(change)}`} title="Change since first snapshot">
          {formatSignedCurrency(change)}
        </span>
      }
    >
      <div className="absolute inset-0 p-1" data-points={points.length}>
        <TimeSeriesChart
          key={up ? "up" : "down"}
          points={points}
          color={up ? "#2ebd6e" : "#ef5350"}
          fill={up ? "rgba(46,189,110,0.18)" : "rgba(239,83,80,0.18)"}
          secondsVisible={false}
        />
      </div>
      {points.length < 2 && (
        <p className="pointer-events-none absolute inset-x-0 top-1/2 text-center text-[12px] text-muted">
          Value history fills in as snapshots are recorded.
        </p>
      )}
    </Panel>
  );
}
