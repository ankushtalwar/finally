"use client";

import { useEffect, useRef, useState } from "react";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import type { Point } from "@/hooks/usePriceStream";

interface TimeSeriesChartProps {
  points: Point[];
  color: string;
  /** rgba fill for the area top; bottom fades to transparent */
  fill: string;
  secondsVisible?: boolean;
}

/**
 * Thin wrapper around Lightweight Charts (v5). The library is imported lazily
 * inside an effect so it never runs during static export / SSR.
 * Appends with `series.update` on new ticks; `setData` only on first load.
 */
export default function TimeSeriesChart({ points, color, fill, secondsVisible = true }: TimeSeriesChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const lastTimeRef = useRef<number | null>(null);
  const pointsRef = useRef(points);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    pointsRef.current = points;
  }, [points]);

  useEffect(() => {
    let disposed = false;
    const el = containerRef.current;
    if (!el) return;
    import("lightweight-charts")
      .then(({ createChart, AreaSeries, ColorType, CrosshairMode }) => {
        if (disposed) return;
        const chart = createChart(el, {
          autoSize: true,
          layout: {
            background: { type: ColorType.Solid, color: "transparent" },
            textColor: "#7c8695",
            fontSize: 11,
            attributionLogo: false,
          },
          grid: {
            vertLines: { color: "#1a212b" },
            horzLines: { color: "#1a212b" },
          },
          rightPriceScale: { borderColor: "#252d39" },
          timeScale: { borderColor: "#252d39", timeVisible: true, secondsVisible },
          crosshair: { mode: CrosshairMode.Normal },
        });
        const series = chart.addSeries(AreaSeries, {
          lineColor: color,
          topColor: fill,
          bottomColor: "rgba(0,0,0,0)",
          lineWidth: 2,
          priceLineVisible: true,
          priceLineColor: color,
        });
        chartRef.current = chart;
        seriesRef.current = series;
        setReady(true);
      })
      .catch(() => {
        /* chart unavailable (e.g. no canvas); panel still renders */
      });
    return () => {
      disposed = true;
      chartRef.current?.remove();
      chartRef.current = null;
      seriesRef.current = null;
      lastTimeRef.current = null;
    };
    // Created once per mount; colors are fixed per instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!ready || !series) return;
    const data = pointsRef.current;
    if (data.length === 0) {
      if (lastTimeRef.current !== null) series.setData([]);
      lastTimeRef.current = null;
      return;
    }
    const last = lastTimeRef.current;
    const newestTime = data[data.length - 1].time;
    if (last === null || newestTime < last) {
      series.setData(data.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
      chartRef.current?.timeScale().fitContent();
    } else {
      for (const p of data) {
        if (p.time >= last) series.update({ time: p.time as UTCTimestamp, value: p.value });
      }
    }
    lastTimeRef.current = newestTime;
  }, [points, ready]);

  return <div ref={containerRef} className="h-full w-full" />;
}
