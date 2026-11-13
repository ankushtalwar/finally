"""FinAlly Rich Terminal Demo.

Run with:  uv run --extra dev python rich_terminal_demo.py [--duration 60] [--threshold 0.15]

Live dashboard driven by the GBM simulator: per-ticker sparklines, colored
direction arrows, a market-breadth bar, and a scrolling event log of notable
ticks, session highs/lows and large day moves.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import deque

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource

TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]
SPARK = "▁▂▃▄▅▆▇█"
ARROWS = {"up": ("▲", "green"), "down": ("▼", "red"), "flat": ("•", "bright_black")}
HISTORY = 36


def sparkline(values: list[float]) -> Text:
    """Unicode sparkline; each bar is colored by its move vs the previous point."""
    text = Text()
    if len(values) < 2:
        return text
    lo, hi = min(values), max(values)
    spread = hi - lo
    for i, v in enumerate(values):
        idx = 3 if spread == 0 else int((v - lo) / spread * (len(SPARK) - 1))
        prev = values[i - 1] if i else v
        style = "green" if v > prev else "red" if v < prev else "bright_black"
        text.append(SPARK[idx], style=style)
    return text


def build_table(cache: PriceCache, history: dict[str, deque], flash: dict[str, int]) -> Table:
    table = Table(expand=True, border_style="bright_black", header_style="bold bright_white")
    table.add_column("Ticker", style="bold bright_white", width=7)
    table.add_column("Price", justify="right", width=10)
    table.add_column("", width=2)
    table.add_column("Tick", justify="right", width=8)
    table.add_column("Day %", justify="right", width=8)
    table.add_column("Trend", no_wrap=True, ratio=1)
    for t in TICKERS:
        u = cache.get(t)
        if u is None:
            table.add_row(t, "---", "", "", "", "")
            continue
        arrow, color = ARROWS[u.direction]
        # Flashed rows get a reverse-video price for a few frames
        price_style = f"bold reverse {color}" if flash.get(t, 0) > 0 else f"bold {color}"
        day_color = "green" if u.day_change_percent > 0 else "red" if u.day_change_percent < 0 else "bright_black"
        table.add_row(
            t,
            Text(f"${u.price:,.2f}", style=price_style),
            Text(arrow, style=f"bold {color}"),
            Text(f"{u.change:+.2f}", style=color),
            Text(f"{u.day_change_percent:+.2f}%", style=day_color),
            sparkline(list(history[t])),
        )
    return table


def breadth_bar(cache: PriceCache, width: int = 30) -> Text:
    ups = sum(1 for u in cache.get_all().values() if u.day_change_percent > 0)
    total = max(len(cache), 1)
    filled = round(ups / total * width)
    bar = Text("Breadth ", style="bright_white")
    bar.append("█" * filled, style="green")
    bar.append("█" * (width - filled), style="red")
    bar.append(f"  {ups} up / {total - ups} down", style="bright_black")
    return bar


def build_dashboard(cache, history, flash, events, start, duration) -> Layout:
    elapsed = time.time() - start
    layout = Layout()
    layout.split_column(Layout(name="header", size=3), Layout(name="body"), Layout(name="log", size=12))
    header = Text.assemble(
        ("  FinAlly ", "bold #ecad0a"),
        ("Rich Terminal Demo", "bold bright_white"),
        ("  |  ", "bright_black"),
        (f"{elapsed:5.1f}s / {duration}s", "#209dd7"),
        ("  |  ", "bright_black"),
        ("Ctrl+C to exit", "italic bright_black"),
    )
    layout["header"].update(Panel(header, border_style="#ecad0a"))
    layout["body"].update(
        Panel(
            Group(build_table(cache, history, flash), Text(), breadth_bar(cache)),
            title="[bold]Live Prices[/]",
            border_style="bright_black",
        )
    )
    log = Text()
    for line in events:
        log.append_text(line)
        log.append("\n")
    if not events:
        log.append("Waiting for notable events...", style="italic bright_black")
    layout["log"].update(Panel(log, title="[bold #ecad0a]Event Log[/]", border_style="bright_black"))
    return layout


def event_line(kind: str, ticker: str, detail: str, color: str, arrow: str) -> Text:
    return Text.assemble(
        (time.strftime("%H:%M:%S"), "bright_black"),
        "  ",
        (f"{arrow} {ticker:<5}", f"bold {color}"),
        (f"{kind:<8}", color),
        (detail, "bright_white"),
    )


async def run(duration: int, threshold: float) -> None:
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.5)
    history = {t: deque(maxlen=HISTORY) for t in TICKERS}
    flash: dict[str, int] = {}
    events: deque[Text] = deque(maxlen=9)
    hi: dict[str, float] = {}
    lo: dict[str, float] = {}

    await source.start(TICKERS)
    start = time.time()
    last_version = -1
    try:
        with Live(
            build_dashboard(cache, history, flash, events, start, duration),
            refresh_per_second=8,
            screen=True,
        ) as live:
            while time.time() - start < duration:
                await asyncio.sleep(0.125)
                for t in list(flash):
                    flash[t] -= 1
                if cache.version != last_version:
                    last_version = cache.version
                    for t in TICKERS:
                        u = cache.get(t)
                        if u is None:
                            continue
                        history[t].append(u.price)
                        arrow, color = ARROWS[u.direction]
                        if u.direction != "flat":
                            flash[t] = 2
                        if abs(u.change_percent) >= threshold:
                            events.appendleft(
                                event_line("MOVE", t, f"{u.change_percent:+.2f}% tick -> ${u.price:,.2f}", color, arrow)
                            )
                        if abs(u.day_change_percent) >= 2.0 and abs(u.change_percent) >= threshold:
                            events.appendleft(
                                event_line("SHOCK", t, f"{u.day_change_percent:+.2f}% on the day", "#ecad0a", "!")
                            )
                        # Session extremes (skip the first sample)
                        if t in hi and u.price > hi[t]:
                            events.appendleft(event_line("HIGH", t, f"new session high ${u.price:,.2f}", "green", "▲"))
                        if t in lo and u.price < lo[t]:
                            events.appendleft(event_line("LOW", t, f"new session low ${u.price:,.2f}", "red", "▼"))
                        hi[t] = max(hi.get(t, u.price), u.price)
                        lo[t] = min(lo.get(t, u.price), u.price)
                live.update(build_dashboard(cache, history, flash, events, start, duration))
    except KeyboardInterrupt:
        pass
    finally:
        await source.stop()
    Console().print("[bold #ecad0a]FinAlly[/] demo finished.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--duration", type=int, default=60, help="seconds to run")
    p.add_argument("--threshold", type=float, default=0.15, help="%% tick move that is logged")
    a = p.parse_args()
    asyncio.run(run(a.duration, a.threshold))
