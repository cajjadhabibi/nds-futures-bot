import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from nds_engine import NDSMarketAnalyzer, NodeType, SymmetryCalculator

def analyze_symbol(symbol_name, yf_ticker):
    print(f"\n=======================================================")
    print(f"      NDS ANALYSIS: {symbol_name} ({yf_ticker})")
    print(f"=======================================================")
    
    ticker = yf.Ticker(yf_ticker)
    
    # 1. Daily data (Macro)
    df_daily = ticker.history(period="180d", interval="1d")
    if isinstance(df_daily.columns, pd.MultiIndex):
        df_daily.columns = df_daily.columns.get_level_values(0)
        
    # 2. 4-Hour data (Intermediate structure)
    df_4h = ticker.history(period="60d", interval="1h") # Use 1h resampled to 4h or 1h directly
    if isinstance(df_4h.columns, pd.MultiIndex):
        df_4h.columns = df_4h.columns.get_level_values(0)

    current_price = float(df_daily['Close'].iloc[-1])
    print(f"Current Market Price: {current_price:.2f}")

    # Analyzer for Daily
    an_daily = NDSMarketAnalyzer(left_bars=2, right_bars=2)
    res_daily = an_daily.analyze(df_daily)
    
    # Analyzer for 1H
    an_1h = NDSMarketAnalyzer(left_bars=3, right_bars=3)
    res_1h = an_1h.analyze(df_4h)

    print("\n--- Daily Macro Nodes (Last 8) ---")
    daily_nodes = res_daily['nodes'][-8:]
    for n in daily_nodes:
        t_str = str(n.timestamp)[:10]
        n_num = f"{'N' if n.node_type == NodeType.PEAK else 'S'}{n.number}" if n.number else "  "
        print(f"  {t_str} | {n.node_type.value:<6} {n_num} | Price: {n.price:.2f}")

    # Identify major high (T or Peak 3) and major low (Origin or Valley 3)
    all_peaks_daily = [n for n in res_daily['nodes'] if n.node_type == NodeType.PEAK]
    all_valleys_daily = [n for n in res_daily['nodes'] if n.node_type == NodeType.VALLEY]

    recent_major_peak = max([p.price for p in all_peaks_daily[-4:]])
    recent_major_valley = min([v.price for v in all_valleys_daily[-4:]])
    
    # Symmetry and 86.4% calculation
    cycle_range = recent_major_peak - recent_major_valley
    ret_864_down = recent_major_peak - (0.864 * cycle_range) # if pulling back from top
    ret_864_up = recent_major_valley + (0.864 * cycle_range)   # if bouncing from bottom
    point_z = recent_major_valley + (0.5 * cycle_range)

    print("\n--- Key NDS Structural Reference Levels ---")
    print(f"Major Cycle Peak (T / N3):   {recent_major_peak:.2f}")
    print(f"Major Cycle Low (Origin/S3): {recent_major_valley:.2f}")
    print(f"Cycle Amplitude (Delta):     {cycle_range:.2f}")
    print(f"Point Z (Mid-Cycle Pivot):   {point_z:.2f}")
    print(f"Golden 86.4% Retracement (Down Cycle Target): {ret_864_down:.2f}")
    print(f"Golden 86.4% Retracement (Up Cycle Target):   {ret_864_up:.2f}")

    # Open Nodes (1 and 2 that need closure)
    print("\n--- Recent Unclosed / Reference Nodes (Target Areas) ---")
    recent_1h_peaks = [n for n in res_1h['nodes'] if n.node_type == NodeType.PEAK][-5:]
    recent_1h_valleys = [n for n in res_1h['nodes'] if n.node_type == NodeType.VALLEY][-5:]
    
    print("Recent Overhead Peaks (Resistance / Bullish Targets):")
    for p in recent_1h_peaks:
        dist = p.price - current_price
        if dist > 0:
            print(f"  Open Peak Target: {p.price:.2f} (+{dist:.2f} pts) [Date: {str(p.timestamp)[:13]}]")
            
    print("Recent Underlying Valleys (Support / Bearish Targets):")
    for v in recent_1h_valleys:
        dist = current_price - v.price
        if dist > 0:
            print(f"  Open Valley Target: {v.price:.2f} (-{dist:.2f} pts) [Date: {str(v.timestamp)[:13]}]")

    # AB=CD symmetry targets
    if len(recent_1h_peaks) >= 2 and len(recent_1h_valleys) >= 2:
        # Check last leg symmetry
        p1 = recent_1h_peaks[-2].price
        v1 = recent_1h_valleys[-2].price
        p2 = recent_1h_peaks[-1].price
        v2 = recent_1h_valleys[-1].price
        
        # If trend is downward:
        leg1_drop = abs(p1 - v1)
        sym_down_target = p2 - leg1_drop
        print(f"\n--- Price Symmetry Projections (AB=CD) ---")
        print(f"Bearish Symmetry Target (Leg 1: {leg1_drop:.2f} pts): {sym_down_target:.2f}")
        
        # If trend is upward:
        leg1_rise = abs(p1 - v1)
        sym_up_target = v2 + leg1_rise
        print(f"Bullish Symmetry Target (Leg 1: {leg1_rise:.2f} pts): {sym_up_target:.2f}")

    # Check for active setups
    if res_1h['setups']:
        print(f"\nActive 123Flag Setups: {len(res_1h['setups'])}")
        for s in res_1h['setups'][-2:]:
            print(f"  Setup Direction: {s.direction.value} | Entry: {s.entry_price:.2f} | SL: {s.stop_loss:.2f} | TP1: {s.take_profit_1:.2f} | TP2 (86.4%): {s.take_profit_2:.2f}")

if __name__ == "__main__":
    analyze_symbol("GOLD (XAU/USD - Futures GC=F)", "GC=F")
    analyze_symbol("CRUDE OIL (WTI - Futures CL=F)", "CL=F")
