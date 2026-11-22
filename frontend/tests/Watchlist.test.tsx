import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Watchlist, { type WatchlistRowData } from "@/components/Watchlist";

const rows: WatchlistRowData[] = [
  { ticker: "AAPL", price: 190.12, dayChangePercent: 0.41 },
  { ticker: "TSLA", price: 250.0, dayChangePercent: -1.2 },
];

function setup(overrides: Partial<React.ComponentProps<typeof Watchlist>> = {}) {
  const props = {
    rows,
    history: { AAPL: [{ time: 1, value: 189 }, { time: 2, value: 190.12 }] },
    selected: "AAPL",
    onSelect: vi.fn(),
    onAdd: vi.fn().mockResolvedValue(undefined),
    onRemove: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  const utils = render(<Watchlist {...props} />);
  return { ...utils, props };
}

describe("Watchlist", () => {
  it("renders a row per ticker with formatted price and day change", () => {
    setup();
    expect(screen.getByTestId("watchlist")).toBeInTheDocument();
    expect(screen.getByTestId("watchlist-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("watchlist-price-AAPL")).toHaveTextContent("190.12");
    expect(screen.getByTestId("watchlist-price-TSLA")).toHaveTextContent("250.00");
    expect(screen.getByTestId("watchlist-row-TSLA")).toHaveTextContent("-1.20%");
    expect(screen.getByTestId("watchlist-row-AAPL")).toHaveTextContent("+0.41%");
    expect(screen.getByTestId("watchlist-row-AAPL")).toHaveAttribute("aria-pressed", "true");
  });

  it("flashes green on uptick and red on downtick", () => {
    const { rerender, props } = setup();
    const price = () => screen.getByTestId("watchlist-price-AAPL");
    expect(price().className).not.toMatch(/flash-/);
    rerender(<Watchlist {...props} rows={[{ ...rows[0], price: 191 }, rows[1]]} />);
    expect(price().className).toMatch(/flash-up/);
    rerender(<Watchlist {...props} rows={[{ ...rows[0], price: 189 }, rows[1]]} />);
    expect(price().className).toMatch(/flash-down/);
    expect(price().className).not.toMatch(/flash-up/);
    // the TSLA row didn't change, so it does not flash
    expect(screen.getByTestId("watchlist-price-TSLA").className).not.toMatch(/flash-/);
  });

  it("alternates the animation class so consecutive upticks restart the flash", () => {
    const { rerender, props } = setup();
    const price = () => screen.getByTestId("watchlist-price-AAPL");
    rerender(<Watchlist {...props} rows={[{ ...rows[0], price: 191 }, rows[1]]} />);
    const first = price().className;
    rerender(<Watchlist {...props} rows={[{ ...rows[0], price: 192 }, rows[1]]} />);
    expect(price().className).toMatch(/flash-up/);
    expect(price().className).not.toBe(first);
  });

  it("selects a ticker on click", async () => {
    const { props } = setup();
    await userEvent.click(screen.getByTestId("watchlist-row-TSLA"));
    expect(props.onSelect).toHaveBeenCalledWith("TSLA");
  });

  it("adds a ticker (uppercased) and clears the input", async () => {
    const { props } = setup();
    const input = screen.getByTestId("watchlist-add-input") as HTMLInputElement;
    await userEvent.type(input, "pypl");
    await userEvent.click(screen.getByTestId("watchlist-add-button"));
    expect(props.onAdd).toHaveBeenCalledWith("PYPL");
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("shows the backend error when adding fails", async () => {
    setup({ onAdd: vi.fn().mockRejectedValue(new Error("PYPL is already on your watchlist")) });
    await userEvent.type(screen.getByTestId("watchlist-add-input"), "PYPL{enter}");
    expect(await screen.findByTestId("watchlist-error")).toHaveTextContent("already on your watchlist");
  });

  it("removes a ticker without selecting it", async () => {
    const { props } = setup();
    await userEvent.click(screen.getByTestId("watchlist-remove-TSLA"));
    expect(props.onRemove).toHaveBeenCalledWith("TSLA");
    expect(props.onSelect).not.toHaveBeenCalled();
  });
});
