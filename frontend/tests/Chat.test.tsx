import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChatPanel, { tradeActionText, watchlistActionText } from "@/components/ChatPanel";
import type { ChatMessage } from "@/lib/types";

const reply: ChatMessage = {
  id: "a1",
  role: "assistant",
  content: "Done — bought 5 AAPL at $190.12.",
  created_at: "2026-11-13T10:00:00+00:00",
  actions: {
    trades: [
      { ticker: "AAPL", side: "buy", quantity: 5, status: "executed", price: 190.12, error: null },
      { ticker: "TSLA", side: "sell", quantity: 3, status: "failed", price: null, error: "Insufficient shares" },
    ],
    watchlist_changes: [{ ticker: "PYPL", action: "add", status: "executed", error: null }],
  },
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("chat action text", () => {
  it("describes executed and failed actions", () => {
    expect(tradeActionText(reply.actions!.trades![0])).toBe("BUY 5 AAPL @ $190.12");
    expect(tradeActionText(reply.actions!.trades![1])).toBe("FAILED SELL 3 TSLA: Insufficient shares");
    expect(watchlistActionText(reply.actions!.watchlist_changes![0])).toBe("WATCHLIST ADD PYPL");
  });
});

describe("ChatPanel", () => {
  it("loads history and renders user and assistant bubbles", async () => {
    const history: ChatMessage[] = [
      { id: "u1", role: "user", content: "hi", actions: null, created_at: "" },
      { ...reply, id: "a0" },
    ];
    render(
      <ChatPanel
        loadHistory={() => Promise.resolve(history)}
        send={vi.fn()}
        collapsed={false}
        onToggle={vi.fn()}
      />,
    );
    expect(await screen.findByTestId("chat-message-user")).toHaveTextContent("hi");
    expect(screen.getByTestId("chat-message-assistant")).toHaveTextContent("Done");
    expect(screen.getAllByTestId("chat-action")).toHaveLength(3);
  });

  it("shows a loading indicator while waiting, then the reply with inline actions", async () => {
    const d = deferred<ChatMessage>();
    const send = vi.fn(() => d.promise);
    const onActions = vi.fn();
    render(
      <ChatPanel
        loadHistory={() => Promise.resolve([])}
        send={send}
        onActions={onActions}
        collapsed={false}
        onToggle={vi.fn()}
      />,
    );
    await userEvent.type(screen.getByTestId("chat-input"), "buy 5 AAPL{enter}");
    expect(send).toHaveBeenCalledWith("buy 5 AAPL");
    expect(send).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();
    expect(screen.getByTestId("chat-message-user")).toHaveTextContent("buy 5 AAPL");
    d.resolve(reply);
    await waitFor(() => expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument());
    const actions = screen.getAllByTestId("chat-action");
    expect(actions.map((a) => a.textContent)).toEqual([
      "WATCHLIST ADD PYPL",
      "BUY 5 AAPL @ $190.12",
      "FAILED SELL 3 TSLA: Insufficient shares",
    ]);
    expect(onActions).toHaveBeenCalledWith(reply.actions);
  });

  it("sends via the button and shows a friendly error on network failure", async () => {
    const send = vi.fn().mockRejectedValue(new Error("Request failed (500)"));
    render(
      <ChatPanel loadHistory={() => Promise.resolve([])} send={send} collapsed={false} onToggle={vi.fn()} />,
    );
    await userEvent.type(screen.getByTestId("chat-input"), "hello");
    await userEvent.click(screen.getByTestId("chat-send"));
    expect(await screen.findByTestId("chat-message-assistant")).toHaveTextContent("Couldn't reach FinAlly");
  });

  it("collapses to a rail and can be reopened", async () => {
    const onToggle = vi.fn();
    render(
      <ChatPanel loadHistory={() => Promise.resolve([])} send={vi.fn()} collapsed onToggle={onToggle} />,
    );
    expect(screen.getByTestId("chat-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open AI assistant" }));
    expect(onToggle).toHaveBeenCalled();
  });
});
