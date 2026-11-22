import { expect, test } from "@playwright/test";
import { ensureNotWatched, ensureWatched, getPortfolio } from "./helpers";

// API-level contract checks (CONTRACT §4). Tickers: AAPL (read-only / rejected trades), META (sell-not-held), QQQQ/ZZZZ (watchlist).

test.describe("REST API contract", () => {
  test("GET /api/health", async ({ request }) => {
    const res = await request.get("/api/health");
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(["simulator", "massive"]).toContain(body.market_source);
    expect(typeof body.market_healthy).toBe("boolean");
    expect(typeof body.llm_mock).toBe("boolean");
  });

  test("GET /api/watchlist shape", async ({ request }) => {
    const res = await request.get("/api/watchlist");
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(Array.isArray(body.tickers)).toBe(true);
    expect(body.tickers.length).toBeGreaterThan(0);
    for (const item of body.tickers) {
      expect(typeof item.ticker).toBe("string");
      for (const key of ["price", "previous_price", "change_percent", "day_change_percent", "direction", "timestamp"]) {
        expect(item, `watchlist item ${item.ticker} missing ${key}`).toHaveProperty(key);
      }
    }
  });

  test("GET /api/portfolio shape", async ({ request }) => {
    const p = await getPortfolio(request);
    for (const key of ["cash", "positions_value", "total_value", "unrealized_pnl", "unrealized_pnl_percent"]) {
      expect(typeof (p as unknown as Record<string, unknown>)[key], key).toBe("number");
    }
    expect(Array.isArray(p.positions)).toBe(true);
    expect(p.total_value).toBeCloseTo(p.cash + p.positions_value, 2);
  });

  test("trade with insufficient cash -> 400 and nothing changes", async ({ request }) => {
    await ensureWatched(request, "AAPL");
    const before = await getPortfolio(request);
    const res = await request.post("/api/portfolio/trade", {
      data: { ticker: "AAPL", quantity: 1_000_000_000, side: "buy" },
    });
    expect(res.status()).toBe(400);
    const body = await res.json();
    expect(body.detail).toMatch(/insufficient cash/i);
    const after = await getPortfolio(request);
    expect(after.cash).toBeCloseTo(before.cash, 6);
  });

  test("sell more than held -> 400 Insufficient shares", async ({ request }) => {
    await ensureWatched(request, "META");
    const held = (await getPortfolio(request)).positions.find((x) => x.ticker === "META")?.quantity ?? 0;
    const res = await request.post("/api/portfolio/trade", {
      data: { ticker: "META", quantity: held + 1000, side: "sell" },
    });
    expect(res.status()).toBe(400);
    expect((await res.json()).detail).toMatch(/insufficient shares/i);
  });

  test("quantity <= 0 -> 400", async ({ request }) => {
    for (const quantity of [0, -5]) {
      const res = await request.post("/api/portfolio/trade", {
        data: { ticker: "AAPL", quantity, side: "buy" },
      });
      expect(res.status(), `quantity ${quantity}`).toBe(400);
      expect((await res.json()).detail).toMatch(/greater than 0/i);
    }
  });

  test("trade for a ticker with no price -> 400", async ({ request }) => {
    await ensureNotWatched(request, "ZZZZ");
    const res = await request.post("/api/portfolio/trade", {
      data: { ticker: "ZZZZ", quantity: 1, side: "buy" },
    });
    expect(res.status()).toBe(400);
    expect((await res.json()).detail).toMatch(/no price available/i);
  });

  test("invalid trade body -> 422", async ({ request }) => {
    const res = await request.post("/api/portfolio/trade", { data: { ticker: "AAPL" } });
    expect(res.status()).toBe(422);
  });

  test("watchlist: add 201 (normalized), duplicate 409, delete 204, delete again 404", async ({ request }) => {
    const T = "QQQQ";
    await ensureNotWatched(request, T);
    try {
      const add = await request.post("/api/watchlist", { data: { ticker: ` ${T.toLowerCase()} ` } });
      expect(add.status()).toBe(201);
      expect((await add.json()).ticker).toBe(T);

      const dup = await request.post("/api/watchlist", { data: { ticker: T } });
      expect(dup.status()).toBe(409);
      expect(typeof (await dup.json()).detail).toBe("string");

      const del = await request.delete(`/api/watchlist/${T}`);
      expect(del.status()).toBe(204);

      const del2 = await request.delete(`/api/watchlist/${T}`);
      expect(del2.status()).toBe(404);
    } finally {
      await ensureNotWatched(request, T);
    }
  });

  test("watchlist: invalid symbol -> 400", async ({ request }) => {
    for (const ticker of ["123!", "TOOLONGTICKER1"]) {
      const res = await request.post("/api/watchlist", { data: { ticker } });
      expect(res.status(), `ticker "${ticker}"`).toBe(400);
    }
  });

  test("chat: empty message -> 422", async ({ request }) => {
    const res = await request.post("/api/chat", { data: { message: "" } });
    expect(res.status()).toBe(422);
  });

  test("chat: mock fallback reply shape and history", async ({ request }) => {
    const health = await (await request.get("/api/health")).json();
    test.skip(health.llm_mock !== true, "requires LLM_MOCK=true");

    const res = await request.post("/api/chat", { data: { message: "what do you think?" } });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.role).toBe("assistant");
    expect(body.content).toMatch(/^Mock: I'm FinAlly, your AI trading assistant\./);
    expect(typeof body.id).toBe("string");
    expect(typeof body.created_at).toBe("string");

    const hist = await (await request.get("/api/chat/history")).json();
    expect(Array.isArray(hist.messages)).toBe(true);
    const last = hist.messages[hist.messages.length - 1];
    expect(last.role).toBe("assistant");
    expect(last.content).toBe(body.content);
    const prev = hist.messages[hist.messages.length - 2];
    expect(prev.role).toBe("user");
    expect(prev.actions).toBeNull();
  });

  test("chat: mock failed trade is reported with status 'failed'", async ({ request }) => {
    const health = await (await request.get("/api/health")).json();
    test.skip(health.llm_mock !== true, "requires LLM_MOCK=true");

    await ensureWatched(request, "AAPL");
    const res = await request.post("/api/chat", { data: { message: "sell 999999 AAPL" } });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.content).toBe("Mock: executing sell 999999 AAPL.");
    expect(body.actions.trades).toHaveLength(1);
    expect(body.actions.trades[0]).toMatchObject({ ticker: "AAPL", side: "sell", status: "failed" });
    expect(typeof body.actions.trades[0].error).toBe("string");
  });

  test("unknown non-API path falls back to the SPA", async ({ request }) => {
    const res = await request.get("/some/unknown/route");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("text/html");
  });

  test("GET /api/stream/prices is an SSE stream", async ({ page }) => {
    // APIRequestContext buffers whole bodies, so read the first event in the browser.
    // Navigate to a same-origin JSON page first so fetch() needs no CORS.
    await page.goto("/api/health");
    const first = await page.evaluate(async (url) => {
      const ctrl = new AbortController();
      const res = await fetch(url, { signal: ctrl.signal });
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      const deadline = Date.now() + 10_000;
      while (Date.now() < deadline && !buf.includes("data:")) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value);
      }
      // Read until the data line is complete.
      while (Date.now() < deadline && !/data:.*\n\n/s.test(buf)) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value);
      }
      ctrl.abort();
      return { contentType: res.headers.get("content-type"), body: buf };
    }, "/api/stream/prices");

    expect(first.contentType).toContain("text/event-stream");
    const dataLine = first.body.split("\n").find((l) => l.startsWith("data:"));
    expect(dataLine, `no data line in: ${first.body.slice(0, 200)}`).toBeTruthy();
    const payload = JSON.parse(dataLine!.slice(5).trim());
    expect(payload).toHaveProperty("AAPL");
    for (const key of ["ticker", "price", "previous_price", "timestamp", "direction"]) {
      expect(payload.AAPL).toHaveProperty(key);
    }
  });
});
