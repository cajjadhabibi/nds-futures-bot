import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
from nds_engine import NDSMarketAnalyzer, NodeType, SymmetryCalculator

def run():
    df_4h = yf.download('BTC-USD', period='90d', interval='4h', progress=False)
    if isinstance(df_4h.columns, pd.MultiIndex):
        df_4h.columns = df_4h.columns.get_level_values(0)

    df_1d = yf.download('BTC-USD', period='180d', interval='1d', progress=False)
    if isinstance(df_1d.columns, pd.MultiIndex):
        df_1d.columns = df_1d.columns.get_level_values(0)

    analyzer_4h = NDSMarketAnalyzer(left_bars=3, right_bars=3)
    res_4h = analyzer_4h.analyze(df_4h)

    analyzer_1d = NDSMarketAnalyzer(left_bars=3, right_bars=3)
    res_1d = analyzer_1d.analyze(df_1d)

    print("=== DAILY MACRO ANALYSIS ===")
    current_price = float(df_4h['Close'].iloc[-1])
    print(f"Current Live Price: {current_price:.2f}")

    print("\nRecent Daily Nodes:")
    for n in res_1d['nodes'][-6:]:
        t_str = str(n.timestamp)[:10]
        print(f"  {t_str} | {n.node_type.value:<6} | {n.price:.2f}")

    print("\n=== 4H NODES & SEQUENCING ===")
    peaks = [n for n in res_4h['nodes'] if n.node_type == NodeType.PEAK]
    valleys = [n for n in res_4h['nodes'] if n.node_type == NodeType.VALLEY]

    print("\nRecent 4H Peaks (Open Resistance Levels to be Closed):")
    for p in peaks[-6:]:
        t_str = str(p.timestamp)[:16]
        print(f"  {t_str} | Peak: {p.price:.2f}")

    print("\nRecent 4H Valleys (Support / Sequence Nodes):")
    for v in valleys[-6:]:
        t_str = str(v.timestamp)[:16]
        print(f"  {t_str} | Valley: {v.price:.2f}")

    # Downward symmetry
    p_last = peaks[-1].price
    p_prev = peaks[-2].price
    v_prev = valleys[-2].price

    ab_drop = p_prev - v_prev
    cd_target = p_last - ab_drop
    print(f"\n1. Direct Downward Symmetry Target (AB=CD): {cd_target:.2f}")

    # Macro 86.4% and targets
    major_high = 81075.60 # Major high
    recent_low = min([v.price for v in valleys[-3:]] + [float(df_4h['Low'].iloc[-1])])
    drop_range = major_high - recent_low

    level_864 = recent_low + (0.864 * drop_range)
    level_z = recent_low + (drop_range * 0.5)

    print(f"2. Major High Origin (T): {major_high:.2f}")
    print(f"3. Cycle Bottom Floor (F / S3): {recent_low:.2f}")
    print(f"4. Target Point Z (First Minimum Target): {level_z:.2f}")
    print(f"5. Target Open Node 1 (Peak 1): {peaks[-2].price:.2f}")
    print(f"6. Target Open Node 2 (Peak 2): {peaks[-3].price:.2f}")
    print(f"7. Target Cycle Close (86.4% of Drop): {level_864:.2f}")

if __name__ == '__main__':
    run()
