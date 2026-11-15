import os
import sys
import time
import json
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
MINI_APP_URL = "https://nds-futures-market-scanner-telegram-mini-app-for-x5vpqy4cnn.openbot.site"

def load_config():
    cfg = {
        "telegram_chat_id": "",
        "symbols": ["BTCUSD", "ETHUSD", "SOLUSD"],
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

    cfg["telegram_bot_token"] = token or cfg.get("telegram_bot_token", "")
    return cfg

class NDSTelegramNotifier:
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_message(self, text: str, parse_mode: str = "HTML", with_miniapp_button: bool = True) -> bool:
        """Send message to Telegram chat with optional WebApp button"""
        if not self.bot_token or not self.chat_id:
            print(f"\n[ALERT GENERATED - DRY RUN (Missing token or chat_id)]:\n{text}\n")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if with_miniapp_button:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": "📊 مشاهده جزئیات در مینی‌اپ", "web_app": {"url": MINI_APP_URL}}]
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
        """Format clean Persian alert message for 1:3+ setups"""
        direction = setup_data['direction']
        dir_text = "🟢 LONG (خرید)" if direction == "LONG" else "🔴 SHORT (فروش)"
        emoji = "📈" if direction == "LONG" else "📉"

        msg = (
            f"<b>{emoji} موقعیت طلایی NDS با R/R حداقل ۱:۳ {emoji}</b>\n\n"
            f"▫️ <b>نماد:</b> {symbol}\n"
            f"▫️ <b>تایم‌فریم:</b> {timeframe}\n"
            f"▫️ <b>ستاپ:</b> 123Flag Setup\n"
            f"▫️ <b>جهت معامله:</b> {dir_text}\n\n"
            f"📍 <b>نقطه ورود (Entry):</b> <code>{setup_data['entry']:.2f}</code>\n"
            f"🛑 <b>حد ضرر (Stop Loss):</b> <code>{setup_data['sl']:.2f}</code>\n\n"
            f"🎯 <b>تارگت اول (نود باز ۱):</b> <code>{setup_data['tp1']:.2f}</code>\n"
            f"🎯 <b>تارگت دوم (نود باز ۲):</b> <code>{setup_data['tp2']:.2f}</code>\n"
            f"🎯 <b>تارگت طلایی (۸۶.۴٪ هوک):</b> <code>{setup_data['tp3']:.2f}</code>\n"
            f"🎯 <b>تارگت نهایی (کف/سقف چرخه):</b> <code>{setup_data.get('tp4', 0.0):.2f}</code>\n\n"
            f"💎 <b>نسبت سود به ریسک (R/R):</b> <code>1:{setup_data['rr']:.1f}</code> ✅\n"
            f"⏱ <b>زمان ستاپ:</b> {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"<i>📌 استراتژی خروج: پس از تاچ تارگت اول، پوزیشن را به نقطه ورود ریسک‌فری کنید و تا تراز ۸۶.۴٪ با معامله همراه باشید.</i>"
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
        self.alerted_setups = set()

    def fetch_candles_kraken(self, symbol: str, interval_min: int = 5) -> pd.DataFrame:
        pair_map = {"BTCUSD": "XXBTZUSD", "ETHUSD": "XETHZUSD", "SOLUSD": "SOLUSD", "XRPUSD": "XXRPZUSD", "DOGEUSD": "XDGUSD", "LTCUSD": "XLTCZUSD", "XLMUSD": "XXLMZUSD", "XMRUSD": "XXMRZUSD"}
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
            df.set_index('Time', inplace=True)
            return df

    def scan_symbol(self, symbol: str) -> dict:
        try:
            df = self.fetch_candles_kraken(symbol, 5)
        except Exception as e:
            # Fallback or quiet on unsupported pair
            return None

        res = self.analyzer.analyze(df.tail(120))
        nodes = res['nodes']
        setups = res['setups']
        hooks = res['hooks']

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
        print(f"[{timestamp}] شروع اسکن دوره‌ای ۳۰ ارز...", flush=True)
        for sym in self.symbols:
            data = self.scan_symbol(sym)
            if not data:
                continue

            print(f"  • {sym:<8}: قیمت {data['latest_close']:.4f} | نودها: {data['nodes_count']}", flush=True)
            if data['active_setup']:
                setup = data['active_setup']
                setup_id = f"{sym}_{setup['direction']}_{setup['entry']:.1f}"
                if setup_id not in self.alerted_setups:
                    self.alerted_setups.add(setup_id)
                    print(f"  🚨 ستاپ طلایی کشف شد! {sym} - R/R = 1:{setup['rr']:.1f} -> ارسال ناتیفیکیشن...", flush=True)
                    msg = self.notifier.format_alert(sym, self.timeframe, setup)
                    self.notifier.send_message(msg, with_miniapp_button=True)

    def start_24_7(self):
        print(f"=== ربات اسکنر ۲۴ ساعته ۳۰ ارز NDS فعال شد ===", flush=True)
        print(f"فیلتر حداقل ریسک به ریوارد: 1:{self.min_rr}", flush=True)
        print(f"فاصله بررسی: هر {self.poll_interval} ثانیه", flush=True)
        print(f"تعداد نمادها: {len(self.symbols)} ارز پرحجم", flush=True)
        print("در حال پایش بدون وقفه بازار...\n", flush=True)

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
