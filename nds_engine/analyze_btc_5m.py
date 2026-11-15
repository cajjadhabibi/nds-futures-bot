import urllib.request
import json
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, r"C:\Users\sajjad\OpenBot\Agents\agent-12c7a32e-1779-4fbe-add5-333a137ff056")
from nds_engine.node_detector import NodeDetector, NodeType
from nds_engine.sequencing import SequenceEngine
from nds_engine.hooks import HookDetector
from nds_engine.flag_setup import FlagSetupDetector

# Fetch 5m candles from Kraken
url_5m = 'https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=5'
req = urllib.request.Request(url_5m, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read().decode())
    candles = data['result']['XXBTZUSD']
    df_5m = pd.DataFrame(candles, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Vwap', 'Volume', 'Count'])
    df_5m['Time'] = pd.to_datetime(df_5m['Time'].astype(int), unit='s')
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df_5m[col] = df_5m[col].astype(float)
    df_5m.set_index('Time', inplace=True)

# Fetch 1h candles from Kraken
url_1h = 'https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60'
req = urllib.request.Request(url_1h, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read().decode())
    candles_1h = data['result']['XXBTZUSD']
    df_1h = pd.DataFrame(candles_1h, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Vwap', 'Volume', 'Count'])
    df_1h['Time'] = pd.to_datetime(df_1h['Time'].astype(int), unit='s')
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df_1h[col] = df_1h[col].astype(float)
    df_1h.set_index('Time', inplace=True)

latest_close = df_5m['Close'].iloc[-1]
latest_time = df_5m.index[-1]
print(f"Latest 5m Bar Time: {latest_time} | Open: {df_5m['Open'].iloc[-1]} | High: {df_5m['High'].iloc[-1]} | Low: {df_5m['Low'].iloc[-1]} | Close: {latest_close}")

# Analyze 5m nodes
detector = NodeDetector(left_bars=2, right_bars=2)
nodes = detector.find_raw_nodes(df_5m.tail(150))

seq_engine = SequenceEngine()
sequences = seq_engine.process_nodes(nodes)

hook_detector = HookDetector()
hooks = hook_detector.detect_hooks(nodes)

flag_detector = FlagSetupDetector()
setups = flag_detector.scan_setups(nodes)

print("\n=== RECENT 15 NODES (5m) ===")
for n in nodes[-15:]:
    t_str = str(n.timestamp)[11:16]
    print(f"Time: {t_str} | {n.node_type.value:<6} | Price: {n.price:.2f} | Num: {n.number} | Closed: {n.is_closed}")

print("\n=== RECENT HOOKS (5m) ===")
for h in hooks[-5:]:
    print(f"Hook: {h.hook_type.value} | Start: {h.start_node.price:.2f} -> Extreme: {h.extreme_node.price:.2f} | 86.4%: {h.level_864:.2f} | Completed: {h.is_completed}")

print("\n=== DETECTED SETUPS (5m) ===")
for s in setups[-5:]:
    print(f"Setup: {s.direction.value} | Entry: {s.entry_price:.2f} | SL: {s.stop_loss:.2f} | TP1: {s.take_profit_1:.2f} | TP2: {s.take_profit_2:.2f} | RR: {s.risk_reward:.2f}")

# Open nodes
open_peaks = [n for n in nodes if n.node_type == NodeType.PEAK and n.number in [1, 2] and not n.is_closed]
open_valleys = [n for n in nodes if n.node_type == NodeType.VALLEY and n.number in [1, 2] and not n.is_closed]
print(f"\nOpen Peaks (Unclosed): {[p.price for p in open_peaks]}")
print(f"Open Valleys (Unclosed): {[v.price for v in open_valleys]}")

# Recent peaks & valleys for manual inspection
peaks = [n for n in nodes if n.node_type == NodeType.PEAK]
valleys = [n for n in nodes if n.node_type == NodeType.VALLEY]
print(f"\nLast 4 Peaks: {[p.price for p in peaks[-4:]]}")
print(f"Last 4 Valleys: {[v.price for v in valleys[-4:]]}")

# Downward / upward symmetry
if len(peaks) >= 2 and len(valleys) >= 2:
    print(f"Last Swing High: {peaks[-1].price} at {str(peaks[-1].timestamp)[11:16]}")
    print(f"Last Swing Low: {valleys[-1].price} at {str(valleys[-1].timestamp)[11:16]}")
