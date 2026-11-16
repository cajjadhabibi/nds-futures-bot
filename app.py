import os
import sys
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# Import NDS Scanner Bot
from nds_engine.nds_telegram_bot import NDSScannerBot
from nds_engine import cloud_store
import signal

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/health'):
            body = json.dumps({"status": "ok", "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                               "persistence": cloud_store.enabled()}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        trades_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nds_engine", "active_trades.json")
        trades_data = {}
        if os.path.exists(trades_path):
            try:
                with open(trades_path, 'r', encoding='utf-8') as f:
                    trades_data = json.load(f)
            except Exception:
                pass

        tp_hits = sum(1 for t in trades_data.values() if t.get("tp1_hit") or t.get("tp2_hit") or t.get("tp3_hit"))
        sl_hits = sum(1 for t in trades_data.values() if t.get("sl_hit"))
        active = sum(1 for t in trades_data.values() if not t.get("is_closed"))

        res = {
            "status": "online",
            "service": "NDS 1.2 Futures 30-Coin Scanner & Telegram Bot",
            "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "miniapp_url": "https://nds-futures-market-scanner-telegram-mini-app-for-x5vpqy4cnn.openbot.site",
            "summary": {
                "total_trades": len(trades_data),
                "tp_hit_count": tp_hits,
                "sl_hit_count": sl_hits,
                "active_count": active
            },
            "trades": trades_data
        }
        self.wfile.write(json.dumps(res, ensure_ascii=False, indent=2).encode('utf-8'))

    def log_message(self, format, *args):
        # Quiet HTTP access logs
        pass

def run_web_server(port: int = 10000):
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"Health-check & API web server running on port {port}...", flush=True)
    server.serve_forever()

def start_nds_background():
    print("Starting NDS 30-Coin 24/7 Scanner thread...", flush=True)
    bot = NDSScannerBot()
    bot.start_24_7()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    # 0. Restore trades/subscribers from the private GitHub data repo (Render disk is wiped on every deploy)
    try:
        cloud_store.restore()
    except Exception as e:
        print(f"[cloud_store] restore error: {e}", flush=True)
    cloud_store.start_sync()
    cloud_store.start_keepalive()

    def _on_term(signum, frame):
        print("SIGTERM received - saving data before shutdown...", flush=True)
        try:
            cloud_store.flush("shutdown")
        finally:
            os._exit(0)
    signal.signal(signal.SIGTERM, _on_term)
    
    # 1. Start Scanner & Position Tracker in background thread
    t = threading.Thread(target=start_nds_background, daemon=True)
    t.start()
    
    # 2. Start HTTP Health-check server on main thread for Render
    run_web_server(port)
