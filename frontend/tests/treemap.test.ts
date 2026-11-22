import { describe, expect, it } from "vitest";
import { pnlColor, squarify } from "@/lib/treemap";

const area = (r: { w: number; h: number }) => r.w * r.h;

describe("squarify", () => {
  it("returns nothing for empty or zero input", () => {
    expect(squarify([], 100, 100)).toEqual([]);
    expect(squarify([{ id: "A", value: 0 }], 100, 100)).toEqual([]);
    expect(squarify([{ id: "A", value: 5 }], 0, 100)).toEqual([]);
  });

  it("fills the whole box with a single item", () => {
    const [r] = squarify([{ id: "A", value: 5 }], 300, 200);
    expect(r).toMatchObject({ id: "A", x: 0, y: 0, w: 300, h: 200 });
  });

  it("allocates area proportional to value and covers the box exactly", () => {
    const items = [6, 6, 4, 3, 2, 2, 1].map((v, i) => ({ id: `T${i}`, value: v }));
    const W = 600;
    const H = 400;
    const rects = squarify(items, W, H);
    expect(rects).toHaveLength(items.length);
    const total = items.reduce((s, i) => s + i.value, 0);
    for (const r of rects) {
      expect(area(r)).toBeCloseTo((r.value / total) * W * H, 6);
      expect(r.x).toBeGreaterThanOrEqual(-1e-9);
      expect(r.y).toBeGreaterThanOrEqual(-1e-9);
      expect(r.x + r.w).toBeLessThanOrEqual(W + 1e-6);
      expect(r.y + r.h).toBeLessThanOrEqual(H + 1e-6);
    }
    expect(rects.reduce((s, r) => s + area(r), 0)).toBeCloseTo(W * H, 6);
  });

  it("produces reasonably square tiles for equal values", () => {
    const rects = squarify(
      [1, 1, 1, 1].map((v, i) => ({ id: String(i), value: v })),
      200,
      200,
    );
    for (const r of rects) {
      const ratio = Math.max(r.w / r.h, r.h / r.w);
      expect(ratio).toBeLessThanOrEqual(1.0001);
    }
  });

  it("does not overlap tiles", () => {
    const rects = squarify(
      [10, 7, 5, 3, 1].map((v, i) => ({ id: String(i), value: v })),
      500,
      300,
    );
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        const a = rects[i];
        const b = rects[j];
        const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
        const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        expect(ox > 1e-6 && oy > 1e-6).toBe(false);
      }
    }
  });
});

describe("pnlColor", () => {
  it("is neutral near zero, green for gains, red for losses", () => {
    expect(pnlColor(0)).toBe("rgb(48, 54, 61)");
    const [, g1] = pnlColor(5).match(/\d+/g)!.map(Number);
    expect(g1).toBeGreaterThan(100);
    const [r2] = pnlColor(-5).match(/\d+/g)!.map(Number);
    expect(r2).toBeGreaterThan(150);
  });
});
