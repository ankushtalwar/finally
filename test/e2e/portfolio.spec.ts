import { expect, test } from "@playwright/test";
import { ensureWatched, openApp } from "./helpers";

// GOOGL is used only by this spec.
const TICKER = "GOOGL";

test.describe("Portfolio visualization", () => {
  test("P&L chart renders and history has at least one snapshot", async ({ page, request }) => {
    const res = await request.get("/api/portfolio/history");
    expect(res.status()).toBe(200);
    const body = (await res.json()) as { snapshots: { total_value: number; recorded_at: string }[] };
    expect(Array.isArray(body.snapshots)).toBe(true);
    expect(body.snapshots.length).toBeGreaterThanOrEqual(1);
    for (const s of body.snapshots) {
      expect(typeof s.total_value).toBe("number");
      expect(typeof s.recorded_at).toBe("string");
    }

    await openApp(page);
    const chart = page.getByTestId("pnl-chart");
    await expect(chart).toBeVisible();
    const box = await chart.boundingBox();
    expect(box?.width ?? 0).toBeGreaterThan(50);
    expect(box?.height ?? 0).toBeGreaterThan(50);
  });

  test("a trade adds a snapshot and a heatmap tile", async ({ page, request }) => {
    await ensureWatched(request, TICKER);
    const before = (await (await request.get("/api/portfolio/history")).json()) as {
      snapshots: unknown[];
    };

    const trade = await request.post("/api/portfolio/trade", {
      data: { ticker: TICKER, quantity: 1, side: "buy" },
    });
    expect(trade.status(), await trade.text()).toBe(200);

    await expect
      .poll(async () => {
        const after = (await (await request.get("/api/portfolio/history")).json()) as {
          snapshots: unknown[];
        };
        // Capped at 1000, so ">= before" with a cap guard; normally strictly greater.
        return after.snapshots.length > before.snapshots.length || after.snapshots.length >= 1000;
      })
      .toBe(true);

    await openApp(page);
    await expect(page.getByTestId("heatmap")).toBeVisible();
    await expect(page.getByTestId(`heatmap-tile-${TICKER}`)).toBeVisible();
    await expect(page.getByTestId(`position-row-${TICKER}`)).toBeVisible();
  });
});
