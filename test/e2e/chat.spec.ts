import { expect, test } from "@playwright/test";
import { ensureNotWatched, ensureWatched, getWatchlistTickers, heldQuantity, openApp, sendChat } from "./helpers";

/**
 * Requires the app to run with LLM_MOCK=true (CONTRACT §5 mock triggers):
 *  - "(buy|sell) <qty> <TICKER>"  -> "Mock: executing {side} {qty} {TICKER}."
 *  - "(add|watch) <TICKER>"       -> "Mock: updating watchlist for {TICKER}."
 *  - otherwise                     -> "Mock: I'm FinAlly, your AI trading assistant. ..."
 */
test.describe("AI chat (mock mode)", () => {
  test.beforeAll(async ({ request }) => {
    const res = await request.get("/api/health");
    const health = (await res.json()) as { llm_mock?: boolean };
    expect(health.llm_mock, "app must run with LLM_MOCK=true for chat E2E tests").toBe(true);
  });

  test("'buy 2 NVDA' executes a trade shown inline and the position appears", async ({ page, request }) => {
    const TICKER = "NVDA";
    await ensureWatched(request, TICKER);
    const qtyBefore = await heldQuantity(request, TICKER);

    await openApp(page, TICKER);
    await sendChat(page, "buy 2 NVDA");

    await expect(page.getByTestId("chat-message-user").filter({ hasText: "buy 2 NVDA" }).last()).toBeVisible();
    const reply = page
      .getByTestId("chat-message-assistant")
      .filter({ hasText: "Mock: executing buy 2 NVDA." })
      .last();
    await expect(reply).toBeVisible({ timeout: 20_000 });

    const action = page.getByTestId("chat-action").filter({ hasText: TICKER }).last();
    await expect(action).toBeVisible();
    await expect(action).toContainText(/buy/i);
    await expect(action).not.toContainText(/failed/i);

    await expect(page.getByTestId(`position-row-${TICKER}`)).toBeVisible();
    await expect.poll(() => heldQuantity(request, TICKER)).toBeCloseTo(qtyBefore + 2, 6);
    await expect(page.getByTestId("chat-loading")).toHaveCount(0);
  });

  test("a plain message gets the fallback reply with no actions", async ({ page }) => {
    await openApp(page);
    const actionsBefore = await page.getByTestId("chat-action").count();

    await sendChat(page, "hello, how is my portfolio doing?");

    const reply = page
      .getByTestId("chat-message-assistant")
      .filter({ hasText: "Mock: I'm FinAlly, your AI trading assistant." })
      .last();
    await expect(reply).toBeVisible({ timeout: 20_000 });
    await expect(reply).toContainText(/Your portfolio is worth \$[\d,]+\.\d{2}/);
    expect(await page.getByTestId("chat-action").count()).toBe(actionsBefore);
  });

  test("'add ORCL' adds the ticker to the watchlist", async ({ page, request }) => {
    const TICKER = "ORCL";
    await ensureNotWatched(request, TICKER);
    try {
      await openApp(page);
      await sendChat(page, `add ${TICKER}`);

      await expect(
        page.getByTestId("chat-message-assistant").filter({ hasText: `Mock: updating watchlist for ${TICKER}.` }).last(),
      ).toBeVisible({ timeout: 20_000 });
      await expect(page.getByTestId("chat-action").filter({ hasText: TICKER }).last()).toBeVisible();
      await expect(page.getByTestId(`watchlist-row-${TICKER}`)).toBeVisible();
      expect(await getWatchlistTickers(request)).toContain(TICKER);
    } finally {
      await ensureNotWatched(request, TICKER);
    }
  });

  test("Enter key sends the message", async ({ page }) => {
    await openApp(page);
    const before = await page.getByTestId("chat-message-user").count();
    await page.getByTestId("chat-input").fill("hi there");
    await page.getByTestId("chat-input").press("Enter");
    await expect.poll(() => page.getByTestId("chat-message-user").count()).toBeGreaterThan(before);
  });
});
