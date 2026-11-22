import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";

/** The 10 default watchlist tickers seeded on a fresh DB (CONTRACT §2). */
export const DEFAULT_TICKERS = [
  "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX",
] as const;

export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number;
  weight: number;
}

export interface Portfolio {
  cash: number;
  positions_value: number;
  total_value: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number;
  positions: Position[];
}

/** Parse "$10,000.00" / "190.12" / "-$12.50" into a number. */
export function parseMoney(text: string | null): number {
  const cleaned = (text ?? "").replace(/[^0-9.\-]/g, "");
  const value = Number.parseFloat(cleaned);
  if (Number.isNaN(value)) throw new Error(`Cannot parse number from "${text}"`);
  return value;
}

/** Read a numeric text value from a locator, retrying until it parses. */
export async function readNumber(locator: Locator): Promise<number> {
  let value = Number.NaN;
  await expect
    .poll(async () => {
      const text = await locator.textContent();
      value = Number.parseFloat((text ?? "").replace(/[^0-9.\-]/g, ""));
      return Number.isFinite(value);
    }, { message: "waiting for a numeric value" })
    .toBe(true);
  return value;
}

export async function getPortfolio(request: APIRequestContext): Promise<Portfolio> {
  const res = await request.get("/api/portfolio");
  expect(res.status(), "GET /api/portfolio").toBe(200);
  return (await res.json()) as Portfolio;
}

export async function heldQuantity(request: APIRequestContext, ticker: string): Promise<number> {
  const p = await getPortfolio(request);
  return p.positions.find((pos) => pos.ticker === ticker)?.quantity ?? 0;
}

export async function getWatchlistTickers(request: APIRequestContext): Promise<string[]> {
  const res = await request.get("/api/watchlist");
  expect(res.status(), "GET /api/watchlist").toBe(200);
  const body = (await res.json()) as { tickers: { ticker: string }[] };
  return body.tickers.map((t) => t.ticker);
}

/** Ensure a ticker is on the watchlist (201 or 409 both fine). */
export async function ensureWatched(request: APIRequestContext, ticker: string): Promise<void> {
  const res = await request.post("/api/watchlist", { data: { ticker } });
  expect([201, 409], `POST /api/watchlist ${ticker} -> ${res.status()}`).toContain(res.status());
}

/** Ensure a ticker is NOT on the watchlist (204 or 404 both fine). */
export async function ensureNotWatched(request: APIRequestContext, ticker: string): Promise<void> {
  const res = await request.delete(`/api/watchlist/${ticker}`);
  expect([204, 404], `DELETE /api/watchlist/${ticker} -> ${res.status()}`).toContain(res.status());
}

/** Sell any existing holding of a ticker so the test starts from zero. */
export async function flattenPosition(request: APIRequestContext, ticker: string): Promise<void> {
  const qty = await heldQuantity(request, ticker);
  if (qty > 0) {
    const res = await request.post("/api/portfolio/trade", {
      data: { ticker, quantity: qty, side: "sell" },
    });
    expect(res.status(), `flatten ${ticker}: ${await res.text()}`).toBe(200);
  }
}

/** Open the app and wait until the SSE stream is connected and prices render. */
export async function openApp(page: Page, priceTicker = "AAPL"): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "connected", {
    timeout: 20_000,
  });
  await expect(page.getByTestId(`watchlist-price-${priceTicker}`)).toHaveText(/\d+\.\d{2}/, {
    timeout: 20_000,
  });
}

/** Snapshot of every visible watchlist price text keyed by ticker. */
export async function priceTexts(page: Page, tickers: readonly string[]): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const t of tickers) {
    out[t] = ((await page.getByTestId(`watchlist-price-${t}`).textContent()) ?? "").trim();
  }
  return out;
}

/** Wait until at least one watchlist price differs from the given snapshot. */
export async function expectSomePriceToChange(
  page: Page,
  tickers: readonly string[],
  timeout = 15_000,
): Promise<void> {
  const before = await priceTexts(page, tickers);
  await expect
    .poll(
      async () => {
        const now = await priceTexts(page, tickers);
        return tickers.some((t) => now[t] !== before[t] && /\d/.test(now[t]));
      },
      { message: "expected at least one watchlist price to change", timeout },
    )
    .toBe(true);
}

/**
 * Assert a position row has a cell whose whole text is the quantity (e.g. "3" or "3.00").
 * Row textContent concatenates cells without separators, so match per cell.
 */
export async function expectRowQuantity(row: Locator, quantity: number): Promise<void> {
  const pattern = new RegExp(`^\\s*${quantity}(\\.0+)?\\s*$`);
  await expect(row.locator("td").filter({ hasText: pattern }).first()).toBeVisible();
}

/** Submit a trade through the UI trade bar. */
export async function tradeViaUi(
  page: Page,
  side: "buy" | "sell",
  ticker: string,
  quantity: number,
): Promise<void> {
  await page.getByTestId("trade-ticker").fill(ticker);
  await page.getByTestId("trade-quantity").fill(String(quantity));
  await page.getByTestId(side === "buy" ? "trade-buy" : "trade-sell").click();
}

/** Send a chat message through the chat panel. */
export async function sendChat(page: Page, message: string): Promise<void> {
  await page.getByTestId("chat-input").fill(message);
  await page.getByTestId("chat-send").click();
}
