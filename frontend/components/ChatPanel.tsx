"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { formatCurrency, formatQuantity } from "@/lib/format";
import type { ChatActions, ChatMessage, TradeAction, WatchlistAction } from "@/lib/types";

export function tradeActionText(a: TradeAction): string {
  const base = `${a.side.toUpperCase()} ${formatQuantity(a.quantity)} ${a.ticker}`;
  if (a.status === "executed") {
    return a.price != null ? `${base} @ ${formatCurrency(a.price)}` : base;
  }
  return `FAILED ${base}${a.error ? `: ${a.error}` : ""}`;
}

export function watchlistActionText(a: WatchlistAction): string {
  const base = `WATCHLIST ${a.action.toUpperCase()} ${a.ticker}`;
  return a.status === "executed" ? base : `FAILED ${base}${a.error ? `: ${a.error}` : ""}`;
}

function Actions({ actions }: { actions: ChatActions | null }) {
  if (!actions) return null;
  const items = [
    ...(actions.watchlist_changes ?? []).map((a) => ({ ok: a.status === "executed", text: watchlistActionText(a) })),
    ...(actions.trades ?? []).map((a) => ({ ok: a.status === "executed", text: tradeActionText(a) })),
  ];
  if (items.length === 0) return null;
  return (
    <ul className="mt-1.5 flex flex-col gap-1">
      {items.map((it, i) => (
        <li
          key={i}
          data-testid="chat-action"
          className={`num rounded-sm border-l-2 bg-bg/60 px-2 py-0.5 text-[11px] ${
            it.ok ? "border-l-up text-up" : "border-l-down text-down"
          }`}
        >
          {it.text}
        </li>
      ))}
    </ul>
  );
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div
      data-testid={isUser ? "chat-message-user" : "chat-message-assistant"}
      className={`max-w-[92%] rounded-md px-2.5 py-1.5 text-[12.5px] leading-snug ${
        isUser ? "self-end bg-blue/15 text-text" : "self-start border border-line bg-raised text-text"
      }`}
    >
      <p className="whitespace-pre-wrap break-words">{message.content}</p>
      {!isUser && <Actions actions={message.actions} />}
    </div>
  );
}

interface ChatPanelProps {
  loadHistory: () => Promise<ChatMessage[]>;
  send: (message: string) => Promise<ChatMessage>;
  onActions?: (actions: ChatActions | null) => void;
  collapsed: boolean;
  onToggle: () => void;
}

let localId = 0;

export default function ChatPanel({ loadHistory, send, onActions, collapsed, onToggle }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    loadHistory()
      .then((m) => {
        if (!cancelled) setMessages((cur) => (cur.length ? cur : m));
      })
      .catch(() => {
        /* history is optional */
      });
    return () => {
      cancelled = true;
    };
  }, [loadHistory]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && typeof el.scrollTo === "function") el.scrollTo({ top: el.scrollHeight });
  }, [messages, loading, collapsed]);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    const userMsg: ChatMessage = {
      id: `local-${++localId}`,
      role: "user",
      content: text,
      actions: null,
      created_at: new Date().toISOString(),
    };
    setMessages((m) => [...m, userMsg]);
    setLoading(true);
    try {
      const reply = await send(text);
      setMessages((m) => [...m, reply]);
      onActions?.(reply.actions);
    } catch (err) {
      setMessages((m) => [
        ...m,
        {
          id: `local-${++localId}`,
          role: "assistant",
          content: `Couldn't reach FinAlly: ${err instanceof Error ? err.message : "network error"}. Try again.`,
          actions: null,
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  if (collapsed) {
    return (
      <aside data-testid="chat-panel" className="flex w-9 shrink-0 flex-col items-center border-l border-line bg-panel py-2">
        <button
          type="button"
          onClick={onToggle}
          aria-label="Open AI assistant"
          aria-expanded={false}
          className="rounded-sm px-1 py-2 text-[11px] font-semibold text-accent [writing-mode:vertical-rl] hover:bg-raised"
        >
          FinAlly AI
        </button>
      </aside>
    );
  }

  return (
    <aside
      data-testid="chat-panel"
      className="flex w-[340px] shrink-0 flex-col border-l border-line bg-panel max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-30 max-lg:shadow-[-12px_0_32px_rgba(0,0,0,0.5)]"
    >
      <header className="flex h-9 shrink-0 items-center justify-between border-b border-line px-3">
        <div className="flex items-baseline gap-2">
          <span className="text-[12px] font-semibold text-accent">FinAlly AI</span>
          <span className="text-[11px] text-muted">can trade and edit your watchlist</span>
        </div>
        <button
          type="button"
          onClick={onToggle}
          aria-label="Collapse AI assistant"
          aria-expanded={true}
          className="rounded-sm px-1.5 text-muted hover:bg-raised hover:text-text"
        >
          »
        </button>
      </header>
      <div ref={scrollRef} className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2.5">
        {messages.length === 0 && !loading && (
          <div className="text-[12px] leading-relaxed text-muted">
            <p>Ask about your portfolio or tell me what to trade.</p>
            <ul className="mt-2 flex flex-col gap-1 text-faint">
              <li>&ldquo;How concentrated is my portfolio?&rdquo;</li>
              <li>&ldquo;Buy 5 shares of NVDA&rdquo;</li>
              <li>&ldquo;Add PYPL to my watchlist&rdquo;</li>
            </ul>
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        {loading && (
          <div
            data-testid="chat-loading"
            role="status"
            aria-label="FinAlly is thinking"
            className="typing flex gap-1 self-start rounded-md border border-line bg-raised px-3 py-2 text-accent"
          >
            <span>●</span>
            <span>●</span>
            <span>●</span>
          </div>
        )}
      </div>
      <form onSubmit={submit} className="flex shrink-0 gap-1.5 border-t border-line p-2">
        <input
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Message FinAlly"
          aria-label="Message FinAlly"
          className="min-w-0 flex-1 rounded-sm border border-line bg-bg px-2 py-1.5 text-[12.5px] placeholder:text-faint focus:border-blue focus:outline-none"
        />
        <button
          type="submit"
          data-testid="chat-send"
          disabled={loading || !input.trim()}
          className="rounded-sm bg-purple px-3 text-[12px] font-semibold text-white hover:bg-purple-hi disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </aside>
  );
}
