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

class NDSTelegramNotifier:
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token
        self.chat_id = str(chat_id) if chat_id else ""

    def send_message(self, text: str, parse_mode: str = "HTML", with_miniapp_button: bool = True, chat_id: str = None) -> bool:
        """Send message to Telegram chat with optional WebApp button"""
        target_chat = str(chat_id) if chat_id else self.chat_id
        if not self.bot_token or not target_chat:
            print(f"\n[ALERT GENERATED - DRY RUN (Missing token or chat_id)]:\n{text}\n")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": parse_mode
        }
        if with_miniapp_button:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {"text": "📊 مشاهده وضعیت در مینی‌اپ", "web_app": {"url": MINI_APP_URL}}
                    ]
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
                ok = res.get("ok", False)
                if ok:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] پیام با موفقیت به تلگرام ارسال شد.")
                else:
                    print(f"Telegram response not ok: {res}")
                return ok
        except Exception as e:
            print(f"Error sending to Telegram: {e}")
            return False

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
            f"📍 <b>نقطه ورود (Entry):</b> <code>{setup_data['entry']:.2f}</code>\n"
            f"🛑 <b>حد ضرر (Stop Loss):</b> <code>{setup_data['sl']:.2f}</code>\n\n"
            f"🎯 <b>تارگت اول (نود باز ۱):</b> <code>{setup_data['tp1']:.2f}</code>\n"
            f"🎯 <b>تارگت دوم (نود باز ۲):</b> <code>{setup_data['tp2']:.2f}</code>\n"
            f"🎯 <b>تارگت طلایی (۸۶.۴٪ هوک):</b> <code>{setup_data['tp3']:.2f}</code>\n"
            f"🎯 <b>تارگت نهایی (کف/سقف چرخه):</b> <code>{setup_data.get('tp4', 0.0):.2f}</code>\n\n"
            f"💎 <b>نسبت سود به ریسک (R/R):</b> <code>1:{setup_data['rr']:.1f}</code> ✅\n"
            f"⏱ <b>زمان ستاپ:</b> {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"<i>📌 وضعیت تحقق تارگت‌ها یا استاپ به صورت لحظه‌ای گزارش می‌شود. با ارسال /trades می‌توانید کارنامه را ببینید.</i>"
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
            f"▫️ <b>تارگت محقق‌شده:</b> {target_name} (<code>{target_price:.2f}</code>)\n"
            f"▫️ <b>قیمت ورود:</b> <code>{entry:.2f}</code>\n"
            f"▫️ <b>قیمت جاری:</b> <code>{current_price:.2f}</code>\n"
            f"▫️ <b>میزان سود:</b> <code>+{profit:.2f} $</code> (<b>+{pct:.2f}%</b>)\n\n"
        )
        if is_final:
            msg += "💎 <b>نتیجه:</b> چرخه ساختار NDS کامل شد و تمامی اهداف سود محقق گردیدند."
        else:
            msg += "💡 <b>توصیه NDS:</b> استاپ لاس را به نقطه ورود (Risk-Free) منتقل نمایید تا ریسک معامله به صفر برسد."
        return msg

    def format_sl_hit(self, trade: dict, sl_price: float) -> str:
        dir_text = "LONG 🟢" if trade['direction'] == "LONG" else "SHORT 🔴"
        entry = trade['entry']
        loss = abs(sl_price - entry)
        pct = (loss / entry) * 100

        msg = (
            f"<b>🛑 حد ضرر فعال شد | STOP LOSS HIT 🛑</b>\n\n"
            f"▫️ <b>نماد:</b> {trade['symbol']}\n"
            f"▫️ <b>جهت معامله:</b> {dir_text}\n"
            f"▫️ <b>قیمت استاپ:</b> <code>{sl_price:.2f}</code>\n"
            f"▫️ <b>قیمت ورود:</b> <code>{entry:.2f}</code>\n"
            f"▫️ <b>اختلاف:</b> <code>-{loss:.2f} $</code> (-{pct:.2f}%)\n\n"
            f"<i>ℹ️ پوزیشن طبق مدیریت سرمایه NDS بسته شد. بازار برای موج و نودهای جدید رصد می‌شود.</i>"
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
                "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "history": []
            }
            self.save_trades()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] پوزیشن {setup_id} در سامانه رهگیری ثبت شد.")

    def check_trade(self, symbol: str, high: float, low: float, current_price: float):
        for trade_id, t in list(self.trades.items()):
            if t["symbol"] != symbol or t.get("is_closed"):
                continue

            direction = t["direction"]
            now_str = datetime.now().strftime('%H:%M:%S')

            # LONG Check
            if direction == "LONG":
                # Check TP1
                if not t["tp1_hit"] and high >= t["tp1"]:
                    t["tp1_hit"] = True
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

                # Check SL (only if TP3 not hit)
                elif not t["sl_hit"] and low <= t["sl"]:
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

                # Check SL
                elif not t["sl_hit"] and high >= t["sl"]:
                    t["sl_hit"] = True
                    t["is_closed"] = True
                    t["close_reason"] = "SL_HIT"
                    t["history"].append(f"حد ضرر تاچ شد در {now_str}")
                    msg = self.notifier.format_sl_hit(t, t["sl"])
                    self.notifier.send_message(msg)

            self.save_trades()

    def get_summary_report(self) -> str:
        """Detailed report of all positions: which hit TP, which hit SL, and which are active"""
        if not self.trades:
            return (
                "📊 <b>کارنامه و وضعیت لحظه‌ای معاملات NDS:</b>\n\n"
                "<i>تا کنون پوزیشنی ثبت نشده یا در حال اسکن بازار برای اولین ستاپ طلایی هستیم.</i>\n\n"
                "🔍 اسکنر ۳۰ ارز به صورت ۲۴ ساعته فعال است و به محض ورود معامله ثبت و رهگیری می‌شود."
            )

        tp_hits = []
        sl_hits = []
        active = []

        for tid, t in self.trades.items():
            sym = t['symbol']
            dir_str = "🟢 LONG" if t['direction'] == "LONG" else "🔴 SHORT"
            
            # Check target hits
            if t.get("tp1_hit") or t.get("tp2_hit") or t.get("tp3_hit"):
                hits = []
                if t.get("tp1_hit"): hits.append("TP1")
                if t.get("tp2_hit"): hits.append("TP2")
                if t.get("tp3_hit"): hits.append("TP3 (طلایی)")
                status = " + ".join(hits)
                tp_hits.append(f"• <b>{sym}</b> ({dir_str}): تاچ <b>{status}</b> ✅")
            elif t.get("sl_hit"):
                sl_hits.append(f"• <b>{sym}</b> ({dir_str}): استاپ در <code>{t['sl']:.2f}</code> فعال شد 🛑")
            elif not t.get("is_closed"):
                active.append(f"• <b>{sym}</b> ({dir_str}): ورود <code>{t['entry']:.2f}</code> | در حال حرکت ⏳")

        total = len(self.trades)
        wins = len(tp_hits)
        losses = len(sl_hits)
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

        msg = "📊 <b>کارنامه تفکیکی معاملات NDS (بررسی دقیق TP و SL):</b>\n\n"

        if tp_hits:
            msg += "🎯 <b>پوزیشن‌های تارگت‌خورده (سودده):</b>\n" + "\n".join(tp_hits) + "\n\n"
        else:
            msg += "🎯 <b>پوزیشن‌های تارگت‌خورده:</b> هنوز موردی تارگت نزده است.\n\n"

        if sl_hits:
            msg += "🛑 <b>پوزیشن‌های استاپ‌خورده:</b>\n" + "\n".join(sl_hits) + "\n\n"
        else:
            msg += "🛑 <b>پوزیشن‌های استاپ‌خورده:</b> هیچ استاپی فعال نشده است ✅\n\n"

        if active:
            msg += "⏳ <b>پوزیشن‌های باز (در حال حرکت به سمت تارگت):</b>\n" + "\n".join(active) + "\n\n"

        msg += (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>نرخ موفقیت (Win Rate):</b> <code>{win_rate:.1f}%</code>\n"
            f"🔢 <b>کل موقعیت‌های رصد شده:</b> {total}\n"
            f"⏱ <b>بروزرسانی:</b> {datetime.now().strftime('%H:%M:%S')}"
        )
        return msg


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
                                text = msg.get("text", "").strip().lower()

                                if text in ["/trades", "/status", "/report", "/tp", "/sl", "وضعیت", "معاملات", "گزارش"]:
                                    report = self.tracker.get_summary_report()
                                    self.notifier.send_message(report, with_miniapp_button=True, chat_id=chat_id)
                                elif text in ["/start", "/help", "شروع", "راهنما"]:
                                    welcome = (
                                        "🤖 <b>ربات اسکنر و رهگیر زنده معاملات NDS V1.2</b>\n\n"
                                        "این ربات بازار ۳۰ ارز برتر را برای ستاپ‌های 123Flag با R/R حداقل ۱:۳ اسکن کرده و تمام مراحل معامله را تا لمس تارگت‌ها یا استاپ پیگیری می‌کند.\n\n"
                                        "📌 <b>دستورات:</b>\n"
                                        "▫️ /trades یا /status - <i>کارنامه تفکیکی معاملات (کدام TP خورده، کدام استاپ خورده)</i>\n"
                                        "▫️ /report - <i>آمار کلی و وین‌ریت</i>\n\n"
                                        "✨ هر زمان تارگتی تاچ شود، پیام اختصاصی آن برای شما ارسال می‌شود."
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

        # Robust check of recent candles (last 5 candles = 25 minutes) for any TP / SL touch
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
        peaks = [n for n in nodes if n.node_type == NodeType.PEAK]
        valleys = [n for n in nodes if n.node_type == NodeType.VALLEY]

        active_setup = None
        if setups:
            latest = setups[-1]
            if latest.risk_reward >= self.min_rr:
                active_setup = {
                    "symbol": symbol,
                    "direction": latest.direction.value,
                    "entry": latest.entry_price,
                    "sl": latest.stop_loss,
                    "tp1": latest.take_profit_1,
                    "tp2": latest.take_profit_2,
                    "tp3": res.get("latest_864_level") or (latest.entry_price * 0.99),
                    "tp4": valleys[-1].price if latest.direction == SetupDirection.SHORT else peaks[-1].price,
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
                setup_id = f"{sym}_{setup['direction']}_{setup['entry']:.1f}"
                if setup_id not in self.alerted_setups:
                    self.alerted_setups.add(setup_id)
                    print(f"  🚨 ستاپ طلایی جدید: {sym} | R/R 1:{setup['rr']:.1f} -> ارسال و ثبت در رهگیر...", flush=True)
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
