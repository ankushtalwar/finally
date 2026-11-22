interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
}

/** Build an SVG polyline `points` string scaled into the box. Exported for tests. */
export function sparklinePoints(values: number[], width: number, height: number, pad = 1.5): string {
  if (values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const stepX = (width - pad * 2) / (values.length - 1);
  return values
    .map((v, i) => {
      const x = pad + i * stepX;
      const y = pad + (height - pad * 2) * (1 - (v - min) / span);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

export default function Sparkline({ values, width = 72, height = 22 }: SparklineProps) {
  const recent = values.slice(-120);
  const pts = sparklinePoints(recent, width, height);
  const up = recent.length > 1 && recent[recent.length - 1] >= recent[0];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" className="block">
      {pts ? (
        <polyline
          points={pts}
          fill="none"
          stroke={up ? "var(--color-up)" : "var(--color-down)"}
          strokeWidth={1.2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ) : (
        <line x1={0} x2={width} y1={height / 2} y2={height / 2} stroke="var(--color-line)" strokeDasharray="2 3" />
      )}
    </svg>
  );
}
