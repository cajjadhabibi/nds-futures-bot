import os
import sys
import time
import json
import threading
import urllib.request
import urllib.parse
import pandas as pd
from datetime import datetime

# Add workspace to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nds_engine.analyzer import NDSMarketAnalyzer
from nds_engine.node_detector import NodeType
from nds_engine.flag_setup import SetupDirection

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_config.json")
TRADES_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "active_trades.json")
SUBSCRIBERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscribers.json")
MINI_APP_URL = "https://nds-futures-market-scanner-telegram-mini-app-for-x5vpqy4cnn.openbot.site"

def load_config():
    cfg = {
        "telegram_chat_id": "",
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

def load_subscribers(default_chat_id: str = "") -> list:
    subs = []
    if os.path.exists(SUBSCRIBERS_PATH):
        try:
            with open(SUBSCRIBERS_PATH, "r", encoding="utf-8") as f:
                subs = json.load(f)
        except Exception:
            subs = []
    if default_chat_id and str(default_chat_id) not in subs:
        subs.append(str(default_chat_id))
    return subs

def add_subscriber(chat_id: str):
    subs = load_subscribers()
    cid = str(chat_id)
    if cid not in subs:
        subs.append(cid)
        try:
            with open(SUBSCRIBERS_PATH, "w", encoding="utf-8") as f:
                json.dump(subs, f, indent=2)
            print(f"Added new subscriber: {cid}")
        except Exception as e:
            print(f"Error saving subscriber: {e}")

def split_text(text: str, max_chars: int = 3800) -> list:
    """Split message cleanly across lines to never exceed Telegram 4096 char limit"""
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

class NDSTelegramNotifier:
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token
        self.chat_id = str(chat_id) if chat_id else ""
        if self.chat_id:
            add_subscriber(self.chat_id)

    def send_message(self, text: str, parse_mode: str = "HTML", with_miniapp_button: bool = True, chat_id: str = None) -> bool:
        """Send message safely handling character limits and markdown fallback"""
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

                # Only attach MiniApp button on the final chunk
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
                    print(f"Error sending chunk {idx+1}/{len(chunks)} to {tid}: {e.code}")
                    # If HTML parsing failed, retry plain text
                    if parse_mode:
                        try:
                            payload.pop("parse_mode", None)
                            req_retry = urllib.request.Request(
                                url,
                                data=json.dumps(payload).encode('utf-8'),
                                headers={'Content-Type': 'application/json', 'User-Agent': 'NDSBot/1.0'}
                            )
                            with urllib.request.urlopen(req_retry, timeout=10) as resp_retry:
                                res_retry = json.loads(resp_retry.read().decode())
                                if res_retry.get("ok"):
                                    success = True
                        except Exception as e2:
                            print(f"Plain text retry failed: {e2}")
                except Exception as e:
                    print(f"Connection error to {tid}: {e}")

        if success:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] پیام ({len(chunks)} بخش) به {len(targets)} کاربر تلگرام ارسال شد.")
        return success

    def format_alert(self, symbol: str, timeframe: str, setup_data: dict) -> str:
        direction = setup_data['direction']
        dir_text = "🟢 LONG (خرید)" if direction == "LONG" else "🔴 SHORT (فروش)"
        emoji = "📈" if direction == "LONG" else "📉"

        msg = (
            f"<b>{emoji} موقعیت طلایی NDS با R/R حداقل ۱:۳ {emoji}</b>\n\n"
            f"▪️ <b>نماد:</b> {symbol}\n"
            f"▪️ <b>تایم‌فریم:</b> {timeframe}\n"
            f"▪️ <b>ستاپ:</b> 123Flag Setup\n"
            f"▪️ <b>جهت معامله:</b> {dir_text}\n\n"
            f"📍 <b>نقطه ورود (Entry):</b> <code>{setup_data['entry']:.4f}</code>\n"
            f"🛑 <b>حد ضرر (Stop Loss):</b> <code>{setup_data['sl']:.4f}</code>\n\n"
            f"🎯 <b>تارگت اول (TP1):</b> <code>{setup_data['tp1']:.4f}</code>\n"
            f"🎯 <b>تارگت دوم (TP2):</b> <code>{setup_data['tp2']:.4f}</code>\n"
            f"🎯 <b>تارگت طلایی (TP3 - ۸۶.۴٪):</b> <code>{setup_data['tp3']:.4f}</code>\n"
            f"🎯 <b>تارگت نهایی (TP4 - چرخه):</b> <code>{setup_data.get('tp4', 0.0):.4f}</code>\n\n"
            f"💎 <b>نسبت سود به ریسک (R/R):</b> <code>1:{setup_data['rr']:.1f}</code> ✅\n"
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
            f"▫️ <b>میزان سود:</b> <code>+{profit:.4f} $</code> (<b>+{pct:.2f}%</b>)\n\n"
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
            f"<i>ℹ️ به دلیل تاچ شدن تارگت اول در گذشته، سود تارگت قبلی حفظ شد و معامله بدون ضرر بسته شد.</i>"
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
            f"▫️ <b>میزان ضرر:</b> <code>-{loss:.4f} $</code> (-{pct:.2f}%)\n\n"
            f"<i>ℹ️ پوزیشن طبق مدیریت سرمایه NDS بسته شد. بازار برای امواج و نودهای بعدی رصد می‌شود.</i>"
        )
        return msg


class PositionTracker:
    def __init__(self, notifier: NDSTelegramNotifier, db_path: str = TRADES_DB_PATH):
        self.notifier = notifier
        self.db_path = db_path
        self.trades = self.load_trades()

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

    def add_trade(self, setup_id: str, data: dict):
        if setup_id not in self.trades:
            self.trades[setup_id] = {
                "id": setup_id,
                "symbol": data["symbol"],
                "direction": data["direction"],
                "entry": float(data["entry"]),
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
                "history": []
            }
            self.save_trades()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] پوزیشن زنده {setup_id} ثبت شد.")

    def check_trade(self, symbol: str, high: float, low: float, current_price: float):
        for trade_id, t in list(self.trades.items()):
            if t["symbol"] != symbol or t.get("is_closed"):
                continue

            direction = t["direction"]
            now_str = datetime.now().strftime('%H:%M:%S')
            entry = t["entry"]

            # LONG Check
            if direction == "LONG":
                # Check TP1
                if not t["tp1_hit"] and high >= t["tp1"]:
                    t["tp1_hit"] = True
                    t["is_risk_free"] = True
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

                # Check Risk-Free exit (if TP1 hit and price returns to entry)
                elif t.get("is_risk_free") and low <= entry:
                    t["is_closed"] = True
                    t["close_reason"] = "RISK_FREE"
                    t["history"].append(f"خروج در نقطه ورود (ریسک‌فری) در {now_str}")
                    msg = self.notifier.format_risk_free_hit(t, entry)
                    self.notifier.send_message(msg)

                # Check Initial SL (only if TP1 not hit yet)
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

                # Check Risk-Free exit (if TP1 hit and price returns to entry)
                elif t.get("is_risk_free") and high >= entry:
                    t["is_closed"] = True
                    t["close_reason"] = "RISK_FREE"
                    t["history"].append(f"خروج در نقطه ورود (ریسک‌فری) در {now_str}")
                    msg = self.notifier.format_risk_free_hit(t, entry)
                    self.notifier.send_message(msg)

                # Check Initial SL (only if TP1 not hit yet)
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

    def start_command_listener(self):
        """Background thread listening to user commands (/trades, /status, /start) on Telegram"""
        if self._command_listener_running or not self.notifier.bot_token:
            return
        self._command_listener_running = True

        def loop():
            offset = 0
            print("Telegram command listener thread started (/trades, /status, /report)...", flush=True)
            while True:
                try:
                    url = f"https://api.telegram.org/bot{self.notifier.bot_token}/getUpdates?offset={offset}&timeout=10"
                    req = urllib.request.Request(url, headers={'User-Agent': 'NDSBot/1.0'})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = json.loads(resp.read().decode())
                        if data.get("ok"):
                            for update in data.get("result", []):
                                offset = update["update_id"] + 1
                                msg = update.get("message")
                                if not msg:
                                    continue
                                chat_id = str(msg.get("chat", {}).get("id"))
                                raw_text = msg.get("text", "").strip()
                                cmd = raw_text.split()[0].lower() if raw_text else ""
                                if "@" in cmd:
                                    cmd = cmd.split("@")[0]

                                # Auto subscribe user
                                add_subscriber(chat_id)

                                if cmd in ["/trades", "/status", "/report", "/tp", "/sl", "وضعیت", "معاملات", "گزارش", "کارنامه"]:
                                    report = self.tracker.get_summary_report()
                                    self.notifier.send_message(report, with_miniapp_button=True, chat_id=chat_id)
                                elif cmd in ["/start", "/help", "شروع", "راهنما"]:
                                    welcome = (
                                        "🤖 <b>به ربات اسکنر و مینی‌اپ تحلیلی NDS V1.2 خوش آمدید!</b>\n\n"
                                        "این ربات بازار ۳۰ ارز برتر فیوچرز را برای ستاپ‌های 123Flag با R/R حداقل ۱:۳ پایش کرده و اهداف سود و خروج را رهگیری می‌کند.\n\n"
                                        "📌 <b>دستورات در دسترس:</b>\n"
                                        "▫️ /trades یا /status - <i>مشاهده کارنامه معاملات و پوزیشن‌های باز و سودده</i>\n"
                                        "▫️ /report - <i>آمار وین‌ریت و تعداد تارگت‌های تاچ‌شده</i>\n"
                                        "▫️ دکمه زیر - <i>ورود به مینی‌اپ با نمودارهای چرخه‌ای زنده</i>\n\n"
                                        "✨ تمام ستاپ‌ها و لمس تارگت‌ها به صورت لحظه‌ای ارسال می‌شوند."
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
            current_price=curr_price
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
            # Must be recent swing node
            flag_idx = nodes.index(latest.flag_node) if latest.flag_node in nodes else -1
            is_recent = (flag_idx >= len(nodes) - 3)

            if is_recent and latest.risk_reward >= self.min_rr:
                entry = latest.entry_price
                sl = latest.stop_loss
                risk = abs(entry - sl)

                # Strictly ordered targets based on trade direction
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
                    "timestamp": str(latest.timestamp)
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
                    print(f"  🚨 ستاپ طلایی زنده: {sym} | R/R 1:{setup['rr']:.1f} -> ارسال و ثبت در رهگیر...", flush=True)
                    self.tracker.add_trade(setup_id, setup)
                    msg = self.notifier.format_alert(sym, self.timeframe, setup)
                    self.notifier.send_message(msg, with_miniapp_button=True)

    def start_24_7(self):
        print(f"=== ربات اسکنر و رهگیر زنده NDS فعال شد ===", flush=True)
        print(f"فیلتر حداقل ریسک به ریوارد: 1:{self.min_rr}", flush=True)
        print(f"فاصله بررسی: هر {self.poll_interval} ثانیه", flush=True)
        print("در حال پایش بازار و رهگیری تارگت‌ها...\n", flush=True)

        # Start interactive command listener
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
