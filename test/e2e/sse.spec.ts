import { expect, test } from "@playwright/test";
import { DEFAULT_TICKERS, expectSomePriceToChange, openApp } from "./helpers";

const STREAM = "**/api/stream/prices*";

test.describe("SSE resilience", () => {
  test("stream blocked at load: status is not connected, then recovers when unblocked", async ({ page }) => {
    await page.route(STREAM, (route) => route.abort("connectionrefused"));
    await page.goto("/");

    const status = page.getByTestId("connection-status");
    await expect(status).toHaveAttribute("data-status", /^(reconnecting|disconnected)$/, {
      timeout: 15_000,
    });

    await page.unroute(STREAM);
    await expect(status).toHaveAttribute("data-status", "connected", { timeout: 30_000 });
    await expect(page.getByTestId("watchlist-price-AAPL")).toHaveText(/\d+\.\d{2}/, { timeout: 20_000 });
    await expectSomePriceToChange(page, DEFAULT_TICKERS);
  });

  test("stream dropped mid-session: status leaves connected, then reconnects and live prices resume", async ({ page }) => {
    // The first stream connection gets one marker price and then ends, as if the
    // server dropped it. Reconnects are refused until `released`, then pass through
    // to the real backend.
    const MARKER = 123.45;
    let released = false;
    let connections = 0;
    await page.route(STREAM, async (route) => {
      connections += 1;
      if (released) return route.continue();
      if (connections > 1) return route.abort("connectionrefused");
      const now = Date.now() / 1000;
      const tick = {
        ticker: "AAPL", price: MARKER, previous_price: MARKER, timestamp: now,
        change: 0, change_percent: 0, direction: "flat", day_open: MARKER, day_change_percent: 0,
      };
      await route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
        body: `retry: 300\n\ndata: ${JSON.stringify({ AAPL: tick })}\n\n`,
      });
    });

    await page.goto("/");
    const status = page.getByTestId("connection-status");
    const aapl = page.getByTestId("watchlist-price-AAPL");

    // The stream delivered data, then dropped: the UI must notice.
    await expect(aapl).toHaveText(MARKER.toFixed(2), { timeout: 15_000 });
    await expect(status).toHaveAttribute("data-status", /^(reconnecting|disconnected)$/, {
      timeout: 15_000,
    });
    await expect
      .poll(() => connections, { message: "client should retry after the drop", timeout: 20_000 })
      .toBeGreaterThan(1);

    released = true;
    await expect(status).toHaveAttribute("data-status", "connected", { timeout: 30_000 });
    // Live data replaces the marker price.
    await expect(aapl).not.toHaveText(MARKER.toFixed(2), { timeout: 20_000 });
    await expect(aapl).toHaveText(/\d+\.\d{2}/);
    await expectSomePriceToChange(page, DEFAULT_TICKERS);
  });

  test("going offline then back online reconnects and prices resume", async ({ page, context }) => {
    await openApp(page);
    const status = page.getByTestId("connection-status");

    // Block new stream connections and drop the network. Chromium may or may not
    // tear down the already-open stream on offline emulation, so the intermediate
    // state is recorded rather than required; recovery is required.
    await page.route(STREAM, (route) => route.abort("internetdisconnected"));
    await context.setOffline(true);
    let sawNonConnected = false;
    try {
      await expect(status).toHaveAttribute("data-status", /^(reconnecting|disconnected)$/, {
        timeout: 8_000,
      });
      sawNonConnected = true;
    } catch {
      test.info().annotations.push({
        type: "note",
        description: "status stayed 'connected' while offline (browser kept the open stream alive)",
      });
    }

    await context.setOffline(false);
    await page.unroute(STREAM);
    await expect(status).toHaveAttribute("data-status", "connected", { timeout: 30_000 });
    await expectSomePriceToChange(page, DEFAULT_TICKERS, 20_000);
    test.info().annotations.push({ type: "sawNonConnected", description: String(sawNonConnected) });
  });
});
