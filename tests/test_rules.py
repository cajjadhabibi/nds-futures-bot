"""Unit tests for the NDS bot's trade rules and reports. No network, no Telegram."""
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nds_engine.nds_telegram_bot as bot


class SilentNotifier(bot.NDSTelegramNotifier):
    def __init__(self):
        self.sent = []

    def send_message(self, text, *a, **k):
        self.sent.append(text)
        return True


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "MISSED_PATH", str(tmp_path / "missed.json"))
    monkeypatch.setattr(bot, "VOIDED_PATH", str(tmp_path / "voided.json"))
    return bot.PositionTracker(SilentNotifier(), db_path=str(tmp_path / "trades.json"))


def long_setup(entry=100.0, sl=99.0):
    r = entry - sl
    return {"symbol": "ETHUSD", "direction": "LONG", "entry": entry, "sl": sl,
            "tp1": entry + 1.5 * r, "tp2": entry + 2.5 * r, "tp3": entry + 3.5 * r, "tp4": entry + 4.5 * r,
            "rr": 3.5, "timestamp": "2026-11-22 10:00:00"}


def test_net_pnl_includes_fees():
    # 30$ notional, flat exit -> only the two fees are lost
    assert bot.net_pnl("LONG", 100, 100) == pytest.approx(-0.03)
    assert bot.net_pnl("SHORT", 100, 99) == pytest.approx(0.3 - 0.0005 * (30 + 29.7))


def test_trade_outcome():
    t = dict(long_setup(), tp3_hit=True, is_closed=True)
    assert bot.trade_outcome(t)[0] == "TP3"
    t = dict(long_setup(), sl_hit=True, is_closed=True)
    assert bot.trade_outcome(t) == ("SL", pytest.approx(bot.net_pnl("LONG", 100, 99)))
    t = dict(long_setup(), tp1_hit=True, is_closed=True, close_reason="RISK_FREE")
    assert bot.trade_outcome(t)[0] == "RF"


def test_drift_against_setup_is_rejected():
    assert bot.entry_reject_reason(long_setup(), 99.9) == "PRICE_AGAINST_SETUP"
    s = dict(long_setup(), direction="SHORT", sl=101.0, tp1=98.5, tp2=97.5, tp3=96.5)
    assert bot.entry_reject_reason(s, 100.1) == "PRICE_AGAINST_SETUP"


def test_tp1_too_close_is_rejected():
    # live 101.2: risk 2.2, TP1 101.5 is 0.3 away = 0.14R < 0.5R
    assert bot.entry_reject_reason(long_setup(), 101.2) == "TP1_TOO_CLOSE"
    assert bot.entry_reject_reason(long_setup(), 100.0) is None


def test_blocked_symbol(monkeypatch):
    monkeypatch.setattr(bot, "BLOCKED_SYMBOLS", {"ETHUSD"})
    assert bot.entry_reject_reason(long_setup(), 100.0) == "SYMBOL_BLOCKED"


def test_min_sl_widens_tight_stop_only():
    tight = bot.apply_min_sl(long_setup(100.0, 99.9))          # 0.1% stop
    assert tight["sl"] == pytest.approx(99.5) and tight["setup_sl"] == 99.9
    wide = bot.apply_min_sl(long_setup(100.0, 99.0))           # 1% stop stays
    assert wide["sl"] == 99.0
    short = bot.apply_min_sl(dict(long_setup(100.0, 99.9), direction="SHORT", sl=100.1,
                                  tp1=99.85, tp2=99.75, tp3=99.65))
    assert short["sl"] == pytest.approx(100.5)


def test_rebase_keeps_levels():
    s = bot.rebase_to_live(long_setup(), 100.2)
    assert s["entry"] == 100.2 and s["setup_entry"] == 100.0 and s["sl"] == 99.0


def test_targets_in_order():
    assert bot.targets_in_order(long_setup())
    assert not bot.targets_in_order(dict(long_setup(), tp1=99.5))


def _candles(rows, start):
    idx = pd.to_datetime([start + i * 300 for i in range(len(rows))], unit="s")
    return pd.DataFrame(rows, columns=["High", "Low"], index=idx)


def test_risk_free_not_closed_in_same_check_as_tp1(tracker):
    assert tracker.add_trade("ETH_A", long_setup())
    t = tracker.trades["ETH_A"]
    start = t["created_ts"] - t["created_ts"] % 300
    # one candle that touches TP1 (101.5) and also dips below entry
    tracker.check_trade("ETHUSD", 0, 0, 100.5, candles=_candles([[101.6, 99.95]], start))
    assert t["tp1_hit"] and t["is_risk_free"] and not t["is_closed"]


def test_sl_and_daily_limit(tracker, monkeypatch):
    monkeypatch.setattr(bot, "DAILY_LOSS_LIMIT_USD", 0.25)
    assert tracker.add_trade("ETH_B", long_setup())
    t = tracker.trades["ETH_B"]
    t["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    start = t["created_ts"] - t["created_ts"] % 300
    tracker.check_trade("ETHUSD", 0, 0, 98.9, candles=_candles([[100.1, 98.9]], start))
    assert t["sl_hit"] and t["close_reason"] == "SL_HIT"
    assert tracker.daily_net() < -0.25
    assert tracker.daily_limit_reason() == "DAILY_LOSS_LIMIT"


def test_duplicate_symbol_blocked(tracker):
    assert tracker.add_trade("ETH_C", long_setup())
    assert not tracker.add_trade("ETH_D", long_setup())


def test_reports_fit_telegram(tracker):
    now = datetime.now(timezone.utc)
    for i in range(400):
        s = dict(long_setup(), symbol=f"C{i % 30}USD")
        tracker.trades[f"T{i}"] = dict(s, id=f"T{i}", is_closed=True, tp3_hit=i % 3 == 0,
                                       sl_hit=i % 3 == 1, tp1_hit=i % 3 != 1,
                                       created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                                       created_ts=time.time())
    for report in (tracker.get_pnl_report(), tracker.get_journal_report(), tracker.get_summary_report()):
        assert 0 < len(report) <= 4096
    j = tracker.get_journal_report()
    assert "8.61" not in j and "Profit Factor" in j


def test_empty_reports(tracker):
    assert tracker.get_pnl_report() and tracker.get_journal_report() and tracker.get_summary_report()
