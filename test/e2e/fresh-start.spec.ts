import { expect, test } from "@playwright/test";
import { DEFAULT_TICKERS, expectSomePriceToChange, getPortfolio, openApp } from "./helpers";

/**
 * Runs first (its own Playwright project) against a fresh DB, before any other
 * spec trades. Set FRESH_DB=false when running locally against a used DB to
 * skip the exact $10,000 assertions.
 */
const freshDb = (process.env.FRESH_DB ?? "true").toLowerCase() !== "false";

test.describe("Fresh start", () => {
  test("shows the 10 default tickers in the watchlist", async ({ page }) => {
    await openApp(page);
    const watchlist = page.getByTestId("watchlist");
    await expect(watchlist).toBeVisible();
    for (const ticker of DEFAULT_TICKERS) {
      await expect(page.getByTestId(`watchlist-row-${ticker}`)).toBeVisible();
      await expect(page.getByTestId(`watchlist-price-${ticker}`)).toHaveText(/\d+\.\d{2}/);
    }
  });

  test("shows $10,000.00 cash and total value", async ({ page, request }) => {
    test.skip(!freshDb, "FRESH_DB=false: database is not fresh");
    const portfolio = await getPortfolio(request);
    expect(portfolio.cash).toBe(10000);
    expect(portfolio.positions).toHaveLength(0);

    await openApp(page);
    await expect(page.getByTestId("cash-balance")).toHaveText(/\$10,000\.00/);
    await expect(page.getByTestId("total-value")).toHaveText(/\$10,000\.00/);
  });

  test("streams prices and reports connected status", async ({ page }) => {
    await openApp(page);
    await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "connected");
    await expectSomePriceToChange(page, DEFAULT_TICKERS);
  });

  test("clicking a watchlist row selects it for the main chart", async ({ page }) => {
    await openApp(page);
    await expect(page.getByTestId("main-chart")).toBeVisible();
    await page.getByTestId("watchlist-row-MSFT").click();
    await expect(page.getByTestId("selected-ticker")).toContainText("MSFT");
    await page.getByTestId("watchlist-row-TSLA").click();
    await expect(page.getByTestId("selected-ticker")).toContainText("TSLA");
  });

  test("renders the main layout panels", async ({ page }) => {
    await openApp(page);
    for (const id of ["heatmap", "pnl-chart", "positions-table", "chat-panel", "chat-input"]) {
      await expect(page.getByTestId(id), `testid ${id}`).toBeVisible();
    }
  });
});
