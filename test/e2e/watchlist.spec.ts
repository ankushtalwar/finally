import { expect, test } from "@playwright/test";
import { ensureNotWatched, ensureWatched, getWatchlistTickers, openApp } from "./helpers";

// PYPL is used only by this spec.
const TICKER = "PYPL";

test.describe("Watchlist", () => {
  test.beforeEach(async ({ request }) => {
    await ensureNotWatched(request, TICKER);
  });

  test.afterEach(async ({ request }) => {
    await ensureNotWatched(request, TICKER);
  });

  test("add a ticker (lowercase input is normalized) and then remove it", async ({ page, request }) => {
    await openApp(page);
    await expect(page.getByTestId(`watchlist-row-${TICKER}`)).toHaveCount(0);

    await page.getByTestId("watchlist-add-input").fill(TICKER.toLowerCase());
    await page.getByTestId("watchlist-add-button").click();

    const row = page.getByTestId(`watchlist-row-${TICKER}`);
    await expect(row).toBeVisible();
    // New ticker joins the price universe and starts streaming.
    await expect(page.getByTestId(`watchlist-price-${TICKER}`)).toHaveText(/\d+\.\d{2}/, {
      timeout: 20_000,
    });
    expect(await getWatchlistTickers(request)).toContain(TICKER);

    await page.getByTestId(`watchlist-remove-${TICKER}`).click();
    await expect(row).toHaveCount(0);
    await expect.poll(() => getWatchlistTickers(request)).not.toContain(TICKER);
  });

  test("adding a duplicate ticker shows an error", async ({ page, request }) => {
    await ensureWatched(request, "AAPL");
    await openApp(page);
    await page.getByTestId("watchlist-add-input").fill("AAPL");
    await page.getByTestId("watchlist-add-button").click();
    await expect(page.getByTestId("watchlist-error")).toBeVisible();
    // Still exactly one AAPL row.
    await expect(page.getByTestId("watchlist-row-AAPL")).toHaveCount(1);
  });
});
