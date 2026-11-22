import { expect, test } from "@playwright/test";
import {
  ensureWatched,
  expectRowQuantity,
  flattenPosition,
  heldQuantity,
  openApp,
  readNumber,
  tradeViaUi,
} from "./helpers";

// Distinct tickers per test so specs don't interfere: JPM (buy), V (sell), AMZN (errors).

test.describe("Trading", () => {
  test("buy shares: cash decreases, position row and heatmap tile appear", async ({ page, request }) => {
    const TICKER = "JPM";
    await ensureWatched(request, TICKER);
    await flattenPosition(request, TICKER);

    await openApp(page, TICKER);
    await expect(page.getByTestId(`position-row-${TICKER}`)).toHaveCount(0);
    const cashBefore = await readNumber(page.getByTestId("cash-balance"));

    await tradeViaUi(page, "buy", TICKER, 3);

    await expect(page.getByTestId("trade-success")).toBeVisible();
    await expect(page.getByTestId("trade-error")).toHaveCount(0);

    const row = page.getByTestId(`position-row-${TICKER}`);
    await expect(row).toBeVisible();
    await expectRowQuantity(row, 3);
    await expect(page.getByTestId(`heatmap-tile-${TICKER}`)).toBeVisible();

    await expect
      .poll(() => readNumber(page.getByTestId("cash-balance")), { message: "cash should decrease" })
      .toBeLessThan(cashBefore);

    expect(await heldQuantity(request, TICKER)).toBeCloseTo(3, 6);
  });

  test("sell shares: cash increases, position updates then disappears", async ({ page, request }) => {
    const TICKER = "V";
    await ensureWatched(request, TICKER);
    await flattenPosition(request, TICKER);
    const setup = await request.post("/api/portfolio/trade", {
      data: { ticker: TICKER, quantity: 5, side: "buy" },
    });
    expect(setup.status(), await setup.text()).toBe(200);

    await openApp(page, TICKER);
    const row = page.getByTestId(`position-row-${TICKER}`);
    await expect(row).toBeVisible();
    await expect(page.getByTestId(`heatmap-tile-${TICKER}`)).toBeVisible();

    // Partial sell.
    const cashBefore = await readNumber(page.getByTestId("cash-balance"));
    await tradeViaUi(page, "sell", TICKER, 2);
    await expect(page.getByTestId("trade-success")).toBeVisible();
    await expect
      .poll(() => readNumber(page.getByTestId("cash-balance")), { message: "cash should increase" })
      .toBeGreaterThan(cashBefore);
    await expect(row).toBeVisible();
    await expectRowQuantity(row, 3);
    expect(await heldQuantity(request, TICKER)).toBeCloseTo(3, 6);

    // Sell the rest: row and tile go away.
    const cashMid = await readNumber(page.getByTestId("cash-balance"));
    await tradeViaUi(page, "sell", TICKER, 3);
    await expect(row).toHaveCount(0);
    await expect(page.getByTestId(`heatmap-tile-${TICKER}`)).toHaveCount(0);
    await expect
      .poll(() => readNumber(page.getByTestId("cash-balance")))
      .toBeGreaterThan(cashMid);
    expect(await heldQuantity(request, TICKER)).toBe(0);
  });

  test("buying with insufficient cash shows a trade error", async ({ page, request }) => {
    const TICKER = "AMZN";
    await ensureWatched(request, TICKER);
    const qtyBefore = await heldQuantity(request, TICKER);

    await openApp(page, TICKER);
    const cashBefore = await readNumber(page.getByTestId("cash-balance"));
    await tradeViaUi(page, "buy", TICKER, 1_000_000);

    await expect(page.getByTestId("trade-error")).toBeVisible();
    await expect(page.getByTestId("trade-error")).toContainText(/insufficient cash/i);
    expect(await heldQuantity(request, TICKER)).toBe(qtyBefore);
    expect(await readNumber(page.getByTestId("cash-balance"))).toBeCloseTo(cashBefore, 2);
  });

  test("selling shares not held shows a trade error", async ({ page, request }) => {
    const TICKER = "AMZN";
    await ensureWatched(request, TICKER);
    await flattenPosition(request, TICKER);

    await openApp(page, TICKER);
    await tradeViaUi(page, "sell", TICKER, 1);
    await expect(page.getByTestId("trade-error")).toBeVisible();
    await expect(page.getByTestId("trade-error")).toContainText(/insufficient shares/i);
  });
});
