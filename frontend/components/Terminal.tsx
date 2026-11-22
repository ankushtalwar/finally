"use client";

import { useCallback, useMemo, useState, useSyncExternalStore } from "react";
import Header from "./Header";
import Watchlist, { type WatchlistRowData } from "./Watchlist";
import MainChart from "./MainChart";
import Heatmap from "./Heatmap";
import PnlChart from "./PnlChart";
import PositionsTable from "./PositionsTable";
import TradeBar from "./TradeBar";
import ChatPanel from "./ChatPanel";
import { api } from "@/lib/api";
import { revaluePortfolio } from "@/lib/portfolio";
import type { ChatActions, Portfolio, Snapshot, WatchlistItem } from "@/lib/types";
import { usePriceStream } from "@/hooks/usePriceStream";
import { usePolling } from "@/hooks/usePolling";

const PORTFOLIO_POLL_MS = 5_000;
const HISTORY_POLL_MS = 30_000;
const WATCHLIST_POLL_MS = 15_000;
/** Below this width the chat starts collapsed and opens as an overlay drawer. */
const NARROW_QUERY = "(max-width: 1023px)";

function subscribeNarrow(onChange: () => void) {
  if (typeof window.matchMedia !== "function") return () => {};
  const mq = window.matchMedia(NARROW_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
const isNarrow = () => typeof window.matchMedia === "function" && window.matchMedia(NARROW_QUERY).matches;
const notNarrowOnServer = () => false;

export default function Terminal() {
  const { prices, history, status } = usePriceStream();
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  // null = follow the viewport (collapsed on tablet widths) until the user toggles it.
  const [chatPref, setChatPref] = useState<boolean | null>(null);
  const narrow = useSyncExternalStore(subscribeNarrow, isNarrow, notNarrowOnServer);
  const chatCollapsed = chatPref ?? narrow;

  const refreshPortfolio = useCallback(() => {
    api.getPortfolio().then(setPortfolio).catch(() => {});
  }, []);
  const refreshHistory = useCallback(() => {
    api.getHistory().then((r) => setSnapshots(r.snapshots)).catch(() => {});
  }, []);
  const refreshWatchlist = useCallback(() => {
    api
      .getWatchlist()
      .then((r) => {
        setWatchlist(r.tickers);
        setSelected((cur) => cur ?? r.tickers[0]?.ticker ?? null);
      })
      .catch(() => {});
  }, []);

  usePolling(refreshPortfolio, PORTFOLIO_POLL_MS);
  usePolling(refreshHistory, HISTORY_POLL_MS);
  usePolling(refreshWatchlist, WATCHLIST_POLL_MS);

  const live = useMemo(() => (portfolio ? revaluePortfolio(portfolio, prices) : null), [portfolio, prices]);

  const rows: WatchlistRowData[] = useMemo(
    () =>
      watchlist.map((w) => {
        const tick = prices[w.ticker];
        return {
          ticker: w.ticker,
          price: tick?.price ?? w.price,
          dayChangePercent: tick?.day_change_percent ?? w.day_change_percent ?? 0,
        };
      }),
    [watchlist, prices],
  );

  const addTicker = useCallback(
    async (ticker: string) => {
      const item = await api.addTicker(ticker);
      setWatchlist((cur) => (cur.some((w) => w.ticker === item.ticker) ? cur : [...cur, item]));
      refreshWatchlist();
    },
    [refreshWatchlist],
  );

  const removeTicker = useCallback(
    async (ticker: string) => {
      await api.removeTicker(ticker);
      setWatchlist((cur) => cur.filter((w) => w.ticker !== ticker));
      setSelected((cur) => (cur === ticker ? null : cur));
      refreshWatchlist();
    },
    [refreshWatchlist],
  );

  const trade = useCallback(
    async (ticker: string, quantity: number, side: "buy" | "sell") => {
      const res = await api.trade(ticker, quantity, side);
      setPortfolio(res.portfolio);
      refreshHistory();
      return res.trade;
    },
    [refreshHistory],
  );

  const loadChatHistory = useCallback(() => api.getChatHistory().then((r) => r.messages), []);
  const sendChat = useCallback((message: string) => api.sendChat(message), []);
  const onChatActions = useCallback(
    (actions: ChatActions | null) => {
      refreshPortfolio();
      if (actions?.trades?.length) refreshHistory();
      if (actions?.watchlist_changes?.length) refreshWatchlist();
    },
    [refreshPortfolio, refreshHistory, refreshWatchlist],
  );

  const selectedTick = selected ? prices[selected] : undefined;
  const selectedRow = rows.find((r) => r.ticker === selected);
  const positions = live?.positions ?? [];

  return (
    <div className="flex h-screen min-h-[600px] flex-col overflow-hidden">
      <Header
        totalValue={live?.total_value ?? null}
        cash={live?.cash ?? null}
        unrealizedPnl={live?.unrealized_pnl ?? null}
        unrealizedPnlPercent={live?.unrealized_pnl_percent ?? null}
        status={status}
      />
      <div className="relative flex min-h-0 flex-1">
        <main className="grid min-h-0 min-w-0 flex-1 grid-cols-[minmax(250px,290px)_minmax(0,1fr)] gap-1 p-1">
          <Watchlist
            rows={rows}
            history={history}
            selected={selected}
            onSelect={setSelected}
            onAdd={addTicker}
            onRemove={removeTicker}
          />
          <div className="grid min-h-0 min-w-0 grid-rows-[minmax(200px,1.15fr)_minmax(170px,0.85fr)_auto_minmax(130px,0.8fr)] gap-1">
            <MainChart
              ticker={selected}
              points={selected ? history[selected] ?? [] : []}
              price={selectedTick?.price ?? selectedRow?.price ?? null}
              dayChangePercent={selectedTick?.day_change_percent ?? selectedRow?.dayChangePercent ?? 0}
            />
            <div className="grid min-h-0 grid-cols-2 gap-1">
              <Heatmap positions={positions} onSelect={setSelected} />
              <PnlChart snapshots={snapshots} />
            </div>
            <TradeBar selectedTicker={selected} onTrade={trade} />
            <PositionsTable positions={positions} onSelect={setSelected} />
          </div>
        </main>
        <ChatPanel
          loadHistory={loadChatHistory}
          send={sendChat}
          onActions={onChatActions}
          collapsed={chatCollapsed}
          onToggle={() => setChatPref(!chatCollapsed)}
        />
      </div>
    </div>
  );
}
