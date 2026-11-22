import type { Portfolio, Position, PriceMap } from "./types";

/**
 * Re-price a server portfolio snapshot with the latest streamed prices so the
 * header total and the table move between 5 s polls.
 */
export function revaluePortfolio(portfolio: Portfolio, prices: PriceMap): Portfolio {
  const positions: Position[] = portfolio.positions.map((p) => {
    const live = prices[p.ticker]?.price;
    const current = live != null && Number.isFinite(live) ? live : p.current_price;
    const market_value = current * p.quantity;
    const cost = p.avg_cost * p.quantity;
    const unrealized_pnl = market_value - cost;
    return {
      ...p,
      current_price: current,
      market_value,
      unrealized_pnl,
      unrealized_pnl_percent: cost > 0 ? (unrealized_pnl / cost) * 100 : 0,
      weight: 0,
    };
  });
  const positions_value = positions.reduce((s, p) => s + p.market_value, 0);
  const total_value = portfolio.cash + positions_value;
  const costBasis = positions.reduce((s, p) => s + p.avg_cost * p.quantity, 0);
  const unrealized_pnl = positions_value - costBasis;
  for (const p of positions) p.weight = total_value > 0 ? p.market_value / total_value : 0;
  positions.sort((a, b) => b.market_value - a.market_value);
  return {
    cash: portfolio.cash,
    positions_value,
    total_value,
    unrealized_pnl,
    unrealized_pnl_percent: costBasis > 0 ? (unrealized_pnl / costBasis) * 100 : 0,
    positions,
  };
}
