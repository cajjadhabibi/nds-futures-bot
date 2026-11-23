"""Bybit demo booking: missing closed PnL is retried and later backfilled. No network."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nds_engine.bybit_demo as bd


def make(monkeypatch, tmp_path, answers):
    monkeypatch.setattr(bd, "STATE_PATH", str(tmp_path / "exec_state.json"))
    ex = bd.BybitDemoExecutor()
    calls = []

    def fake_req(method, path, params=None):
        calls.append(path)
        return answers.pop(0) if answers else {"retCode": 0, "result": {"list": []}}

    ex._req = fake_req
    return ex, calls


def closed_pos(**kw):
    p = {"bybit_symbol": "QNTUSDT", "direction": "SHORT", "qty": "0.19", "fill": 157.37, "tick": "0.01",
         "opened_ms": 1, "status": "closed", "close_why": "exchange (PnL not found)", "pnl": 0,
         "closing_since": int(time.time()) - 3600}
    p.update(kw)
    return p


def row(side, entry, pnl):
    return {"side": side, "avgEntryPrice": str(entry), "closedPnl": str(pnl)}


def test_backfill_books_only_this_position(monkeypatch, tmp_path):
    ans = [{"retCode": 0, "result": {"list": [row("Buy", 157.37, -0.7643), row("Buy", 162.04, 0.5)]}}]
    ex, _ = make(monkeypatch, tmp_path, ans)
    ex.state.update(realized_pnl=-5.83, positions={"Q": closed_pos()})
    ex._sync({})
    p = ex.state["positions"]["Q"]
    assert p["pnl"] == -0.7643
    assert "backfilled" in p["close_why"]
    assert abs(ex.state["realized_pnl"] - (-6.5943)) < 1e-9
    ex._sync({})                                   # booked once only
    assert abs(ex.state["realized_pnl"] - (-6.5943)) < 1e-9


def test_backfill_waits_when_still_missing(monkeypatch, tmp_path):
    ex, calls = make(monkeypatch, tmp_path, [{"retCode": 10006, "retMsg": "rate limit"}])
    ex.state.update(realized_pnl=-1.0, positions={"Q": closed_pos()})
    ex._sync({})
    assert ex.state["positions"]["Q"]["pnl"] == 0 and ex.state["realized_pnl"] == -1.0
    assert calls == ["/v5/position/closed-pnl"]


def test_old_missing_pnl_not_rechecked(monkeypatch, tmp_path):
    ex, calls = make(monkeypatch, tmp_path, [])
    ex.state["positions"] = {"Q": closed_pos(closing_since=int(time.time()) - 8 * 86400)}
    ex._sync({})
    assert calls == []


def test_api_error_keeps_position_closing(monkeypatch, tmp_path):
    ex, _ = make(monkeypatch, tmp_path, [{"retCode": 10006, "retMsg": "rate limit"}])
    p = closed_pos(status="closing", close_why="exchange", closing_since=int(time.time()))
    ex.state["positions"] = {"Q": p}
    assert ex._try_book("Q", p) is False
    assert p["status"] == "closing"
