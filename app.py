import os
import sys
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# Import NDS Scanner Bot
from nds_engine.nds_telegram_bot import NDSScannerBot

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        res = {
            "status": "online",
            "service": "NDS 1.2 Futures 30-Coin Scanner & Telegram Bot",
            "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "miniapp_url": "https://nds-futures-market-scanner-telegram-mini-app-for-x5vpqy4cnn.openbot.site"
        }
        self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))

    def log_message(self, format, *args):
        # Quiet HTTP access logs
        pass

def run_web_server(port: int = 10000):
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"Health-check web server running on port {port}...", flush=True)
    server.serve_forever()

def start_nds_background():
    print("Starting NDS 30-Coin 24/7 Scanner thread...", flush=True)
    # Ensure config picks up env vars
    bot = NDSScannerBot()
    bot.start_24_7()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    # 1. Start Scanner in background thread
    t = threading.Thread(target=start_nds_background, daemon=True)
    t.start()
    
    # 2. Start HTTP Health-check server on main thread for Render
    run_web_server(port)
