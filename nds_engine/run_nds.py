import sys
import os

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import yfinance as yf
import pandas as pd

from nds_engine import NDSMarketAnalyzer, NodeType, SetupDirection

def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "^DJI" # Dow Jones as in PDF
    period = sys.argv[2] if len(sys.argv) > 2 else "60d"
    interval = sys.argv[3] if len(sys.argv) > 3 else "1h"

    print(f"=== NDS (Nodal Displacement Sequencing) Market Engine ===")
    print(f"Fetching data for {symbol} ({period}, {interval})...")

    df = yf.download(symbol, period=period, interval=interval, progress=False)
    if df.empty:
        print(f"Error: No data retrieved for {symbol}")
        return

    # Flatten multi-level columns if any (recent yfinance)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    analyzer = NDSMarketAnalyzer(left_bars=3, right_bars=3)
    results = analyzer.analyze(df, account_balance=1000.0, risk_pct=0.02)

    print("\n" + "=" * 50)
    print(f"  NDS ANALYSIS REPORT: {symbol}")
    print("=" * 50)
    print(f"Latest Close Price:   {results['latest_price']:.2f}")
    print(f"Total Nodes Detected: {results['total_nodes']}")
    print(f"Active Hooks Detected: {len(results['hooks'])}")
    print(f"Sequences Tracked:     {len(results['sequences'])}")
    print(f"123Flag Setups Found:  {len(results['setups'])}")

    if results['latest_864_level']:
        print(f"Active 86.4% Target:  {results['latest_864_level']:.2f}")

    print("\n--- Open Nodes Requiring Closure (Page 132) ---")
    print(f"Open Peaks (1-2):   {results['open_nodes']['open_peaks_count']}")
    print(f"Open Valleys (1-2): {results['open_nodes']['open_valleys_count']}")

    latest_setup = results['latest_setup']
    if latest_setup:
        print("\n" + "*" * 50)
        print("  LATEST 123FLAG SETUP DETECTED!")
        print("*" * 50)
        print(f"Direction:     {latest_setup.direction.value}")
        print(f"Entry Price:   {latest_setup.entry_price:.2f} (at Flag point F)")
        print(f"Stop Loss:     {latest_setup.stop_loss:.2f}")
        print(f"Target 1:      {latest_setup.take_profit_1:.2f} (Open Node)")
        print(f"Target 2 (T):  {latest_setup.take_profit_2:.2f} (86.4% Cycle Level)")
        print(f"Risk/Reward:   1:{latest_setup.risk_reward}")
        if results['position_sizing']:
            ps = results['position_sizing']
            print(f"Position Size: Risk ${ps['risk_amount_usd']} ({ps['risk_percent']}) | Distance: {ps['stop_loss_distance']} pts")
    else:
        print("\nNo active 123Flag setup on the current bar. Market is cycling toward next node.")

    # Generate Chart
    print("\nGenerating NDS Visualizer Chart...")
    fig, ax = plt.subplots(figsize=(14, 8), dpi=100)
    fig.patch.set_facecolor('#131722')
    ax.set_facecolor('#131722')

    # Plot Close price line
    ax.plot(df.index, df['Close'], color='#2962FF', linewidth=1.2, label=f'{symbol} Close', alpha=0.7)

    # Plot detected Nodes
    for n in results['nodes']:
        color = '#00E676' if n.node_type == NodeType.PEAK else '#FF5252'
        marker = '^' if n.node_type == NodeType.PEAK else 'v'
        label_text = f"N{n.number}" if (n.number and n.node_type == NodeType.PEAK) else (f"S{n.number}" if n.number else "•")
        ax.scatter(n.timestamp, n.price, color=color, s=50, marker=marker, zorder=5)
        ax.annotate(label_text, (n.timestamp, n.price), textcoords="offset points",
                    xytext=(0, 10 if n.node_type == NodeType.PEAK else -15),
                    ha='center', fontsize=8, color=color, weight='bold')

    # Draw 86.4% line if present
    if results['latest_864_level']:
        ax.axhline(results['latest_864_level'], color='#FFA726', linestyle='--', linewidth=1.5,
                   label=f'86.4% Target: {results["latest_864_level"]:.2f}')

    # Plot Latest Setup
    if latest_setup:
        ax.scatter(latest_setup.flag_node.timestamp, latest_setup.entry_price, color='#FFEA00', s=120, marker='*', zorder=6, label='123Flag Entry')
        ax.axhline(latest_setup.stop_loss, color='#FF1744', linestyle=':', linewidth=1.2, label=f'SL: {latest_setup.stop_loss:.2f}')
        ax.axhline(latest_setup.take_profit_1, color='#00E676', linestyle=':', linewidth=1.2, label=f'TP1: {latest_setup.take_profit_1:.2f}')

    ax.set_title(f"NDS Market Engine (Nodal Displacement Sequencing) - {symbol}", color='#FFFFFF', fontsize=14, weight='bold')
    ax.tick_params(colors='#B2B5BE')
    ax.grid(True, color='#2A2E39', linestyle='--', alpha=0.5)
    ax.legend(facecolor='#1E222D', edgecolor='#2A2E39', labelcolor='#FFFFFF', loc='upper left')

    output_dir = os.path.dirname(os.path.abspath(__file__))
    chart_path = os.path.join(output_dir, "nds_market_view.png")
    plt.tight_layout()
    plt.savefig(chart_path, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    print(f"Chart saved to: {chart_path}")

if __name__ == "__main__":
    main()
