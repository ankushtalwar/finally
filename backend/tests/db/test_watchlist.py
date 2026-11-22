"""Watchlist CRUD."""


def test_add_new_ticker_appends(db):
    assert db.add_to_watchlist("PYPL") is True
    assert db.get_watchlist()[-1] == "PYPL"


def test_add_normalizes(db):
    assert db.add_to_watchlist("  pypl ") is True
    assert "PYPL" in db.get_watchlist()


def test_add_duplicate_returns_false(db):
    assert db.add_to_watchlist("AAPL") is False
    assert db.add_to_watchlist("aapl") is False
    assert db.get_watchlist().count("AAPL") == 1


def test_remove(db):
    assert db.remove_from_watchlist("tsla") is True
    assert "TSLA" not in db.get_watchlist()
    assert db.remove_from_watchlist("TSLA") is False


def test_remove_absent(db):
    assert db.remove_from_watchlist("ZZZZ") is False


def test_order_by_added_at(db):
    for t in ("BBB", "AAA", "CCC"):
        db.add_to_watchlist(t)
    assert db.get_watchlist()[-3:] == ["BBB", "AAA", "CCC"]


def test_readd_after_remove(db):
    db.remove_from_watchlist("AAPL")
    assert db.add_to_watchlist("AAPL") is True
    assert db.get_watchlist()[-1] == "AAPL"


def test_users_are_isolated(db):
    assert db.get_watchlist(user_id="other") == []
    assert db.add_to_watchlist("AAPL", user_id="other") is True
    assert db.get_watchlist(user_id="other") == ["AAPL"]
    assert len(db.get_watchlist()) == 10
