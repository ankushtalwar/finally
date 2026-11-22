import Panel from "./Panel";
import {
  formatCurrency,
  formatPercent,
  formatPrice,
  formatQuantity,
  formatSignedCurrency,
  pnlClass,
} from "@/lib/format";
import type { Position } from "@/lib/types";

interface PositionsTableProps {
  positions: Position[];
  onSelect?: (ticker: string) => void;
}

export default function PositionsTable({ positions, onSelect }: PositionsTableProps) {
  return (
    <Panel title="Positions" className="h-full" bodyClassName="overflow-auto">
      <table data-testid="positions-table" className="w-full border-collapse text-[12px]">
        <thead className="sticky top-0 bg-panel text-[10px] text-muted">
          <tr className="border-b border-line-soft">
            <th className="px-2.5 py-1 text-left font-medium">Ticker</th>
            <th className="px-2 py-1 text-right font-medium">Qty</th>
            <th className="px-2 py-1 text-right font-medium">Avg cost</th>
            <th className="px-2 py-1 text-right font-medium">Price</th>
            <th className="px-2 py-1 text-right font-medium">Value</th>
            <th className="px-2 py-1 text-right font-medium">Unrealized P&amp;L</th>
            <th className="px-2.5 py-1 text-right font-medium">%</th>
          </tr>
        </thead>
        <tbody>
          {positions.length === 0 ? (
            <tr>
              <td colSpan={7} className="px-2.5 py-3 text-muted">
                No open positions. Use the trade bar or ask FinAlly to buy something.
              </td>
            </tr>
          ) : (
            positions.map((p) => (
              <tr
                key={p.ticker}
                data-testid={`position-row-${p.ticker}`}
                onClick={() => onSelect?.(p.ticker)}
                className="cursor-pointer border-b border-line-soft hover:bg-raised"
              >
                <td className="px-2.5 py-1 font-semibold">{p.ticker}</td>
                <td className="num px-2 py-1 text-right">{formatQuantity(p.quantity)}</td>
                <td className="num px-2 py-1 text-right text-muted">{formatPrice(p.avg_cost)}</td>
                <td className="num px-2 py-1 text-right">{formatPrice(p.current_price)}</td>
                <td className="num px-2 py-1 text-right">{formatCurrency(p.market_value)}</td>
                <td className={`num px-2 py-1 text-right ${pnlClass(p.unrealized_pnl)}`}>
                  {formatSignedCurrency(p.unrealized_pnl)}
                </td>
                <td className={`num px-2.5 py-1 text-right ${pnlClass(p.unrealized_pnl_percent)}`}>
                  {formatPercent(p.unrealized_pnl_percent)}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </Panel>
  );
}
