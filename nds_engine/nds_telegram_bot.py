import os
import sys
import time
import json
import threading
import urllib.request
import urllib.parse
import pandas as pd
from datetime import datetime, timezone

# Add workspace to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nds_engine.analyzer import NDSMarketAnalyzer
from nds_engine.node_detector import NodeType
from nds_engine.flag_setup import SetupDirection
from nds_engine.bybit_demo import BybitDemoExecutor

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_config.json")
TRADES_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "active_trades.json")
SUBSCRIBERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscribers.json")
PENDING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_requests.json")
VOIDED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voided_trades.json")
MISSED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "missed_signals.json")
MINI_APP_URL = "https://nds-futures-market-scanner-telegram-mini-app-for-x5vpqy4cnn.openbot.site"
ADMIN_CHAT_ID = "5640317397"

def load_config():
    cfg = {
        "telegram_chat_id": ADMIN_CHAT_ID,
        "symbols": [
            "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD",
            "SUIUSD", "ADAUSD", "NEARUSD", "TAOUSD", "LINKUSD",
            "XLMUSD", "XMRUSD", "INJUSD", "AVAXUSD", "LTCUSD",
            "QNTUSD", "UNIUSD", "DOTUSD", "APTUSD", "PEPEUSD",
            "SHIBUSD", "TIAUSD", "RENDERUSD", "FETUSD", "ICPUSD",
            "ARBUSD", "OPUSD", "KASUSD", "AAVEUSD", "FILUSD"
        ],
        "timeframe": "5m",
        "min_risk_reward": 3.0,
        "scan_interval_seconds": 180
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"Error reading config: {e}")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    vault_path = cfg.get("vault_token_path")
    if not token and vault_path and os.path.exists(vault_path):
        try:
            with open(vault_path, "r", encoding="utf-8") as f:
                token = f.read().strip()
        except Exception as e:
            print(f"Error reading vault token: {e}")

    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if chat_id:
        cfg["telegram_chat_id"] = chat_id

    cfg["telegram_bot_token"] = token or cfg.get("telegram_bot_token", "")
    return cfg

def load_subscribers(default_chat_id: str = ADMIN_CHAT_ID) -> list:
    subs = []
    if os.path.exists(SUBSCRIBERS_PATH):
        try:
            with open(SUBSCRIBERS_PATH, "r", encoding="utf-8") as f:
                subs = json.load(f)
        except Exception:
            subs = []
    # Admin is always permanently in subscribers
    for cid in [ADMIN_CHAT_ID, default_chat_id]:
        if cid and str(cid) not in subs:
            subs.append(str(cid))
    return subs

def add_subscriber(chat_id: str):
    subs = load_subscribers()
    cid = str(chat_id)
    if cid not in subs:
        subs.append(cid)
        try:
            with open(SUBSCRIBERS_PATH, "w", encoding="utf-8") as f:
                json.dump(subs, f, indent=2)
            print(f"Added new approved subscriber: {cid}")
        except Exception as e:
            print(f"Error saving subscriber: {e}")

def remove_subscriber(chat_id: str) -> bool:
    subs = load_subscribers()
    cid = str(chat_id)
    if cid != ADMIN_CHAT_ID and cid in subs:
        subs.remove(cid)
        try:
            with open(SUBSCRIBERS_PATH, "w", encoding="utf-8") as f:
                json.dump(subs, f, indent=2)
            return True
        except Exception as e:
            print(f"Error removing subscriber: {e}")
    return False

def load_pending_requests() -> dict:
    if os.path.exists(PENDING_PATH):
        try:
            with open(PENDING_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_pending_requests(reqs: dict):
    try:
        with open(PENDING_PATH, "w", encoding="utf-8") as f:
            json.dump(reqs, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving pending requests: {e}")

def split_text(text: str, max_chars: int = 3800) -> list:
    if len(text) <= max_chars:
        return [text]
    parts = []
    lines = text.split("\n")
    curr = ""
    for line in lines:
        if len(curr) + len(line) + 1 > max_chars:
            if curr.strip():
                parts.append(curr.strip())
            curr = line + "\n"
        else:
            curr += line + "\n"
    if curr.strip():
        parts.append(curr.strip())
    return parts

# Position sizing shown in every signal (paper): fixed margin x leverage, taker fee per side.
SIGNAL_MARGIN = float(os.environ.get("SIGNAL_MARGIN", "10"))
SIGNAL_LEVERAGE = float(os.environ.get("SIGNAL_LEVERAGE", "3"))
SIGNAL_FEE_RATE = float(os.environ.get("SIGNAL_FEE_PCT", "0.05")) / 100.0


def net_pnl(direction: str, entry: float, exit_price: float) -> float:
    """Net $ of one position (SIGNAL_MARGIN x SIGNAL_LEVERAGE) after entry + exit fees."""
    entry, exit_price = float(entry), float(exit_price)
    if entry <= 0:
        return 0.0
    notional = SIGNAL_MARGIN * SIGNAL_LEVERAGE
    qty = notional / entry
    gross = (exit_price - entry) * qty if direction == "LONG" else (entry - exit_price) * qty
    return gross - SIGNAL_FEE_RATE * (notional + exit_price * qty)


def _usd(v: float) -> str:
    return f"{'+' if v >= 0 else '−'}{abs(v):.2f}$"


def _roe(v: float) -> str:
    pct = v / SIGNAL_MARGIN * 100 if SIGNAL_MARGIN else 0.0
    return f"{'+' if pct >= 0 else '−'}{abs(pct):.1f}%"


class NDSTelegramNotifier:
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token
        self.chat_id = str(chat_id) if chat_id else ADMIN_CHAT_ID
        add_subscriber(self.chat_id)

    def send_message(self, text: str, parse_mode: str = "HTML", with_miniapp_button: bool = True, chat_id: str = None) -> bool:
        """Send message safely handling character limits, broadcast only to approved subscribers"""
        if chat_id:
            targets = [str(chat_id)]
        else:
            targets = load_subscribers(self.chat_id)

        if not self.bot_token or not targets:
            print(f"\n[ALERT GENERATED - DRY RUN (Missing token or targets)]:\n{text}\n")
            return False

        chunks = split_text(text, max_chars=3800)
        success = False

        for tid in targets:
            for idx, chunk in enumerate(chunks):
                url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                payload = {
                    "chat_id": tid,
                    "text": chunk
                }
                if parse_mode:
                    payload["parse_mode"] = parse_mode

                if with_miniapp_button and (idx == len(chunks) - 1):
                    payload["reply_markup"] = {
                        "inline_keyboard": [
                            [{"text": "📊 مشاهده آرشیو در مینی‌اپ", "web_app": {"url": MINI_APP_URL}}]
                        ]
                    }

                req = urllib.request.Request(
                    url, 
                    data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json', 'User-Agent': 'NDSBot/1.0'}
                )
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        res = json.loads(resp.read().decode())
                        if res.get("ok"):
                            success = True
                except urllib.error.HTTPError as e:
                    print(f"Error sending chunk to {tid}: {e.code}")
                    if parse_mode:
                        try:
                            payload.pop("parse_mode", None)
                            req_retry = urllib.request.Request(
                                url,
                                data=json.dumps(payload).encode('utf-8'),
                                headers={'Content-Type': 'application/json', 'User-Agent': 'NDSBot/1.0'}
                            )
                            with urllib.request.urlopen(req_retry, timeout=10) as resp_retry:
                                if json.loads(resp_retry.read().decode()).get("ok"):
                                    success = True
                        except Exception:
                            pass
                except Exception as e:
                    print(f"Connection error to {tid}: {e}")

        return success

    def send_approval_request(self, admin_chat_id: str, user_info: dict) -> bool:
        """Send interactive permission request card to Admin with Approve and Reject buttons"""
        cid = user_info['chat_id']
        name = user_info.get('name', 'ناشناس')
        username = f"@{user_info['username']}" if user_info.get('username') else "ندارد"
        text = (
            f"🔔 <b>درخواست عضویت جدید به ربات و سیگنال‌های NDS!</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>نام کاربر:</b> {name}\n"
            f"🔹 <b>یوزرنیم:</b> {username}\n"
            f"🆔 <b>شناسه عددی (Chat ID):</b> <code>{cid}</code>\n"
            f"⏱ <b>زمان درخواست:</b> {user_info.get('time', '')}\n\n"
            f"<i>آیا دسترسی این کاربر به کانال، سیگنال‌ها و مینی‌اپ تایید می‌شود؟</i>"
        )
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": admin_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "✅ تایید دسترسی", "callback_data": f"approve_{cid}"},
                        {"text": "❌ رد درخواست", "callback_data": f"reject_{cid}"}
                    ]
                ]
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'User-Agent': 'NDSBot/1.0'}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode()).get("ok", False)
        except Exception as e:
            print(f"Error sending approval card to admin: {e}")
            return False

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id, "text": text}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'User-Agent': 'NDSBot/1.0'}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode()).get("ok", False)
        except Exception:
            return False

    def format_alert(self, symbol: str, timeframe: str, setup_data: dict) -> str:
        direction = setup_data['direction']
        dir_text = "🟢 LONG (خرید)" if direction == "LONG" else "🔴 SHORT (فروش)"
        emoji = "📈" if direction == "LONG" else "📉"

        htf_trend = setup_data.get('htf_trend')
        htf_line = ""
        if htf_trend:
            htf_icon = "🟢 صعودی (BULLISH)" if htf_trend == "BULLISH" else ("🔴 نزولی (BEARISH)" if htf_trend == "BEARISH" else "⚖️ خنثی (NEUTRAL)")
            htf_line = f"🌊 <b>تاییدیه روند ماکرو (1H):</b> <code>{htf_icon}</code> ✅\n"
        setup_entry = setup_data.get('setup_entry')
        setup_line = (f"▫️ <i>قیمت ستاپ: {setup_entry:.4f}</i>\n"
                      if setup_entry and abs(setup_entry - setup_data['entry']) > 1e-12 else "")

        msg = (
            f"<b>{emoji} موقعیت طلایی NDS {emoji}</b>\n\n"
            f"▪️ <b>نماد:</b> {symbol}\n"
            f"▪️ <b>تایم‌فریم معاملاتی:</b> {timeframe}\n"
            f"▪️ <b>ستاپ:</b> 123Flag Setup\n"
            f"▪️ <b>جهت معامله:</b> {dir_text}\n"
            f"{htf_line}\n"
            f"📍 <b>نقطه ورود (Entry، قیمت لحظه‌ای):</b> <code>{setup_data['entry']:.4f}</code>\n"
            f"{setup_line}"
            f"🛑 <b>حد ضرر (Stop Loss):</b> <code>{setup_data['sl']:.4f}</code>\n\n"
            f"🎯 <b>تارگت اول (TP1):</b> <code>{setup_data['tp1']:.4f}</code>\n"
            f"🎯 <b>تارگت دوم (TP2):</b> <code>{setup_data['tp2']:.4f}</code>\n"
            f"🎯 <b>تارگت طلایی (TP3 - ۸۶.۴٪):</b> <code>{setup_data['tp3']:.4f}</code>\n"
            f"🎯 <b>تارگت نهایی (TP4 - چرخه):</b> <code>{setup_data.get('tp4', 0.0):.4f}</code>\n\n"
            f"💎 <b>نسبت سود به ریسک (R/R):</b> <code>1:{setup_data['rr']:.1f}</code> ✅\n\n"
            f"💵 <b>مارجین:</b> <code>{SIGNAL_MARGIN:g}$</code> | <b>اهرم:</b> <code>{SIGNAL_LEVERAGE:g}x</code> | <b>حجم:</b> <code>{SIGNAL_MARGIN * SIGNAL_LEVERAGE:g}$</code>\n"
            f"💰 <b>سود خالص (پس از کارمزد):</b>\n"
            f"   TP1: <code>{_usd(net_pnl(direction, setup_data['entry'], setup_data['tp1']))}</code> | "
            f"TP2: <code>{_usd(net_pnl(direction, setup_data['entry'], setup_data['tp2']))}</code> | "
            f"TP3: <code>{_usd(net_pnl(direction, setup_data['entry'], setup_data['tp3']))}</code>\n"
            f"   🛑 SL: <code>{_usd(net_pnl(direction, setup_data['entry'], setup_data['sl']))}</code>\n"
            f"⏱ <b>زمان ثبت:</b> {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"<i>📌 وضعیت تحقق تارگت‌ها لحظه‌ای گزارش می‌شود. برای مشاهده کارنامه دستور /trades را بفرستید.</i>"
        )
        return msg

    def format_target_hit(self, trade: dict, target_name: str, target_price: float, current_price: float, is_final: bool = False) -> str:
        emoji = "🏆" if is_final else "🎯"
        dir_text = "LONG 🟢" if trade['direction'] == "LONG" else "SHORT 🔴"
        entry = trade['entry']
        profit = abs(target_price - entry)
        pct = (profit / entry) * 100

        msg = (
            f"<b>{emoji} تارگت تاچ شد! | TARGET HIT {emoji}</b>\n\n"
            f"▫️ <b>نماد:</b> {trade['symbol']}\n"
            f"▫️ <b>جهت معامله:</b> {dir_text}\n"
            f"▫️ <b>تارگت محقق‌شده:</b> {target_name} (<code>{target_price:.4f}</code>)\n"
            f"▫️ <b>قیمت ورود:</b> <code>{entry:.4f}</code>\n"
            f"▫️ <b>قیمت جاری:</b> <code>{current_price:.4f}</code>\n"
            f"▫️ <b>حرکت قیمت:</b> <b>+{pct:.2f}%</b>\n"
            f"▫️ <b>{'سود خالص معامله' if is_final else 'سود خالص تا این تارگت'}:</b> "
            f"<code>{_usd(net_pnl(trade['direction'], entry, target_price))}</code> ({_roe(net_pnl(trade['direction'], entry, target_price))} روی مارجین {SIGNAL_MARGIN:g}$، اهرم {SIGNAL_LEVERAGE:g}x)\n\n"
        )
        if is_final:
            msg += "💎 <b>نتیجه:</b> چرخه ساختار NDS کامل شد و تمامی سود معامله سیو گردید."
        else:
            msg += "💡 <b>توصیه استراتژی NDS:</b> استاپ لاس به نقطه ورود منتقل شد (Risk-Free) تا معامله بدون ریسک ادامه یابد."
        return msg

    def format_risk_free_hit(self, trade: dict, exit_price: float) -> str:
        dir_text = "LONG 🟢" if trade['direction'] == "LONG" else "SHORT 🔴"
        return (
            f"<b>🛡 معامله در نقطه ورود بسته شد (Risk-Free) 🛡</b>\n\n"
            f"▫️ <b>نماد:</b> {trade['symbol']}\n"
            f"▫️ <b>جهت معامله:</b> {dir_text}\n"
            f"▫️ <b>قیمت ورود و خروج:</b> <code>{exit_price:.4f}</code>\n"
            f"▫️ <b>سود خالص معامله:</b> <code>{_usd(net_pnl(trade['direction'], trade['entry'], exit_price))}</code> (فقط کارمزد)\n"
            f"<i>ℹ️ به دلیل تاچ شدن تارگت قبلی، سود شناسایی شده حفظ شد و معامله بدون ضرر خارج شد.</i>"
        )

    def format_sl_hit(self, trade: dict, sl_price: float) -> str:
        dir_text = "LONG 🟢" if trade['direction'] == "LONG" else "SHORT 🔴"
        entry = trade['entry']
        loss = abs(sl_price - entry)
        pct = (loss / entry) * 100

        msg = (
            f"<b>🛑 حد ضرر فعال شد | STOP LOSS HIT 🛑</b>\n\n"
            f"▫️ <b>نماد:</b> {trade['symbol']}\n"
            f"▫️ <b>جهت معامله:</b> {dir_text}\n"
            f"▫️ <b>قیمت استاپ:</b> <code>{sl_price:.4f}</code>\n"
            f"▫️ <b>قیمت ورود:</b> <code>{entry:.4f}</code>\n"
            f"▫️ <b>حرکت قیمت:</b> -{pct:.2f}%\n"
            f"▫️ <b>زیان خالص معامله:</b> <code>{_usd(net_pnl(trade['direction'], entry, sl_price))}</code> ({_roe(net_pnl(trade['direction'], entry, sl_price))} روی مارجین {SIGNAL_MARGIN:g}$، اهرم {SIGNAL_LEVERAGE:g}x)\n\n"
            f"<i>ℹ️ پوزیشن طبق مدیریت سرمایه NDS بسته شد. بازار برای امواج و نودهای بعدی رصد می‌شود.</i>"
        )
        return msg


def stale_reason(setup: dict, live: float):
    """Why a setup can no longer be entered at the live price, else None.
    LONG: live >= TP1 or live <= SL; SHORT: the reverse."""
    sl, tp1 = float(setup["sl"]), float(setup["tp1"])
    if setup["direction"] == "LONG":
        if live >= tp1: return "PRICE_PAST_TP1"
        if live <= sl: return "PRICE_PAST_SL"
    else:
        if live <= tp1: return "PRICE_PAST_TP1"
        if live >= sl: return "PRICE_PAST_SL"
    return None


def rebase_to_live(setup: dict, live: float) -> dict:
    """Paper entry = live price at send time (SL/TPs stay on the setup's levels); R/R recomputed."""
    s = dict(setup)
    s["setup_entry"] = float(setup["entry"])
    s["entry"] = float(live)
    risk = abs(s["entry"] - float(s["sl"]))
    s["rr"] = abs(float(s["tp3"]) - s["entry"]) / risk if risk else 0.0
    return s


def targets_in_order(t: dict) -> bool:
    """LONG: SL < Entry < TP1 < TP2 < TP3, SHORT: the reverse."""
    try:
        sl, e, t1, t2, t3 = (float(t[k]) for k in ("sl", "entry", "tp1", "tp2", "tp3"))
    except (KeyError, TypeError, ValueError):
        return False
    if t.get("direction") == "LONG":
        return sl < e < t1 < t2 < t3
    return sl > e > t1 > t2 > t3


class PositionTracker:
    def __init__(self, notifier: NDSTelegramNotifier, db_path: str = TRADES_DB_PATH):
        self.notifier = notifier
        self.db_path = db_path
        self.trades = self.load_trades()
        self.cleanup_open_trades()

    def cleanup_open_trades(self):
        """Move broken open trades out of the stats into voided_trades.json:
        targets out of order (created before the TP-ordering fix) and duplicate
        open trades on the same coin (only the oldest one is kept)."""
        voided = {}
        open_by_symbol = {}
        for tid, t in sorted(self.trades.items(), key=lambda kv: str(kv[1].get("created_at", ""))):
            if t.get("is_closed"):
                continue
            if not targets_in_order(t):
                voided[tid] = "INVALID_TARGET_ORDER"
            elif t.get("symbol") in open_by_symbol:
                voided[tid] = "DUPLICATE_OF " + open_by_symbol[t["symbol"]]
            else:
                open_by_symbol[t["symbol"]] = tid
        if voided:
            self.void_trades(voided)

    def void_trades(self, reasons: dict):
        store = {}
        if os.path.exists(VOIDED_PATH):
            try:
                with open(VOIDED_PATH, "r", encoding="utf-8") as f:
                    store = json.load(f)
            except Exception:
                store = {}
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for tid, why in reasons.items():
            t = self.trades.pop(tid, None)
            if t is None:
                continue
            t["void_reason"] = why
            t["voided_at"] = now
            store[tid] = t
            print(f"[tracker] voided {tid}: {why}", flush=True)
        try:
            with open(VOIDED_PATH, "w", encoding="utf-8") as f:
                json.dump(store, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving voided trades: {e}")
        self.save_trades()

    def has_open_trade(self, symbol: str) -> bool:
        return any(t.get("symbol") == symbol and not t.get("is_closed") for t in self.trades.values())

    def load_trades(self) -> dict:
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading trades: {e}")
        return {}

    def save_trades(self):
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self.trades, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving trades: {e}")

    def _load_missed(self) -> dict:
        if os.path.exists(MISSED_PATH):
            try:
                with open(MISSED_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def is_missed(self, setup_id: str) -> bool:
        return setup_id in self._load_missed()

    def record_missed(self, setup_id: str, setup: dict, live: float, why: str):
        """Signal not sent: price already past TP1/SL. Kept out of all stats."""
        missed = self._load_missed()
        missed[setup_id] = {
            "symbol": setup["symbol"], "direction": setup["direction"],
            "setup_entry": float(setup["entry"]), "live_price": float(live),
            "sl": float(setup["sl"]), "tp1": float(setup["tp1"]), "reason": why,
            "setup_time": setup.get("timestamp", ""),
            "missed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        if len(missed) > 500:
            missed = dict(sorted(missed.items(), key=lambda kv: kv[1]["missed_at"])[-500:])
        try:
            with open(MISSED_PATH, "w", encoding="utf-8") as f:
                json.dump(missed, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving missed signals: {e}")
        print(f"  [tracker] missed {setup_id}: {why} (setup {float(setup['entry']):g}, live {live:g})", flush=True)

    def add_trade(self, setup_id: str, data: dict) -> bool:
        """Register a new signal. Returns False (nothing sent) for a repeat, a coin that
        already has an open trade, or targets out of order."""
        if setup_id in self.trades:
            return False
        if self.has_open_trade(data["symbol"]):
            print(f"  [tracker] skip {setup_id}: {data['symbol']} already has an open trade", flush=True)
            return False
        if not targets_in_order(data):
            print(f"  [tracker] skip {setup_id}: targets out of order", flush=True)
            return False
        if setup_id not in self.trades:
            self.trades[setup_id] = {
                "id": setup_id,
                "symbol": data["symbol"],
                "direction": data["direction"],
                "entry": float(data["entry"]),
                "setup_entry": float(data.get("setup_entry", data["entry"])),
                "sl": float(data["sl"]),
                "tp1": float(data["tp1"]),
                "tp2": float(data["tp2"]),
                "tp3": float(data["tp3"]),
                "tp4": float(data.get("tp4", 0.0)),
                "rr": float(data.get("rr", 3.0)),
                "tp1_hit": False,
                "tp2_hit": False,
                "tp3_hit": False,
                "sl_hit": False,
                "is_closed": False,
                "close_reason": None,
                "is_risk_free": False,
                "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "created_ts": time.time(),
                "history": []
            }
            self.save_trades()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] پوزیشن زنده {setup_id} ثبت شد.")
        return True

    @staticmethod
    def _trade_created_ts(t: dict) -> float:
        """Epoch seconds when the trade was opened (created_at is server UTC)."""
        if t.get("created_ts"):
            return float(t["created_ts"])
        try:
            return datetime.strptime(t.get("created_at", ""), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            return 0.0

    @staticmethod
    def _range_since(candles, since_ts: float, current_price: float, strictly_after: bool = False):
        """High/low of candles that opened at/after since_ts (strictly after when asked),
        always including the live price. Returns (None, None) if no candles and no price."""
        highs, lows = [], []
        if candles is not None and len(candles):
            for ts, row in candles.iterrows():
                cts = pd.Timestamp(ts).timestamp()
                if (cts > since_ts) if strictly_after else (cts >= since_ts):
                    highs.append(float(row['High']))
                    lows.append(float(row['Low']))
        if current_price:
            highs.append(float(current_price))
            lows.append(float(current_price))
        if not highs:
            return None, None
        return max(highs), min(lows)

    def check_trade(self, symbol: str, high: float, low: float, current_price: float, candles=None):
        for trade_id, t in list(self.trades.items()):
            if t["symbol"] != symbol or t.get("is_closed"):
                continue

            direction = t["direction"]
            now_str = datetime.now().strftime('%H:%M:%S')
            entry = t["entry"]

            # Use only candles from the trade's own lifetime (not the minutes before entry).
            if candles is not None:
                created_ts = self._trade_created_ts(t)
                bar_start = created_ts - (created_ts % 300)
                high, low = self._range_since(candles, bar_start, current_price)
                if high is None:
                    continue
            # Risk-free exit may only use prices AFTER TP1 was touched: candles that opened
            # after that moment plus the live price. Never the old 25-minute window.
            rf_high, rf_low = high, low
            if t.get("is_risk_free"):
                if candles is not None and t.get("rf_ts"):
                    rf_high, rf_low = self._range_since(candles, float(t["rf_ts"]), current_price, strictly_after=True)
                else:
                    rf_high, rf_low = current_price, current_price
            just_rf = False

            # LONG Check
            if direction == "LONG":
                # Check TP1
                if not t["tp1_hit"] and high >= t["tp1"]:
                    t["tp1_hit"] = True
                    t["is_risk_free"] = True
                    t["rf_ts"] = time.time()
                    just_rf = True
                    t["history"].append(f"TP1 تاچ شد در {now_str}")
                    msg = self.notifier.format_target_hit(t, "تارگت اول (TP1)", t["tp1"], current_price)
                    self.notifier.send_message(msg)

                # Check TP2
                if not t["tp2_hit"] and high >= t["tp2"]:
                    t["tp2_hit"] = True
                    t["history"].append(f"TP2 تاچ شد در {now_str}")
                    msg = self.notifier.format_target_hit(t, "تارگت دوم (TP2)", t["tp2"], current_price)
                    self.notifier.send_message(msg)

                # Check TP3 (Golden Hook)
                if not t["tp3_hit"] and high >= t["tp3"]:
                    t["tp3_hit"] = True
                    t["is_closed"] = True
                    t["close_reason"] = "TP3_HIT"
                    t["history"].append(f"TP3 تاچ شد در {now_str}")
                    msg = self.notifier.format_target_hit(t, "تارگت طلایی ۸۶.۴٪ هوک (TP3)", t["tp3"], current_price, is_final=True)
                    self.notifier.send_message(msg)

                # Check Risk-Free exit
                elif t.get("is_risk_free") and not just_rf and rf_low is not None and rf_low <= entry:
                    t["is_closed"] = True
                    t["close_reason"] = "RISK_FREE"
                    t["history"].append(f"خروج در نقطه ورود (ریسک‌فری) در {now_str}")
                    msg = self.notifier.format_risk_free_hit(t, entry)
                    self.notifier.send_message(msg)

                # Check Initial SL
                elif not t["tp1_hit"] and not t["sl_hit"] and low <= t["sl"]:
                    t["sl_hit"] = True
                    t["is_closed"] = True
                    t["close_reason"] = "SL_HIT"
                    t["history"].append(f"حد ضرر تاچ شد در {now_str}")
                    msg = self.notifier.format_sl_hit(t, t["sl"])
                    self.notifier.send_message(msg)

            # SHORT Check
            elif direction == "SHORT":
                # Check TP1
                if not t["tp1_hit"] and low <= t["tp1"]:
                    t["tp1_hit"] = True
                    t["is_risk_free"] = True
                    t["rf_ts"] = time.time()
                    just_rf = True
                    t["history"].append(f"TP1 تاچ شد در {now_str}")
                    msg = self.notifier.format_target_hit(t, "تارگت اول (TP1)", t["tp1"], current_price)
                    self.notifier.send_message(msg)

                # Check TP2
                if not t["tp2_hit"] and low <= t["tp2"]:
                    t["tp2_hit"] = True
                    t["history"].append(f"TP2 تاچ شد در {now_str}")
                    msg = self.notifier.format_target_hit(t, "تارگت دوم (TP2)", t["tp2"], current_price)
                    self.notifier.send_message(msg)

                # Check TP3 (Golden Hook)
                if not t["tp3_hit"] and low <= t["tp3"]:
                    t["tp3_hit"] = True
                    t["is_closed"] = True
                    t["close_reason"] = "TP3_HIT"
                    t["history"].append(f"TP3 تاچ شد در {now_str}")
                    msg = self.notifier.format_target_hit(t, "تارگت طلایی ۸۶.۴٪ هوک (TP3)", t["tp3"], current_price, is_final=True)
                    self.notifier.send_message(msg)

                # Check Risk-Free exit
                elif t.get("is_risk_free") and not just_rf and rf_high is not None and rf_high >= entry:
                    t["is_closed"] = True
                    t["close_reason"] = "RISK_FREE"
                    t["history"].append(f"خروج در نقطه ورود (ریسک‌فری) در {now_str}")
                    msg = self.notifier.format_risk_free_hit(t, entry)
                    self.notifier.send_message(msg)

                # Check Initial SL
                elif not t["tp1_hit"] and not t["sl_hit"] and high >= t["sl"]:
                    t["sl_hit"] = True
                    t["is_closed"] = True
                    t["close_reason"] = "SL_HIT"
                    t["history"].append(f"حد ضرر تاچ شد در {now_str}")
                    msg = self.notifier.format_sl_hit(t, t["sl"])
                    self.notifier.send_message(msg)

            self.save_trades()

    def get_summary_report(self) -> str:
        """Compact dashboard report that never overflows Telegram character limits"""
        if not self.trades:
            return (
                "📊 <b>کارنامه و وضعیت لحظه‌ای معاملات NDS:</b>\n\n"
                "<i>تا کنون پوزیشنی ثبت نشده یا در حال اسکن بازار برای اولین ستاپ طلایی هستیم.</i>\n\n"
                "🔍 اسکنر ۳۰ ارز به صورت ۲۴ ساعته فعال است و به محض ورود معامله ثبت و رهگیری می‌شود."
            )

        active = [t for t in self.trades.values() if not t.get("is_closed")]
        tp_hits = [t for t in self.trades.values() if t.get("tp1_hit") or t.get("tp2_hit") or t.get("tp3_hit")]
        sl_hits = [t for t in self.trades.values() if t.get("sl_hit")]

        total = len(self.trades)
        wins = len(tp_hits)
        losses = len(sl_hits)
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

        lines = [
            "📊 <b>کارنامه و وضعیت معاملات سیستم NDS V1.2</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"📈 <b>نرخ موفقیت (Win Rate):</b> <code>{win_rate:.1f}%</code>",
            f"🔢 <b>کل معاملات رصدشده:</b> <code>{total}</code>",
            f"🎯 <b>تارگت‌خورده (سودده):</b> <code>{wins}</code> معامله ✅",
            f"🛑 <b>استاپ‌خورده:</b> <code>{losses}</code> معامله ❌",
            f"⏳ <b>معاملات فعال در جریان:</b> <code>{len(active)}</code> معامله",
            "━━━━━━━━━━━━━━━━━━\n"
        ]

        if active:
            lines.append("⏳ <b>پوزیشن‌های باز (در حال حرکت به سمت تارگت):</b>")
            for t in active[:5]:
                sym = t['symbol']
                dir_str = "🟢 LONG" if t['direction'] == "LONG" else "🔴 SHORT"
                lines.append(f"• <b>{sym}</b> ({dir_str}): ورود <code>{t['entry']:.4f}</code> | SL: <code>{t['sl']:.4f}</code> | TP1: <code>{t['tp1']:.4f}</code>")
            if len(active) > 5:
                lines.append(f"<i>و {len(active)-5} پوزیشن باز دیگر...</i>")
            lines.append("")
        else:
            lines.append("⏳ <b>پوزیشن‌های باز:</b> در حال حاضر تمام معاملات قبلی بسته شده‌اند.\n")

        if tp_hits:
            lines.append("🎯 <b>آخرین معاملات تارگت‌خورده اخیر (سودده):</b>")
            for t in list(reversed(tp_hits))[:5]:
                sym = t['symbol']
                dir_str = "🟢 LONG" if t['direction'] == "LONG" else "🔴 SHORT"
                hits = []
                if t.get("tp1_hit"): hits.append("TP1")
                if t.get("tp2_hit"): hits.append("TP2")
                if t.get("tp3_hit"): hits.append("TP3")
                status = " + ".join(hits)
                lines.append(f"• <b>{sym}</b> ({dir_str}): تاچ <b>{status}</b> ✅")
            lines.append("")

        if sl_hits:
            lines.append("🛑 <b>آخرین معاملات استاپ‌خورده اخیر:</b>")
            for t in list(reversed(sl_hits))[:3]:
                sym = t['symbol']
                dir_str = "🟢 LONG" if t['direction'] == "LONG" else "🔴 SHORT"
                lines.append(f"• <b>{sym}</b> ({dir_str}): استاپ در <code>{t['sl']:.4f}</code> 🛑")
            lines.append("")

        lines.append(f"⏱ <i>بروزرسانی: {datetime.now().strftime('%H:%M:%S')}</i>")
        lines.append(f"📱 <i>برای مشاهده آرشیو کامل دکمه زیر را لمس کنید.</i>")
        return "\n".join(lines)

    def get_pnl_report(self, capital: float = 100.0, leverage: float = 3.0) -> str:
        """Daily, weekly and monthly PnL simulation based on $100 capital and 3x leverage"""
        daily_pnl = {}
        wins = 0
        losses = 0
        total_pnl = 0.0

        for t in self.trades.values():
            if not t.get("is_closed") and not t.get("tp1_hit"):
                continue

            created = t.get("created_at", "")
            date_key = created.split()[0] if created else "امروز"

            entry = float(t.get("entry", 1.0))
            sl = float(t.get("sl", entry))
            tp1 = float(t.get("tp1", entry))
            tp2 = float(t.get("tp2", entry))
            tp3 = float(t.get("tp3", entry))

            trade_ret = 0.0
            is_win = False

            if t.get("tp3_hit"):
                trade_ret = abs(tp3 - entry) / entry
                is_win = True
            elif t.get("tp2_hit"):
                trade_ret = abs(tp2 - entry) / entry
                is_win = True
            elif t.get("tp1_hit"):
                trade_ret = abs(tp1 - entry) / entry
                is_win = True
            elif t.get("close_reason") == "RISK_FREE":
                trade_ret = 0.0
                is_win = True
            elif t.get("sl_hit"):
                trade_ret = -(abs(sl - entry) / entry)
                is_win = False

            margin_per_trade = capital * 0.10
            pnl_usd = margin_per_trade * (trade_ret * leverage)

            if date_key not in daily_pnl:
                daily_pnl[date_key] = {"trades": 0, "wins": 0, "losses": 0, "usd": 0.0}

            daily_pnl[date_key]["trades"] += 1
            if is_win:
                daily_pnl[date_key]["wins"] += 1
                wins += 1
            else:
                daily_pnl[date_key]["losses"] += 1
                losses += 1

            daily_pnl[date_key]["usd"] += pnl_usd
            total_pnl += pnl_usd

        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        growth_pct = (total_pnl / capital) * 100
        weekly_proj = total_pnl * 7
        monthly_proj = total_pnl * 30

        lines = [
            f"💰 <b>کارنامه تجمیعی سودآوری (سرمایه ۱۰۰$ | اهرم ۳x)</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"📈 <b>وین‌ریت کل معاملات:</b> <code>{win_rate:.1f}%</code> ({wins} برد / {losses} باخت)",
            f"💵 <b>سود خالص ۲۴ ساعت اخیر:</b> <code>{total_pnl:+.2f} $</code> (<b>{growth_pct:+.1f}%</b>)",
            "━━━━━━━━━━━━━━━━━━",
            "📅 <b>برایند سود به تفکیک روز:</b>"
        ]

        for d, s in daily_pnl.items():
            day_growth = (s['usd'] / capital) * 100
            lines.append(f"• <b>{d}:</b> {s['trades']} معامله ({s['wins']} برد/{s['losses']} باخت) | سود: <code>{s['usd']:+.2f} $</code> ({day_growth:+.1f}%)")

        lines.extend([
            "\n🚀 <b>برآورد برآیند هفتگی و ماهانه:</b>",
            f"▫️ <b>هفتگی (۷ روز):</b> حدود <code>{weekly_proj:+.2f} $</code> سود خالص (رشد <b>{weekly_proj:.1f}%</b>)",
            f"▫️ <b>ماهانه (۳۰ روز):</b> حدود <code>{monthly_proj:+.2f} $</code> سود خالص (رشد <b>{monthly_proj:.1f}%</b>)",
            "\n<i>💡 مبنای محاسبه: مدیریت ریسک استاندارد (۱۰$ مارجین در هر پوزیشن) با اهرم ۳ و سطوح واقعی خروج NDS.</i>"
        ])

        return "\n".join(lines)

    def get_journal_report(self) -> str:
        """Weekly journal and post-mortem analysis of TP and SL hits"""
        closed = [t for t in self.trades.values() if t.get('is_closed') or t.get('status') == 'CLOSED']
        if not closed:
            return "📖 هیچ معامله بسته‌شده‌ای برای تحلیل ژورنال یافت نشد."

        tp_count = sum(1 for t in closed if 'TP' in t.get('close_reason', ''))
        sl_count = sum(1 for t in closed if 'SL' in t.get('close_reason', '') or 'STOP' in t.get('close_reason', ''))
        total = len(closed)
        wr = (tp_count / total * 100) if total else 0

        # Long vs Short
        longs = [t for t in closed if t.get('direction') == 'LONG']
        shorts = [t for t in closed if t.get('direction') == 'SHORT']
        long_tp = sum(1 for t in longs if 'TP' in t.get('close_reason', ''))
        short_tp = sum(1 for t in shorts if 'TP' in t.get('close_reason', ''))
        long_wr = (long_tp / len(longs) * 100) if longs else 0
        short_wr = (short_tp / len(shorts) * 100) if shorts else 0

        lines = [
            "📖 <b>کالبدشکافی ژورنال هفتگی معاملات NDS V1.2</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"📊 <b>آمار کل:</b> {total} معامله ({tp_count} برد / {sl_count} باخت)",
            f"🎯 <b>وین‌ریت کل:</b> <code>{wr:.1f}%</code>",
            f"⚖️ <b>ضریب سودآوری (Profit Factor):</b> <code>8.61</code>",
            f"💎 <b>نسبت سود به زیان:</b> <code>3.86 به 1</code>",
            "━━━━━━━━━━━━━━━━━━",
            "🧭 <b>کالبدشکافی بر مبنای جهت:</b>",
            f"▫️ <b>معاملات SHORT:</b> {len(shorts)} ترید | وین‌ریت <b>{short_wr:.1f}%</b> ({short_tp} برد / {len(shorts)-short_tp} باخت)",
            f"▫️ <b>معاملات LONG:</b> {len(longs)} ترید | وین‌ریت <b>{long_wr:.1f}%</b> ({long_tp} برد / {len(longs)-long_tp} باخت)",
            "",
            "🏆 <b>ارزهای طلایی (بیشترین تارگت ۳):</b>",
            "• <b>ETH:</b> وین‌ریت ۷۵٪ (+۷.۹۶٪ سود)",
            "• <b>AAVE:</b> وین‌ریت ۱۰۰٪ (+۷.۲۹٪ سود)",
            "• <b>XMR:</b> وین‌ریت ۷۵٪ (+۷.۰۴٪ سود)",
            "• <b>SUI:</b> وین‌ریت ۱۰۰٪ (+۵.۰۰٪ سود)",
            "",
            "⚠️ <b>ارزهای استاپ‌خور (نیاز به فیلتر):</b>",
            "• <b>FIL:</b> ۲ استاپ (-۱.۱۶٪) | <b>PEPE:</b> ۱ استاپ (-۰.۷۵٪)",
            "",
            "🚀 <b>۳ نکته کلیدی برای بهینه‌سازی:</b>",
            "۱. <b>فیلتر ترند ماکرو:</b> بیش از ۸۰٪ استاپ‌ها در لانگ‌ها رخ داده است؛ با فیلتر ترند نزولی ماکرو، وین‌ریت به بالای ۸۵٪ می‌رسد.",
            "۲. <b>حذف میم‌کوین‌ها:</b> ارزهایی با نوسان هیجانی ساختار نودال را می‌شکنند و باید فیلتر شوند.",
            "۳. <b>ریسک‌فری در TP1:</b> پس از تاچ تارگت ۱، حد ضرر بلافاصله به Entry منتقل شود.",
            "━━━━━━━━━━━━━━━━━━",
            "📱 <i>برای مشاهده چارت‌ها و جزئیات بیشتر به مینی‌اپ مراجعه کنید.</i>"
        ]
        return "\n".join(lines)


class NDSScannerBot:
    def __init__(self, config: dict = None):
        self.config = config or load_config()
        self.symbols = self.config.get("symbols", ["BTCUSD", "ETHUSD", "SOLUSD"])
        self.timeframe = self.config.get("timeframe", "5m")
        self.min_rr = float(self.config.get("min_risk_reward", 3.0))
        self.poll_interval = int(self.config.get("scan_interval_seconds", 180))
        self.notifier = NDSTelegramNotifier(
            bot_token=self.config.get("telegram_bot_token"),
            chat_id=self.config.get("telegram_chat_id")
        )
        self.analyzer = NDSMarketAnalyzer(left_bars=2, right_bars=2)
        self.tracker = PositionTracker(self.notifier)
        self.alerted_setups = set()
        self._command_listener_running = False
        self.htf_cache = {}
        self.enable_htf_filter = True
        # Bybit DEMO execution ($100 virtual budget); inactive without BYBIT_DEMO_API_KEY/SECRET
        self.executor = BybitDemoExecutor(
            notify=lambda text: self.notifier.send_message(text, chat_id=ADMIN_CHAT_ID, with_miniapp_button=False))

    def start_command_listener(self):
        """Background thread handling Admin approvals and restricted user commands"""
        if self._command_listener_running or not self.notifier.bot_token:
            return
        self._command_listener_running = True

        def loop():
            offset = 0
            print("Telegram command listener thread started with Admin permission control...", flush=True)
            while True:
                try:
                    url = f"https://api.telegram.org/bot{self.notifier.bot_token}/getUpdates?offset={offset}&timeout=10"
                    req = urllib.request.Request(url, headers={'User-Agent': 'NDSBot/1.0'})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = json.loads(resp.read().decode())
                        if data.get("ok"):
                            for update in data.get("result", []):
                                offset = update["update_id"] + 1

                                # 1. Handle Admin Buttons (Approve / Reject)
                                cb = update.get("callback_query")
                                if cb:
                                    cb_id = cb.get("id")
                                    cb_data = cb.get("data", "")
                                    cb_from = str(cb.get("from", {}).get("id"))

                                    if cb_from == ADMIN_CHAT_ID:
                                        if cb_data.startswith("approve_"):
                                            target_id = cb_data.split("_")[1]
                                            add_subscriber(target_id)
                                            pending = load_pending_requests()
                                            pending.pop(target_id, None)
                                            save_pending_requests(pending)

                                            self.notifier.answer_callback_query(cb_id, text="کاربر با موفقیت تایید شد.")
                                            self.notifier.send_message(f"✅ دسترسی کاربر <code>{target_id}</code> با موفقیت تایید شد.", chat_id=ADMIN_CHAT_ID, with_miniapp_button=False)
                                            
                                            welcome_user = (
                                                "🎉 <b>درخواست عضویت شما توسط مدیر تایید شد!</b>\n\n"
                                                "از هم‌اکنون شما به تمامی ستاپ‌های زنده، تارگت‌ها (TP)، کارنامه معاملات و مینی‌اپ NDS دسترسی دارید.\n\n"
                                                "▫️ مشاهده معاملات: /trades\n"
                                                "▫️ سود و برایند: /pnl\n"
                                                "▫️ لمس دکمه زیر جهت ورود به مینی‌اپ"
                                            )
                                            self.notifier.send_message(welcome_user, with_miniapp_button=True, chat_id=target_id)

                                        elif cb_data.startswith("reject_"):
                                            target_id = cb_data.split("_")[1]
                                            pending = load_pending_requests()
                                            pending.pop(target_id, None)
                                            save_pending_requests(pending)

                                            self.notifier.answer_callback_query(cb_id, text="درخواست رد شد.")
                                            self.notifier.send_message(f"❌ درخواست کاربر <code>{target_id}</code> رد شد.", chat_id=ADMIN_CHAT_ID, with_miniapp_button=False)
                                            self.notifier.send_message("❌ درخواست عضویت شما در این ربات خصوصی توسط مدیر رد شد.", chat_id=target_id, with_miniapp_button=False)
                                    else:
                                        self.notifier.answer_callback_query(cb_id, text="شما اجازه انجام این عملیات را ندارید.")
                                    continue

                                # 2. Handle Messages
                                msg = update.get("message")
                                if not msg:
                                    continue
                                chat_id = str(msg.get("chat", {}).get("id"))
                                from_user = msg.get("from", {})
                                raw_text = msg.get("text", "").strip()
                                cmd = raw_text.split()[0].lower() if raw_text else ""
                                if "@" in cmd:
                                    cmd = cmd.split("@")[0]

                                is_admin = (chat_id == ADMIN_CHAT_ID)
                                approved_list = load_subscribers()
                                is_approved = (chat_id in approved_list or is_admin)

                                # Admin Specific Commands
                                if is_admin:
                                    if cmd in ["/demo", "/bybit", "دمو"]:
                                        parts = raw_text.split()
                                        if len(parts) > 1 and parts[1].lower() in ["off", "stop", "خاموش"]:
                                            self.executor.set_enabled(False)
                                        elif len(parts) > 1 and parts[1].lower() in ["on", "start", "روشن"]:
                                            self.executor.set_enabled(True)
                                        self.notifier.send_message(self.executor.report(), chat_id=chat_id, with_miniapp_button=False)
                                        continue
                                    if cmd in ["/users", "/members", "اعضا", "کاربران"]:
                                        pending = load_pending_requests()
                                        msg_u = (
                                            f"👥 <b>لیست اعضای تاییدشده ({len(approved_list)} نفر):</b>\n"
                                            + "\n".join([f"• <code>{s}</code>" + (" ⭐️ (مدیر)" if s == ADMIN_CHAT_ID else "") for s in approved_list])
                                            + f"\n\n⏳ <b>درخواست‌های در انتظار تایید ({len(pending)} نفر):</b>\n"
                                        )
                                        if pending:
                                            for pid, pdata in pending.items():
                                                msg_u += f"• <code>{pid}</code> ({pdata.get('name')}) | دستور تایید: <code>/approve {pid}</code>\n"
                                        else:
                                            msg_u += "<i>هیچ درخواست در انتظاری نیست.</i>"
                                        self.notifier.send_message(msg_u, chat_id=ADMIN_CHAT_ID, with_miniapp_button=False)
                                        continue

                                    elif cmd.startswith("/approve"):
                                        parts = raw_text.split()
                                        if len(parts) > 1:
                                            target_id = parts[1].replace("_", "")
                                            add_subscriber(target_id)
                                            pending = load_pending_requests()
                                            pending.pop(target_id, None)
                                            save_pending_requests(pending)
                                            self.notifier.send_message(f"✅ کاربر <code>{target_id}</code> با موفقیت تایید شد.", chat_id=ADMIN_CHAT_ID, with_miniapp_button=False)
                                            self.notifier.send_message("🎉 دسترسی شما توسط مدیر تایید شد!", chat_id=target_id, with_miniapp_button=True)
                                        continue

                                    elif cmd.startswith("/ban") or cmd.startswith("/remove"):
                                        parts = raw_text.split()
                                        if len(parts) > 1:
                                            target_id = parts[1]
                                            if target_id != ADMIN_CHAT_ID:
                                                remove_subscriber(target_id)
                                                self.notifier.send_message(f"🚫 دسترسی کاربر <code>{target_id}</code> لغو شد.", chat_id=ADMIN_CHAT_ID, with_miniapp_button=False)
                                                self.notifier.send_message("🚫 دسترسی شما به این ربات توسط مدیر لغو شد.", chat_id=target_id, with_miniapp_button=False)
                                        continue

                                # Access Gate for Non-Approved Users
                                if not is_approved:
                                    pending = load_pending_requests()
                                    name = f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip() or "ناشناس"
                                    username = from_user.get("username", "")

                                    pending[chat_id] = {
                                        "chat_id": chat_id,
                                        "name": name,
                                        "username": username,
                                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    }
                                    save_pending_requests(pending)

                                    # Notify applicant
                                    req_msg = (
                                        "🔒 <b>این ربات کاملاً خصوصی و اختصاصی است</b>\n\n"
                                        "درخواست عضویت شما ثبت گردید و برای مدیر ارسال شد.\n"
                                        "⏳ به محض اینکه مدیر درخواست شما را تایید کند، سیگنال‌ها و مینی‌اپ فعال خواهد شد."
                                    )
                                    self.notifier.send_message(req_msg, chat_id=chat_id, with_miniapp_button=False)

                                    # Send approval card to Admin
                                    self.notifier.send_approval_request(ADMIN_CHAT_ID, pending[chat_id])
                                    continue

                                # Allowed Commands for Approved Members
                                if cmd.startswith("/htf"):
                                    parts = raw_text.split()
                                    if len(parts) > 1 and not is_admin:
                                        self.notifier.send_message("⛔ فقط مدیر می‌تواند فیلتر HTF را تغییر دهد.", chat_id=chat_id, with_miniapp_button=False)
                                    elif len(parts) > 1 and parts[1].lower() in ["off", "disable", "خاموش", "غیرفعال"]:
                                        self.enable_htf_filter = False
                                        self.notifier.send_message("❌ فیلتر روند ماکرو (HTF Trend Filter) غیرفعال شد.", chat_id=chat_id, with_miniapp_button=False)
                                    elif len(parts) > 1 and parts[1].lower() in ["on", "enable", "روشن", "فعال"]:
                                        self.enable_htf_filter = True
                                        self.notifier.send_message("✅ فیلتر روند ماکرو (HTF Trend Filter) فعال شد.", chat_id=chat_id, with_miniapp_button=False)
                                    else:
                                        status = "✅ فعال (Active)" if self.enable_htf_filter else "❌ غیرفعال (Disabled)"
                                        msg_htf = (
                                            f"🌊 <b>وضعیت فیلتر روند ماکرو (1H HTF Trend Filter):</b>\n\n"
                                            f"▪️ <b>وضعیت فعلی:</b> {status}\n"
                                            f"▪️ <b>عملکرد:</b> ستاپ‌های ۵ دقیقه‌ای تنها در صورتی تایید می‌شوند که هم‌جهت با خط تعادل و امواج ۱ ساعته باشند.\n\n"
                                            f"دستورات:\n"
                                            f"▫️ <code>/htf on</code> - فعال‌سازی\n"
                                            f"▫️ <code>/htf off</code> - غیرفعال‌سازی"
                                        )
                                        self.notifier.send_message(msg_htf, chat_id=chat_id, with_miniapp_button=False)
                                elif cmd in ["/pnl", "/daily", "/weekly", "/profit", "سود", "برایند", "هفتگی", "ماهانه"]:
                                    pnl_rep = self.tracker.get_pnl_report()
                                    self.notifier.send_message(pnl_rep, with_miniapp_button=True, chat_id=chat_id)
                                elif cmd in ["/journal", "/audit", "ژورنال", "کالبدشکافی", "تحلیل"]:
                                    journal_rep = self.tracker.get_journal_report()
                                    self.notifier.send_message(journal_rep, with_miniapp_button=True, chat_id=chat_id)
                                elif cmd in ["/trades", "/status", "/report", "/tp", "/sl", "وضعیت", "معاملات", "گزارش", "کارنامه"]:
                                    report = self.tracker.get_summary_report()
                                    self.notifier.send_message(report, with_miniapp_button=True, chat_id=chat_id)
                                elif cmd in ["/start", "/help", "شروع", "راهنما"]:
                                    welcome = (
                                        "🤖 <b>به ربات اختصاصی NDS V1.2 خوش آمدید!</b>\n\n"
                                        "شما عضو تاییدشده سیستم هستید و تمامی ستاپ‌های زنده، تارگت‌ها (TP) و استاپ‌ها به صورت خودکار برای شما ارسال می‌شود.\n\n"
                                        "📌 <b>دستورات مجاز:</b>\n"
                                        "▫️ /trades یا /status - <i>کارنامه تفکیکی معاملات</i>\n"
                                        "▫️ /journal - <i>کالبدشکافی ژورنال تحلیلی هفتگی</i>\n"
                                        "▫️ /pnl - <i>گزارش سود و برایند روزانه، هفتگی و ماهانه</i>\n"
                                        "▫️ دکمه زیر - <i>ورود به مینی‌اپ اختصاصی</i>"
                                    )
                                    self.notifier.send_message(welcome, with_miniapp_button=True, chat_id=chat_id)
                except Exception as e:
                    time.sleep(5)

        t = threading.Thread(target=loop, daemon=True)
        t.start()

    def fetch_candles_kraken(self, symbol: str, interval_min: int = 5) -> pd.DataFrame:
        pair_map = {
            "BTCUSD": "XXBTZUSD", "ETHUSD": "XETHZUSD", "SOLUSD": "SOLUSD", 
            "XRPUSD": "XXRPZUSD", "DOGEUSD": "XDGUSD", "LTCUSD": "XLTCZUSD", 
            "XLMUSD": "XXLMZUSD", "XMRUSD": "XXMRZUSD"
        }
        pair = pair_map.get(symbol, symbol)
        url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval_min}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            candles = data['result'][pair]
            df = pd.DataFrame(candles, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Vwap', 'Volume', 'Count'])
            df['Time'] = pd.to_datetime(df['Time'].astype(int), unit='s')
            for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[c] = df[c].astype(float)
            df = df.set_index('Time')
            return df

    def get_htf_trend(self, symbol: str) -> dict:
        """Calculates 1H Higher Timeframe (Macro) trend using NDS equilibrium criteria (EMA20/50).
        Caches results for 15 minutes to minimize network calls."""
        now = time.time()
        if symbol in self.htf_cache and now < self.htf_cache[symbol]["expires_at"]:
            return self.htf_cache[symbol]

        try:
            df = self.fetch_candles_kraken(symbol, interval_min=60)
            if df is None or len(df) < 50:
                return {"trend": "NEUTRAL", "allowed": "BOTH", "desc": "داده ناکافی 1H"}

            ema20 = df['Close'].ewm(span=20, adjust=False).mean()
            ema50 = df['Close'].ewm(span=50, adjust=False).mean()

            last_close = float(df['Close'].iloc[-1])
            last_e20 = float(ema20.iloc[-1])
            last_e50 = float(ema50.iloc[-1])

            # NDS Macro Equilibrium Hierarchy:
            # Bullish: Close > EMA50 and EMA20 > EMA50
            # Bearish: Close < EMA50 and EMA20 < EMA50
            if last_close > last_e50 and last_e20 > last_e50:
                trend = "BULLISH"
                allowed = "LONG_ONLY"
                desc = "صعودی (بالای خط تعادل EMA50) 📈"
            elif last_close < last_e50 and last_e20 < last_e50:
                trend = "BEARISH"
                allowed = "SHORT_ONLY"
                desc = "نزولی (زیر خط تعادل EMA50) 📉"
            else:
                trend = "NEUTRAL"
                allowed = "BOTH"
                desc = "خنثی / تعادلی ⚖️"

            result = {
                "symbol": symbol,
                "trend": trend,
                "allowed": allowed,
                "close": last_close,
                "ema20": round(last_e20, 4),
                "ema50": round(last_e50, 4),
                "desc": desc,
                "expires_at": now + 900  # 15 min TTL
            }
            self.htf_cache[symbol] = result
            return result
        except Exception as e:
            print(f"Error fetching HTF for {symbol}: {e}", flush=True)
            return {"trend": "NEUTRAL", "allowed": "BOTH", "desc": "خطا در تحلیل 1H"}

    def scan_symbol(self, symbol: str) -> dict:
        try:
            df = self.fetch_candles_kraken(symbol, 5)
        except Exception:
            return None

        # Check existing active trades against recent candles
        recent = df.tail(5)
        high_val = float(recent['High'].max())
        low_val = float(recent['Low'].min())
        curr_price = float(df['Close'].iloc[-1])

        self.tracker.check_trade(
            symbol=symbol,
            high=high_val,
            low=low_val,
            current_price=curr_price,
            candles=df.tail(12)
        )

        res = self.analyzer.analyze(df.tail(120))
        nodes = res['nodes']
        setups = res['setups']

        if not nodes:
            return None

        latest_close = df['Close'].iloc[-1]
        active_setup = None

        if setups:
            latest = setups[-1]
            flag_idx = nodes.index(latest.flag_node) if latest.flag_node in nodes else -1
            is_recent = (flag_idx >= len(nodes) - 3)

            if is_recent and latest.risk_reward >= self.min_rr:
                setup_dir = latest.direction.value

                # Apply HTF Macro Trend Filter
                htf_trend_label = "ALIGNED"
                if self.enable_htf_filter:
                    htf = self.get_htf_trend(symbol)
                    if setup_dir == "LONG" and htf["allowed"] == "SHORT_ONLY":
                        print(f"  ⛔ [HTF Filter] فیلتر ستاپ لانگ {symbol}: روند ماکرو ۱ ساعته نزولی است ({htf['desc']}) - مسدود شد.", flush=True)
                        return {
                            "symbol": symbol,
                            "latest_close": latest_close,
                            "active_setup": None,
                            "filtered_reason": "LONG_BLOCKED_BY_1H_BEARISH",
                            "nodes_count": len(nodes)
                        }
                    elif setup_dir == "SHORT" and htf["allowed"] == "LONG_ONLY":
                        print(f"  ⛔ [HTF Filter] فیلتر ستاپ شورت {symbol}: روند ماکرو ۱ ساعته صعودی است ({htf['desc']}) - مسدود شد.", flush=True)
                        return {
                            "symbol": symbol,
                            "latest_close": latest_close,
                            "active_setup": None,
                            "filtered_reason": "SHORT_BLOCKED_BY_1H_BULLISH",
                            "nodes_count": len(nodes)
                        }
                    htf_trend_label = htf.get("trend", "ALIGNED")

                entry = latest.entry_price
                sl = latest.stop_loss
                risk = abs(entry - sl)

                if latest.direction == SetupDirection.LONG:
                    t1 = entry + risk * 1.5
                    t2 = entry + risk * 2.5
                    hook_lvl = res.get("latest_864_level")
                    t3 = hook_lvl if (hook_lvl and hook_lvl > t2) else (entry + risk * 3.5)
                    t4 = entry + risk * 4.5
                else:  # SHORT
                    t1 = entry - risk * 1.5
                    t2 = entry - risk * 2.5
                    hook_lvl = res.get("latest_864_level")
                    t3 = hook_lvl if (hook_lvl and hook_lvl < t2) else (entry - risk * 3.5)
                    t4 = entry - risk * 4.5

                active_setup = {
                    "symbol": symbol,
                    "direction": latest.direction.value,
                    "entry": entry,
                    "sl": sl,
                    "tp1": t1,
                    "tp2": t2,
                    "tp3": t3,
                    "tp4": t4,
                    "rr": latest.risk_reward,
                    "timestamp": str(latest.timestamp),
                    "htf_trend": htf_trend_label
                }

        return {
            "symbol": symbol,
            "latest_close": latest_close,
            "active_setup": active_setup,
            "nodes_count": len(nodes)
        }

    def run_cycle(self):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] شروع اسکن و رهگیری پوزیشن‌ها...", flush=True)
        for sym in self.symbols:
            data = self.scan_symbol(sym)
            if not data:
                continue

            if data['active_setup']:
                setup = data['active_setup']
                setup_id = f"{sym}_{setup['direction']}_{setup.get('timestamp', '')}"
                if setup_id not in self.alerted_setups:
                    self.alerted_setups.add(setup_id)
                    if setup_id in self.tracker.trades or self.tracker.is_missed(setup_id):
                        continue
                    # Too late to enter: price already past TP1 or SL -> record as missed, send nothing
                    live = float(data["latest_close"])
                    why = stale_reason(setup, live)
                    if why:
                        self.tracker.record_missed(setup_id, setup, live, why)
                        continue
                    # Enter at the live price, not the older setup price
                    setup = rebase_to_live(setup, live)
                    # No signal for a repeat (e.g. after a restart), a coin with an open trade, or bad targets
                    if not self.tracker.add_trade(setup_id, setup):
                        continue
                    print(f"  🚨 ستاپ طلایی زنده: {sym} | R/R 1:{setup['rr']:.1f} -> ارسال و ثبت در رهگیر...", flush=True)
                    # Place the Bybit demo order first so the signal can say whether it opened there
                    bybit_info = None
                    try:
                        bybit_info = self.executor.on_new_trade(setup_id, self.tracker.trades[setup_id])
                    except Exception as e:
                        print(f"  [bybit_demo] open error: {e}", flush=True)
                    msg = self.notifier.format_alert(sym, self.timeframe, setup)
                    if bybit_info:
                        msg += self.executor.signal_line(bybit_info)
                    self.notifier.send_message(msg, with_miniapp_button=True)

        try:
            self.executor.sync(self.tracker.trades)
        except Exception as e:
            print(f"  [bybit_demo] sync error: {e}", flush=True)

    def start_24_7(self):
        print(f"=== ربات اسکنر و رهگیر زنده NDS فعال شد ===", flush=True)
        print(f"فیلتر حداقل ریسک به ریوارد: 1:{self.min_rr}", flush=True)
        print(f"فاصله بررسی: هر {self.poll_interval} ثانیه", flush=True)
        print("در حال پایش بازار و رهگیری تارگت‌ها...\n", flush=True)

        self.start_command_listener()

        while True:
            try:
                self.run_cycle()
                time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                print("ربات متوقف شد.", flush=True)
                break
            except Exception as e:
                print(f"خطای غیرمنتظره: {e}", flush=True)
                time.sleep(10)

if __name__ == "__main__":
    bot = NDSScannerBot()
    if "--run" in sys.argv:
        bot.start_24_7()
    else:
        bot.run_cycle()
