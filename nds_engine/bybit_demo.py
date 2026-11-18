"""
Bybit DEMO execution for NDS signals, capped at a virtual $100 budget.

Safety:
  * Talks ONLY to https://api-demo.bybit.com (Bybit Demo Trading, virtual funds). There is no live mode.
  * Uses its own virtual budget (default $100) no matter how much demo balance the account has.
  * Margin per trade = MARGIN_PCT of the starting budget (10% -> $10), leverage 3x -> $30 position.
  * At most one position per symbol; new trades only while open margin + $10 <= budget equity.

Flow (mirrors the paper tracker so both results can be compared):
  * new signal          -> market order with stop-loss = SL and take-profit = TP3 on the exchange
  * TP1 touched         -> stop-loss moved to the fill price (break-even)
  * paper trade closed  -> any remaining exchange position is closed at market
  * exchange closed it  -> realized PnL is read from Bybit and added to the budget

Environment: BYBIT_DEMO_API_KEY, BYBIT_DEMO_API_SECRET (required), DEMO_BUDGET (100),
             DEMO_MARGIN_PCT (10), DEMO_LEVERAGE (3).
"""
import hashlib
import hmac
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

BASE_URL = "https://api-demo.bybit.com"   # demo only, by design
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "exec_state.json")

API_KEY = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
BUDGET = float(os.environ.get("DEMO_BUDGET", "100"))
MARGIN_PCT = float(os.environ.get("DEMO_MARGIN_PCT", "10"))
LEVERAGE = int(float(os.environ.get("DEMO_LEVERAGE", "3")))

# Kraken symbol (bot) -> Bybit linear symbol. Coins missing here are skipped.
SYMBOL_MAP = {
    "PEPEUSD": "1000PEPEUSDT",
    "SHIBUSD": "SHIB1000USDT",
}
PRICE_MULT = {"1000PEPEUSDT": 1000.0, "SHIB1000USDT": 1000.0}


def _log(msg):
    print(f"[bybit_demo] {msg}", flush=True)


def _fmt(x, step):
    """Round x down to a multiple of step and format without float noise."""
    step_d = len(step.split(".")[1].rstrip("0")) if "." in step else 0
    s = float(step)
    v = math.floor(x / s + 1e-9) * s
    return f"{v:.{step_d}f}"


def _fmt_price(x, tick):
    d = len(tick.split(".")[1].rstrip("0")) if "." in tick else 0
    t = float(tick)
    return f"{round(round(x / t) * t, d):.{d}f}"


class BybitDemoExecutor:
    def __init__(self, notify=None):
        self.notify = notify or (lambda text: None)
        self.lock = threading.Lock()
        self.instruments = {}
        self.state = self._load()
        if self.configured():
            threading.Thread(target=self.probe, daemon=True).start()

    # ---------- infra ----------
    def configured(self):
        return bool(API_KEY and API_SECRET)

    def active(self):
        return self.configured() and self.state.get("enabled", True)

    def _load(self):
        st = {"enabled": True, "budget": BUDGET, "realized_pnl": 0.0, "positions": {}, "log": []}
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    st.update(json.load(f))
            except Exception as e:
                _log(f"state load error: {e}")
        return st

    def _save(self):
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            _log(f"state save error: {e}")

    def _event(self, text):
        self.state["log"] = (self.state.get("log", []) + [f"{datetime.now():%m-%d %H:%M} {text}"])[-40:]

    def _req(self, method, path, params=None):
        params = params or {}
        ts = str(int(time.time() * 1000))
        rw = "10000"
        if method == "GET":
            q = urllib.parse.urlencode(params)
            payload, url, body = q, BASE_URL + path + ("?" + q if q else ""), None
        else:
            payload = json.dumps(params, separators=(",", ":"))
            url, body = BASE_URL + path, payload.encode()
        sig = hmac.new(API_SECRET.encode(), (ts + API_KEY + rw + payload).encode(), hashlib.sha256).hexdigest()
        req = urllib.request.Request(url, data=body, method=method, headers={
            "X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sig,
            "X-BAPI-RECV-WINDOW": rw, "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return {"retCode": e.code, "retMsg": e.read().decode()[:200]}
        except Exception as e:
            return {"retCode": -1, "retMsg": str(e)}

    def _instrument(self, sym):
        """Returns (instrument, error). Only a successful empty answer means 'not listed';
        API/network errors are returned (never cached) so the real cause is visible."""
        if sym not in self.instruments:
            r = self._req("GET", "/v5/market/instruments-info", {"category": "linear", "symbol": sym})
            if r.get("retCode") != 0:
                err = f"Bybit API error {r.get('retCode')}: {str(r.get('retMsg', ''))[:150]}"
                _log(f"instrument {sym}: {err}")
                return None, err
            lst = (r.get("result") or {}).get("list") or []
            self.instruments[sym] = lst[0] if lst and lst[0].get("status") == "Trading" else None
        return self.instruments[sym], None

    def probe(self):
        """One connectivity check at startup, written to the server log."""
        r = self._req("GET", "/v5/market/instruments-info", {"category": "linear", "symbol": "ETHUSDT"})
        _log(f"probe {BASE_URL}: retCode={r.get('retCode')} msg={str(r.get('retMsg', ''))[:200]}")

    def _last_price(self, sym):
        r = self._req("GET", "/v5/market/tickers", {"category": "linear", "symbol": sym})
        try:
            return float(r["result"]["list"][0]["lastPrice"])
        except Exception:
            return None

    @staticmethod
    def bybit_symbol(kraken_symbol):
        if kraken_symbol in SYMBOL_MAP:
            return SYMBOL_MAP[kraken_symbol]
        return kraken_symbol[:-3] + "USDT" if kraken_symbol.endswith("USD") else kraken_symbol

    # ---------- budget ----------
    def equity(self):
        return self.state.get("budget", BUDGET) + self.state.get("realized_pnl", 0.0)

    def margin_per_trade(self):
        return self.state.get("budget", BUDGET) * MARGIN_PCT / 100.0

    def open_positions(self):
        """Positions still using budget: open on the exchange, or closed but PnL not booked yet."""
        return {k: p for k, p in self.state["positions"].items() if p.get("status") in ("open", "closing")}

    # ---------- actions ----------
    def on_new_trade(self, trade_id, t):
        """Open the trade on Bybit demo. Returns fill info when a position was opened, else None."""
        if not self.active():
            return None
        with self.lock:
            try:
                return self._open(trade_id, t)
            finally:
                self._save()

    def _skip(self, trade_id, sym, reason):
        self._event(f"SKIP {sym}: {reason}")
        _log(f"skip {trade_id}: {reason}")

    def _open(self, trade_id, t):
        if trade_id in self.state["positions"]:
            return
        sym = self.bybit_symbol(t["symbol"])
        inst, err = self._instrument(sym)
        if err:
            return self._skip(trade_id, sym, err)
        if not inst:
            return self._skip(trade_id, sym, "not listed on Bybit")
        if any(p["bybit_symbol"] == sym for p in self.open_positions().values()):
            return self._skip(trade_id, sym, "position already open on this coin")
        margin = self.margin_per_trade()
        used = len(self.open_positions()) * margin
        if used + margin > self.equity() + 1e-9:
            return self._skip(trade_id, sym, f"budget full (equity {self.equity():.2f}$, in use {used:.2f}$)")

        mult = PRICE_MULT.get(sym, 1.0)
        sl, tp = t["sl"] * mult, t["tp3"] * mult
        price = self._last_price(sym)
        if not price:
            return self._skip(trade_id, sym, "no price")
        long_ = t["direction"] == "LONG"
        if (long_ and not (sl < price < tp)) or (not long_ and not (tp < price < sl)):
            return self._skip(trade_id, sym, f"price {price:g} already outside SL/TP ({sl:g} / {tp:g})")
        tp1 = t["tp1"] * mult
        if (long_ and price >= tp1) or (not long_ and price <= tp1):
            return self._skip(trade_id, sym, f"price {price:g} already past TP1 ({tp1:g})")

        lot = inst["lotSizeFilter"]
        notional = margin * LEVERAGE
        qty = _fmt(notional / price, lot["qtyStep"])
        min_notional = float(lot.get("minNotionalValue", 5))
        if float(qty) < float(lot["minOrderQty"]) or float(qty) * price < min_notional:
            return self._skip(trade_id, sym, f"${notional:.0f} is below Bybit minimum (min qty {lot['minOrderQty']})")

        lev = self._req("POST", "/v5/position/set-leverage", {
            "category": "linear", "symbol": sym, "buyLeverage": str(LEVERAGE), "sellLeverage": str(LEVERAGE)})
        if lev.get("retCode") not in (0, 110043):          # 110043 = leverage not modified
            return self._skip(trade_id, sym, f"set leverage failed: {lev.get('retMsg')}")

        tick = inst["priceFilter"]["tickSize"]
        r = self._req("POST", "/v5/order/create", {
            "category": "linear", "symbol": sym, "side": "Buy" if long_ else "Sell",
            "orderType": "Market", "qty": qty, "positionIdx": 0, "tpslMode": "Full",
            "stopLoss": _fmt_price(sl, tick), "takeProfit": _fmt_price(tp, tick),
            "orderLinkId": ("nds" + hashlib.md5(trade_id.encode()).hexdigest())[:36],
        })
        if r.get("retCode") != 0:
            return self._skip(trade_id, sym, f"order rejected: {r.get('retMsg')}")

        time.sleep(1.0)
        fill = self._position(sym)
        avg = float(fill.get("avgPrice") or price) if fill else price
        self.state["positions"][trade_id] = {
            "bybit_symbol": sym, "direction": t["direction"], "qty": qty, "fill": avg,
            "sl": float(_fmt_price(sl, tick)), "tp": float(_fmt_price(tp, tick)),
            "tp1": t["tp1"] * mult, "tick": tick, "be": False, "status": "open",
            "opened_ms": int(time.time() * 1000) - 5000, "opened_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        }
        self._event(f"OPEN {t['direction']} {sym} qty {qty} @ {avg:g}")
        return {"symbol": sym, "qty": qty, "fill": avg, "notional": notional, "leverage": LEVERAGE,
                "sl": _fmt_price(sl, tick), "tp": _fmt_price(tp, tick)}

    @staticmethod
    def signal_line(info):
        """Line appended to the Telegram signal when the trade was opened on Bybit demo."""
        return (f"\n\n🧪 <b>این معامله در Bybit (حساب دمو) هم باز شد ✅</b>\n"
                f"▫️ <code>{info['symbol']}</code> | ورود {info['fill']:g} | حجم {info['qty']} "
                f"(~{info['notional']:.0f}$، اهرم {info['leverage']}x)\n"
                f"▫️ SL {info['sl']} | TP {info['tp']} (ثبت‌شده روی صرافی)")

    def _position(self, sym):
        r = self._req("GET", "/v5/position/list", {"category": "linear", "symbol": sym})
        for p in r.get("result", {}).get("list", []) if r.get("retCode") == 0 else []:
            if float(p.get("size") or 0) > 0:
                return p
        return None

    def sync(self, paper_trades):
        """Call once per scan cycle: break-even, mirror paper closes, book exchange closes."""
        if not self.configured():
            return
        with self.lock:
            try:
                self._sync(paper_trades)
            except Exception as e:
                _log(f"sync error: {e}")
            finally:
                self._save()

    def _sync(self, paper_trades):
        for tid, p in list(self.state["positions"].items()):
            if p.get("status") == "closing":
                self._try_book(tid, p)
        opens = {k: p for k, p in self.state["positions"].items() if p.get("status") == "open"}
        if not opens:
            return
        r = self._req("GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT"})
        if r.get("retCode") != 0:
            return _log(f"position list failed: {r.get('retMsg')}")
        live = {p["symbol"]: p for p in r["result"]["list"] if float(p.get("size") or 0) > 0}

        for tid, p in opens.items():
            sym, long_ = p["bybit_symbol"], p["direction"] == "LONG"
            lp = live.get(sym)
            paper = paper_trades.get(tid, {})

            if not lp:                                    # closed on the exchange (SL / TP / BE)
                self._book_close(tid, p, "exchange")
                continue

            mark = float(lp.get("markPrice") or 0)
            tp1_hit = paper.get("tp1_hit") or (mark and ((long_ and mark >= p["tp1"]) or (not long_ and mark <= p["tp1"])))
            if paper.get("is_closed") or tid not in paper_trades:   # closed, or voided by the tracker
                side = "Sell" if long_ else "Buy"
                c = self._req("POST", "/v5/order/create", {
                    "category": "linear", "symbol": sym, "side": side, "orderType": "Market",
                    "qty": lp["size"], "reduceOnly": True, "positionIdx": 0})
                if c.get("retCode") == 0:
                    time.sleep(1.0)
                    self._book_close(tid, p, f"paper {paper.get('close_reason', 'voided')}")
                else:
                    _log(f"close {sym} failed: {c.get('retMsg')}")
            elif tp1_hit and not p["be"]:
                be = p["fill"]
                if (long_ and mark > be) or (not long_ and mark < be):
                    s = self._req("POST", "/v5/position/trading-stop", {
                        "category": "linear", "symbol": sym, "tpslMode": "Full", "positionIdx": 0,
                        "stopLoss": _fmt_price(be, p["tick"])})
                    if s.get("retCode") in (0, 34040):          # 34040 = not modified
                        p["be"] = True
                        self._event(f"BE {sym} stop -> {be:g}")
                        self.notify(f"🛡️ <b>Bybit Demo</b> — <code>{sym}</code> ریسک‌فری شد (استاپ روی ورود {be:g})")
                    else:
                        _log(f"BE {sym} failed: {s.get('retMsg')}")

    def _book_close(self, tid, p, why):
        """Bybit publishes closed PnL a few seconds after the close: mark as closing, book when it appears."""
        p.update(status="closing", close_why=why, closing_since=int(time.time()))
        for _ in range(3):
            if self._try_book(tid, p):
                return
            time.sleep(2)

    def _try_book(self, tid, p):
        r = self._req("GET", "/v5/position/closed-pnl", {
            "category": "linear", "symbol": p["bybit_symbol"], "startTime": p["opened_ms"], "limit": 50})
        rows = r.get("result", {}).get("list", []) if r.get("retCode") == 0 else []
        if not rows and time.time() - p.get("closing_since", 0) < 1800:
            return False                                 # not published yet: retry next cycle
        why = p.get("close_why", "")
        pnl = sum(float(x.get("closedPnl") or 0) for x in rows)
        if not rows:
            why += " (PnL not found)"
        p.update(status="closed", pnl=round(pnl, 4), closed_at=f"{datetime.now():%Y-%m-%d %H:%M:%S}", close_why=why)
        self.state["realized_pnl"] = round(self.state.get("realized_pnl", 0.0) + pnl, 4)
        self._event(f"CLOSE {p['bybit_symbol']} {pnl:+.2f}$ ({why})")
        self.notify(
            f"{'✅' if pnl >= 0 else '🛑'} <b>Bybit Demo — پوزیشن بسته شد</b>\n"
            f"<code>{p['bybit_symbol']}</code> {p['direction']} | سود/زیان: <b>{pnl:+.2f}$</b> ({why})\n"
            f"موجودی بودجه: <b>{self.equity():.2f}$</b> از {self.state.get('budget', BUDGET):.0f}$")
        return True

    # ---------- report ----------
    def report(self):
        if not self.configured():
            return "🧪 Bybit Demo وصل نیست (BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET تنظیم نشده)."
        pos = self.state["positions"].values()
        closed = [p for p in pos if p.get("status") == "closed"]
        wins = sum(1 for p in closed if p.get("pnl", 0) > 0)
        net = self.state.get("realized_pnl", 0.0)
        lines = [
            "🧪 <b>Bybit Demo — حساب ۱۰۰ دلاری</b>",
            f"▪️ وضعیت: {'✅ فعال' if self.active() else '⏸️ متوقف'} | اهرم {LEVERAGE}x | مارجین هر پوزیشن {self.margin_per_trade():.0f}$",
            f"▪️ موجودی بودجه: <b>{self.equity():.2f}$</b> ({net:+.2f}$ / {net / self.state.get('budget', BUDGET) * 100:+.1f}%)",
            f"▪️ بسته‌شده: {len(closed)} | سودده: {wins} | زیان‌ده: {len(closed) - wins}",
            f"▪️ پوزیشن باز: {len(self.open_positions())}",
        ]
        for p in list(self.open_positions().values())[:10]:
            lines.append(f"   • {p['direction']} <code>{p['bybit_symbol']}</code> @ {p['fill']:g}{' 🛡️' if p['be'] else ''}")
        last = self.state.get("log", [])[-6:]
        if last:
            lines.append("\n<b>آخرین رویدادها:</b>")
            lines += [f"<code>{x}</code>" for x in last]
        lines.append("\n<code>/demo on</code> · <code>/demo off</code>")
        return "\n".join(lines)

    def set_enabled(self, on):
        with self.lock:
            self.state["enabled"] = bool(on)
            self._save()
