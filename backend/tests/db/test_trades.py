"""apply_trade: validation, cash/position updates, atomicity, concurrency."""

import threading

import pytest

from app.db import QTY_EPSILON, TradeRejected


def test_buy_creates_position_and_debits_cash(db):
    trade = db.apply_trade("aapl", "buy", 10, 190.0)
    assert set(trade) == {"id", "ticker", "side", "quantity", "price", "executed_at"}
    assert trade["ticker"] == "AAPL"
    assert trade["side"] == "buy"
    assert trade["quantity"] == 10
    assert trade["price"] == 190.0
    assert db.get_cash() == pytest.approx(8100.0)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 10
    assert pos["avg_cost"] == 190.0
    assert set(pos) == {"ticker", "quantity", "avg_cost", "updated_at"}


def test_buy_weighted_avg_cost(db):
    db.apply_trade("AAPL", "buy", 10, 100.0)
    db.apply_trade("AAPL", "buy", 30, 200.0)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 40
    assert pos["avg_cost"] == pytest.approx((10 * 100 + 30 * 200) / 40)
    assert db.get_cash() == pytest.approx(10000 - 1000 - 6000)


def test_sell_keeps_avg_cost_and_credits_cash(db):
    db.apply_trade("AAPL", "buy", 10, 100.0)
    db.apply_trade("AAPL", "sell", 4, 150.0)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == pytest.approx(6)
    assert pos["avg_cost"] == 100.0
    assert db.get_cash() == pytest.approx(10000 - 1000 + 600)


def test_sell_at_loss(db):
    db.apply_trade("AAPL", "buy", 10, 100.0)
    db.apply_trade("AAPL", "sell", 10, 50.0)
    assert db.get_cash() == pytest.approx(9500.0)
    assert db.get_position("AAPL") is None


def test_full_close_deletes_row(db):
    db.apply_trade("TSLA", "buy", 5, 200.0)
    db.apply_trade("TSLA", "sell", 5, 210.0)
    assert db.get_position("TSLA") is None
    assert db.get_positions() == []


def test_fractional_epsilon_residue_closes_position(db):
    db.apply_trade("AAPL", "buy", 0.1, 100.0)
    db.apply_trade("AAPL", "buy", 0.2, 100.0)  # 0.30000000000000004 held
    db.apply_trade("AAPL", "sell", 0.3, 100.0)
    assert db.get_position("AAPL") is None


def test_sell_slightly_more_than_held_within_epsilon(db):
    db.apply_trade("AAPL", "buy", 1, 100.0)
    db.apply_trade("AAPL", "sell", 1 + QTY_EPSILON / 2, 100.0)
    assert db.get_position("AAPL") is None


def test_fractional_shares(db):
    db.apply_trade("NVDA", "buy", 0.5, 800.0)
    assert db.get_position("NVDA")["quantity"] == 0.5
    assert db.get_cash() == pytest.approx(9600.0)


def test_buy_exact_cash(db):
    db.apply_trade("AAPL", "buy", 100, 100.0)
    assert db.get_cash() == 0.0


def test_insufficient_cash(db):
    db.apply_trade("AAPL", "buy", 95, 100.0)  # cash left: 500
    with pytest.raises(TradeRejected) as exc:
        db.apply_trade("MSFT", "buy", 10, 195.02)
    assert exc.value.message == "Insufficient cash: need $1,950.20, have $500.00"
    assert str(exc.value) == exc.value.message


def test_insufficient_shares(db):
    db.apply_trade("AAPL", "buy", 5, 100.0)
    with pytest.raises(TradeRejected) as exc:
        db.apply_trade("AAPL", "sell", 10, 100.0)
    assert exc.value.message.startswith("Insufficient shares:")
    assert "AAPL" in exc.value.message
    assert "10" in exc.value.message and "5" in exc.value.message


def test_sell_not_held(db):
    with pytest.raises(TradeRejected) as exc:
        db.apply_trade("AAPL", "sell", 1, 100.0)
    assert exc.value.message.startswith("Insufficient shares:")


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"ticker": "AAPL", "side": "buy", "quantity": 0, "price": 1.0}, "Quantity"),
        ({"ticker": "AAPL", "side": "buy", "quantity": -1, "price": 1.0}, "Quantity"),
        ({"ticker": "AAPL", "side": "buy", "quantity": float("nan"), "price": 1.0}, "Quantity"),
        ({"ticker": "AAPL", "side": "buy", "quantity": float("inf"), "price": 1.0}, "Quantity"),
        ({"ticker": "AAPL", "side": "buy", "quantity": 1, "price": 0.0}, "price"),
        ({"ticker": "AAPL", "side": "hold", "quantity": 1, "price": 1.0}, "Side"),
        ({"ticker": "  ", "side": "buy", "quantity": 1, "price": 1.0}, "Ticker"),
    ],
)
def test_invalid_inputs_rejected(db, kwargs, fragment):
    with pytest.raises(TradeRejected) as exc:
        db.apply_trade(**kwargs)
    assert fragment in exc.value.message
    assert db.get_trades() == []
    assert db.get_cash() == 10000.0


def test_quantity_zero_message(db):
    with pytest.raises(TradeRejected) as exc:
        db.apply_trade("AAPL", "buy", 0, 100.0)
    assert exc.value.message == "Quantity must be greater than 0"


def test_side_case_insensitive(db):
    trade = db.apply_trade("AAPL", "BUY", 1, 10.0)
    assert trade["side"] == "buy"


def test_rejection_writes_nothing(db):
    db.apply_trade("AAPL", "buy", 10, 100.0)
    before = (db.get_cash(), db.get_positions(), db.get_trades())
    with pytest.raises(TradeRejected):
        db.apply_trade("AAPL", "buy", 1000, 100.0)
    with pytest.raises(TradeRejected):
        db.apply_trade("AAPL", "sell", 11, 100.0)
    assert (db.get_cash(), db.get_positions(), db.get_trades()) == before


def test_unknown_user_rejected(db):
    with pytest.raises(TradeRejected):
        db.apply_trade("AAPL", "buy", 1, 1.0, user_id="nobody")


def test_trades_log_newest_first_and_limit(db):
    db.apply_trade("AAPL", "buy", 1, 10.0)
    db.apply_trade("MSFT", "buy", 1, 10.0)
    db.apply_trade("AAPL", "sell", 1, 11.0)
    trades = db.get_trades()
    assert [(t["ticker"], t["side"]) for t in trades] == [
        ("AAPL", "sell"),
        ("MSFT", "buy"),
        ("AAPL", "buy"),
    ]
    assert len(db.get_trades(limit=2)) == 2
    assert db.get_trades(limit=2)[0]["side"] == "sell"


def test_positions_list(db):
    db.apply_trade("MSFT", "buy", 1, 10.0)
    db.apply_trade("AAPL", "buy", 2, 10.0)
    tickers = {p["ticker"] for p in db.get_positions()}
    assert tickers == {"AAPL", "MSFT"}
    assert db.get_position("msft")["quantity"] == 1


def test_concurrent_buys_are_serialized(db):
    # 50 threads each buy $100; total spend must be exact and nothing lost.
    errors = []

    def worker():
        try:
            db.apply_trade("AAPL", "buy", 1, 100.0)
        except Exception as e:  # pragma: no cover - surfaced via assertion
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert db.get_position("AAPL")["quantity"] == 50
    assert db.get_cash() == pytest.approx(5000.0)
    assert len(db.get_trades(limit=1000)) == 50


def test_concurrent_overspend_is_prevented(db):
    # 20 threads each try to spend $1,000 from $10,000: exactly 10 succeed.
    results = []
    lock = threading.Lock()

    def worker():
        try:
            db.apply_trade("AAPL", "buy", 10, 100.0)
            ok = True
        except TradeRejected:
            ok = False
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 10
    assert db.get_cash() == pytest.approx(0.0)
