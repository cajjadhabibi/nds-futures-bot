import json
from collections import defaultdict

path = r'C:\Users\sajjad\OpenBot\Agents\agent-12c7a32e-1779-4fbe-add5-333a137ff056\nds_engine\active_trades.json'
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

trades = list(data.values()) if isinstance(data, dict) else data

closed = [t for t in trades if t.get('is_closed') or t.get('status') == 'CLOSED']

# Classify
tp_trades = [t for t in closed if 'TP' in t.get('close_reason', '')]
sl_trades = [t for t in closed if 'SL' in t.get('close_reason', '') or 'STOP' in t.get('close_reason', '')]
rf_trades = [t for t in closed if 'RISK_FREE' in t.get('close_reason', '')]

def calc_pnl(t):
    entry = t.get('entry', t.get('entry_price', 0))
    direction = t.get('direction', 'LONG')
    reason = t.get('close_reason', '')
    if not entry or entry <= 0:
        return 0.0
    
    if 'TP3' in reason:
        target = t.get('tp3', entry)
    elif 'TP2' in reason:
        target = t.get('tp2', entry)
    elif 'TP1' in reason:
        target = t.get('tp1', entry)
    elif 'SL' in reason or 'STOP' in reason:
        target = t.get('sl', t.get('stop_loss', entry))
    elif 'RISK_FREE' in reason:
        return 0.0
    else:
        target = entry

    if direction == 'LONG':
        return ((target - entry) / entry) * 100
    else:
        return ((entry - target) / entry) * 100

for t in closed:
    t['computed_pnl'] = calc_pnl(t)

total_trades = len(closed)
tp_count = len(tp_trades)
sl_count = len(sl_trades)
rf_count = len(rf_trades)
win_rate = (tp_count / total_trades) * 100 if total_trades else 0

total_pnl = sum(t['computed_pnl'] for t in closed)
tp_pnl = sum(t['computed_pnl'] for t in tp_trades)
sl_pnl = sum(t['computed_pnl'] for t in sl_trades)

# Breakdown by direction
by_dir = defaultdict(lambda: {'total': 0, 'tp': 0, 'sl': 0, 'pnl': 0.0})
for t in closed:
    d = t.get('direction', 'LONG')
    by_dir[d]['total'] += 1
    by_dir[d]['pnl'] += t['computed_pnl']
    if 'TP' in t.get('close_reason', ''):
        by_dir[d]['tp'] += 1
    elif 'SL' in t.get('close_reason', ''):
        by_dir[d]['sl'] += 1

# Breakdown by coin
by_coin = defaultdict(lambda: {
    'total': 0, 'tp': 0, 'sl': 0, 'pnl': 0.0,
    'tp1': 0, 'tp2': 0, 'tp3': 0,
    'sl_pcts': [], 'tp_pcts': []
})

for t in closed:
    s = t['symbol']
    pnl = t['computed_pnl']
    by_coin[s]['total'] += 1
    by_coin[s]['pnl'] += pnl
    reason = t.get('close_reason', '')
    if 'TP3' in reason:
        by_coin[s]['tp'] += 1
        by_coin[s]['tp3'] += 1
        by_coin[s]['tp_pcts'].append(pnl)
    elif 'TP2' in reason:
        by_coin[s]['tp'] += 1
        by_coin[s]['tp2'] += 1
        by_coin[s]['tp_pcts'].append(pnl)
    elif 'TP1' in reason:
        by_coin[s]['tp'] += 1
        by_coin[s]['tp1'] += 1
        by_coin[s]['tp_pcts'].append(pnl)
    elif 'SL' in reason or 'STOP' in reason:
        by_coin[s]['sl'] += 1
        by_coin[s]['sl_pcts'].append(pnl)

# Deep analysis of SL: why did SL hit?
sl_dists = [abs(t['computed_pnl']) for t in sl_trades]
avg_sl_dist = sum(sl_dists) / len(sl_dists) if sl_dists else 0

tp_dists = [t['computed_pnl'] for t in tp_trades]
avg_tp_dist = sum(tp_dists) / len(tp_dists) if tp_dists else 0

profit_factor = abs(tp_pnl / sl_pnl) if sl_pnl != 0 else 0

print("="*60)
print("STATS_START")
print(f"TOTAL:{total_trades}")
print(f"TP:{tp_count}")
print(f"SL:{sl_count}")
print(f"WR:{win_rate:.1f}")
print(f"TOTAL_PNL:{total_pnl:+.2f}")
print(f"AVG_WIN:{avg_tp_dist:.2f}")
print(f"AVG_LOSS:{avg_sl_dist:.2f}")
print(f"PROFIT_FACTOR:{profit_factor:.2f}")

print("DIRECTIONS")
for d, st in by_dir.items():
    wr = (st['tp'] / st['total']) * 100 if st['total'] else 0
    print(f"DIR|{d}|{st['total']}|{wr:.1f}|{st['tp']}|{st['sl']}|{st['pnl']:+.2f}")

print("TOP_COINS")
sorted_coins = sorted(by_coin.items(), key=lambda x: x[1]['pnl'], reverse=True)
for s, st in sorted_coins[:10]:
    wr = (st['tp'] / st['total']) * 100 if st['total'] else 0
    print(f"COIN_TOP|{s}|{st['total']}|{wr:.1f}|{st['tp']}|{st['tp3']}|{st['sl']}|{st['pnl']:+.2f}")

print("WORST_COINS")
for s, st in sorted_coins[-8:]:
    wr = (st['tp'] / st['total']) * 100 if st['total'] else 0
    print(f"COIN_BAD|{s}|{st['total']}|{wr:.1f}|{st['tp']}|{st['sl']}|{st['pnl']:+.2f}")
