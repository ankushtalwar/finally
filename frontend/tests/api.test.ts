import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import { jsonResponse } from "./mocks";

function mockFetch(res: Response) {
  const fn = vi.fn().mockResolvedValue(res);
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("calls same-origin /api paths with JSON bodies", async () => {
    const fetchFn = mockFetch(jsonResponse({ trade: {}, portfolio: {} }));
    await api.trade("AAPL", 2, "buy");
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe("/api/portfolio/trade");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ ticker: "AAPL", quantity: 2, side: "buy" });
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("surfaces FastAPI string details as the error message", async () => {
    mockFetch(jsonResponse({ detail: "Insufficient cash: need $1.00, have $0.00" }, 400));
    const err = await api.trade("AAPL", 1, "buy").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
    expect(err.message).toMatch(/Insufficient cash/);
  });

  it("uses the first message of a 422 validation error array", async () => {
    mockFetch(jsonResponse({ detail: [{ msg: "Field required", loc: ["body", "ticker"] }] }, 422));
    await expect(api.addTicker("")).rejects.toThrow("Field required");
  });

  it("falls back to a generic message for non-JSON errors", async () => {
    mockFetch(new Response("boom", { status: 500 }));
    await expect(api.getPortfolio()).rejects.toThrow("Request failed (500)");
  });

  it("handles 204 No Content and URL-encodes the ticker on delete", async () => {
    const fetchFn = mockFetch(jsonResponse(null, 204));
    await expect(api.removeTicker("BRK.B")).resolves.toBeUndefined();
    expect(fetchFn.mock.calls[0][0]).toBe("/api/watchlist/BRK.B");
    expect(fetchFn.mock.calls[0][1].method).toBe("DELETE");
  });

  it("parses list endpoints", async () => {
    mockFetch(jsonResponse({ snapshots: [{ total_value: 10000, recorded_at: "2026-11-13T10:00:00+00:00" }] }));
    const res = await api.getHistory();
    expect(res.snapshots).toHaveLength(1);
  });
});
