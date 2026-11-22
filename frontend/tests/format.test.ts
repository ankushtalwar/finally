import { describe, expect, it } from "vitest";
import {
  formatCurrency,
  formatPercent,
  formatPrice,
  formatQuantity,
  formatSignedCurrency,
  pnlClass,
} from "@/lib/format";

describe("format", () => {
  it("formats currency with $ and two decimals", () => {
    expect(formatCurrency(10000)).toBe("$10,000.00");
    expect(formatCurrency(8049.8)).toBe("$8,049.80");
    expect(formatCurrency(null)).toBe("—");
  });
  it("formats prices without a currency symbol", () => {
    expect(formatPrice(190.123)).toBe("190.12");
    expect(formatPrice(1234.5)).toBe("1,234.50");
    expect(formatPrice(null)).toBe("—");
  });
  it("formats signed percents", () => {
    expect(formatPercent(0.41)).toBe("+0.41%");
    expect(formatPercent(-1.5)).toBe("-1.50%");
    expect(formatPercent(0)).toBe("0.00%");
  });
  it("formats signed currency", () => {
    expect(formatSignedCurrency(50.2)).toBe("+$50.20");
    expect(formatSignedCurrency(-3.1)).toBe("-$3.10");
  });
  it("formats quantities", () => {
    expect(formatQuantity(10)).toBe("10");
    expect(formatQuantity(0.12345)).toBe("0.1235");
    expect(formatQuantity(1.5)).toBe("1.5");
  });
  it("picks P&L color classes", () => {
    expect(pnlClass(1)).toBe("text-up");
    expect(pnlClass(-1)).toBe("text-down");
    expect(pnlClass(0)).toBe("text-muted");
  });
});
