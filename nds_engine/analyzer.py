import pandas as pd
from typing import Dict, Any, List, Optional
from .node_detector import NodeDetector, Node, NodeType
from .sequencing import SequenceEngine, Sequence
from .hooks import HookDetector, Hook
from .flag_setup import FlagSetupDetector, FlagSetup
from .symmetry import SymmetryCalculator

class NDSMarketAnalyzer:
    """
    Main orchestrator for NDS (Nodal Displacement Sequencing) Market Analysis.
    Performs full multi-step NDS cycle analysis on price data.
    """
    def __init__(self, left_bars: int = 3, right_bars: int = 3, double_threshold: float = 0.001):
        self.node_detector = NodeDetector(left_bars, right_bars, double_threshold)
        self.sequence_engine = SequenceEngine()
        self.hook_detector = HookDetector()
        self.flag_detector = FlagSetupDetector()
        self.symmetry_calc = SymmetryCalculator()

    def analyze(self, df: pd.DataFrame, account_balance: float = 1000.0, risk_pct: float = 0.02) -> Dict[str, Any]:
        """
        Runs full NDS analysis on the supplied OHLC dataframe.
        """
        required_cols = {'High', 'Low', 'Close'}
        if not required_cols.issubset(df.columns):
            raise ValueError(f"DataFrame must contain columns: {required_cols}")

        # Step 1: Detect raw and clean nodes
        nodes = self.node_detector.find_raw_nodes(df)

        # Step 2: Track Sequences (1-2-3 rule)
        sequences = self.sequence_engine.process_nodes(nodes)

        # Step 3: Identify Hooks and 86.4% Levels
        hooks = self.hook_detector.detect_hooks(nodes)

        # Determine latest reference 86.4% level from most recent active hook
        latest_864 = hooks[-1].level_864 if hooks else None

        # Step 4: Scan for 123Flag Setups
        setups = self.flag_detector.scan_setups(nodes, level_864=latest_864)

        # Step 5: Position Sizing calculation for latest setup (Page 122)
        latest_setup = setups[-1] if setups else None
        position_size = None
        if latest_setup:
            risk_amount = account_balance * risk_pct
            sl_distance = abs(latest_setup.entry_price - latest_setup.stop_loss)
            if sl_distance > 0:
                # Lot calculation
                position_size = {
                    "account_balance": account_balance,
                    "risk_percent": f"{risk_pct * 100}%",
                    "risk_amount_usd": risk_amount,
                    "stop_loss_distance": round(sl_distance, 4),
                    "suggested_units": round(risk_amount / sl_distance, 2)
                }

        # Step 6: Identify Open Nodes needing closure (Page 132)
        open_peaks = [n for n in nodes if n.node_type == NodeType.PEAK and n.number in [1, 2] and not n.is_closed]
        open_valleys = [n for n in nodes if n.node_type == NodeType.VALLEY and n.number in [1, 2] and not n.is_closed]

        return {
            "total_nodes": len(nodes),
            "nodes": nodes,
            "sequences": sequences,
            "hooks": hooks,
            "setups": setups,
            "latest_setup": latest_setup,
            "position_sizing": position_size,
            "open_nodes": {
                "open_peaks_count": len(open_peaks),
                "open_valleys_count": len(open_valleys)
            },
            "latest_price": float(df['Close'].iloc[-1]) if len(df) > 0 else 0.0,
            "latest_864_level": latest_864
        }
