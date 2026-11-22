"""Portfolio snapshots and chat messages."""

import pytest


def test_snapshots_oldest_to_newest(db):
    for v in (10000.0, 10050.5, 9990.0):
        db.record_snapshot(v)
    snaps = db.get_snapshots()
    assert [s["total_value"] for s in snaps] == [10000.0, 10050.5, 9990.0]
    assert set(snaps[0]) == {"total_value", "recorded_at"}
    assert snaps[0]["recorded_at"] <= snaps[-1]["recorded_at"]


def test_snapshots_limit_returns_newest(db):
    for v in range(10):
        db.record_snapshot(float(v))
    snaps = db.get_snapshots(limit=3)
    assert [s["total_value"] for s in snaps] == [7.0, 8.0, 9.0]


def test_snapshots_iso_utc_timestamp(db):
    db.record_snapshot(1.0)
    ts = db.get_snapshots()[0]["recorded_at"]
    assert ts.endswith("+00:00") and "T" in ts


def test_snapshots_per_user(db):
    db.record_snapshot(1.0, user_id="other")
    assert db.get_snapshots() == []
    assert len(db.get_snapshots(user_id="other")) == 1


def test_chat_round_trip(db):
    actions = {
        "trades": [
            {
                "ticker": "AAPL",
                "side": "buy",
                "quantity": 5,
                "status": "executed",
                "price": 190.12,
                "error": None,
            }
        ],
        "watchlist_changes": [{"ticker": "PYPL", "action": "add", "status": "executed", "error": None}],
    }
    u = db.add_chat_message("user", "buy 5 AAPL")
    a = db.add_chat_message("assistant", "Done.", actions)
    assert set(u) == {"id", "role", "content", "actions", "created_at"}
    assert u["actions"] is None
    msgs = db.get_chat_messages()
    assert [m["id"] for m in msgs] == [u["id"], a["id"]]
    assert msgs[0]["actions"] is None
    assert msgs[1]["actions"] == actions
    assert msgs[1]["content"] == "Done."
    assert msgs[1]["role"] == "assistant"


def test_chat_limit_newest_returned_oldest_first(db):
    for i in range(30):
        db.add_chat_message("user" if i % 2 == 0 else "assistant", f"m{i}")
    msgs = db.get_chat_messages()
    assert len(msgs) == 20
    assert [m["content"] for m in msgs] == [f"m{i}" for i in range(10, 30)]
    assert [m["content"] for m in db.get_chat_messages(limit=2)] == ["m28", "m29"]


def test_chat_empty_actions_dict_preserved(db):
    db.add_chat_message("assistant", "hi", {})
    assert db.get_chat_messages()[0]["actions"] == {}


def test_chat_invalid_role(db):
    with pytest.raises(ValueError):
        db.add_chat_message("system", "nope")
