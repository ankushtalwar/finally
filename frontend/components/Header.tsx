import { formatCurrency, formatPercent, formatSignedCurrency, pnlClass } from "@/lib/format";
import type { ConnectionStatus } from "@/lib/types";

interface HeaderProps {
  totalValue: number | null;
  cash: number | null;
  unrealizedPnl: number | null;
  unrealizedPnlPercent: number | null;
  status: ConnectionStatus;
}

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connected: "Live",
  reconnecting: "Reconnecting",
  disconnected: "Offline",
};

const STATUS_COLOR: Record<ConnectionStatus, string> = {
  connected: "bg-up",
  reconnecting: "bg-accent dot-reconnecting",
  disconnected: "bg-down",
};

export default function Header({ totalValue, cash, unrealizedPnl, unrealizedPnlPercent, status }: HeaderProps) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-6 border-b border-line bg-panel px-4">
      <div className="flex items-baseline gap-2">
        <span className="text-[17px] font-bold tracking-tight text-accent">FinAlly</span>
        <span className="hidden text-[11px] text-muted md:inline">AI trading workstation</span>
      </div>

      <div className="ml-auto flex items-center gap-6">
        <div className="flex flex-col items-end leading-tight">
          <span className="text-[10px] text-muted">Portfolio value</span>
          <span data-testid="total-value" className="num text-[18px] font-semibold text-text">
            {formatCurrency(totalValue)}
          </span>
        </div>
        <div className="flex flex-col items-end leading-tight">
          <span className="text-[10px] text-muted">Unrealized P&amp;L</span>
          <span className={`num text-[13px] ${pnlClass(unrealizedPnl ?? 0)}`}>
            {formatSignedCurrency(unrealizedPnl)}{" "}
            <span className="text-[11px]">{formatPercent(unrealizedPnlPercent)}</span>
          </span>
        </div>
        <div className="flex flex-col items-end leading-tight">
          <span className="text-[10px] text-muted">Cash</span>
          <span data-testid="cash-balance" className="num text-[13px] text-text">
            {formatCurrency(cash)}
          </span>
        </div>
        <div className="flex items-center gap-1.5 border-l border-line pl-4" title={`Price stream: ${STATUS_LABEL[status]}`}>
          <span
            data-testid="connection-status"
            data-status={status}
            aria-label={`Price stream ${STATUS_LABEL[status]}`}
            role="status"
            className={`inline-block h-2 w-2 rounded-full ${STATUS_COLOR[status]}`}
          />
          <span className="text-[11px] text-muted">{STATUS_LABEL[status]}</span>
        </div>
      </div>
    </header>
  );
}
