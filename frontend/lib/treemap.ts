// Squarified treemap (Bruls, Huizing, van Wijk). Pure function, unit-tested.

export interface TreemapInput<T = unknown> {
  id: string;
  value: number;
  data?: T;
}

export interface TreemapRect<T = unknown> {
  id: string;
  value: number;
  data?: T;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

function worst(row: number[], side: number): number {
  if (row.length === 0) return Infinity;
  const sum = row.reduce((a, b) => a + b, 0);
  const max = Math.max(...row);
  const min = Math.min(...row);
  const s2 = side * side;
  const sum2 = sum * sum;
  return Math.max((s2 * max) / sum2, sum2 / (s2 * min));
}

export function squarify<T>(items: TreemapInput<T>[], width: number, height: number): TreemapRect<T>[] {
  const valid = items.filter((i) => Number.isFinite(i.value) && i.value > 0);
  if (valid.length === 0 || width <= 0 || height <= 0) return [];
  const total = valid.reduce((s, i) => s + i.value, 0);
  const scale = (width * height) / total;
  const sorted = [...valid].sort((a, b) => b.value - a.value);
  const areas = sorted.map((i) => i.value * scale);

  const out: TreemapRect<T>[] = [];
  let box: Box = { x: 0, y: 0, w: width, h: height };
  let row: number[] = [];
  let rowItems: TreemapInput<T>[] = [];

  const layoutRow = () => {
    const sum = row.reduce((a, b) => a + b, 0);
    if (box.w >= box.h) {
      // place a column on the left
      const colW = sum / box.h;
      let y = box.y;
      row.forEach((a, idx) => {
        const h = a / colW;
        const it = rowItems[idx];
        out.push({ id: it.id, value: it.value, data: it.data, x: box.x, y, w: colW, h });
        y += h;
      });
      box = { x: box.x + colW, y: box.y, w: box.w - colW, h: box.h };
    } else {
      // place a row on top
      const rowH = sum / box.w;
      let x = box.x;
      row.forEach((a, idx) => {
        const w = a / rowH;
        const it = rowItems[idx];
        out.push({ id: it.id, value: it.value, data: it.data, x, y: box.y, w, h: rowH });
        x += w;
      });
      box = { x: box.x, y: box.y + rowH, w: box.w, h: box.h - rowH };
    }
    row = [];
    rowItems = [];
  };

  areas.forEach((area, idx) => {
    const side = Math.min(box.w, box.h);
    if (row.length === 0 || worst([...row, area], side) <= worst(row, side)) {
      row.push(area);
      rowItems.push(sorted[idx]);
    } else {
      layoutRow();
      row.push(area);
      rowItems.push(sorted[idx]);
    }
  });
  if (row.length) layoutRow();
  return out;
}

/** Map a P&L % to a tile color: ramps from neutral gray toward green/red, saturating at ±5%. */
export function pnlColor(pct: number): string {
  const neutral = [48, 54, 61];
  if (!Number.isFinite(pct) || Math.abs(pct) < 0.005) return `rgb(${neutral.join(", ")})`;
  const t = Math.sqrt(Math.min(Math.abs(pct) / 5, 1));
  const target = pct > 0 ? [24, 150, 72] : [196, 44, 48];
  const mix = neutral.map((n, i) => Math.round(n + (target[i] - n) * t));
  return `rgb(${mix.join(", ")})`;
}
