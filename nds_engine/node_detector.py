from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
import pandas as pd
import numpy as np

class NodeType(Enum):
    PEAK = "PEAK"      # قله (N)
    VALLEY = "VALLEY"  # دره (S)

@dataclass
class Node:
    index: int                  # Bar index
    timestamp: any              # Candle timestamp
    price: float                # Extreme price (High for peak, Low for valley)
    node_type: NodeType         # PEAK or VALLEY
    number: Optional[int] = None # 1, 2, or 3 according to NDS sequence
    is_closed: bool = False      # Whether closed by node 3
    is_double: bool = False      # Double Top / Double Bottom merged

class NodeDetector:
    """
    Detects market nodes (peaks & valleys) using wick extremes and applies
    NDS rules including Double Top / Double Bottom merging.
    """
    def __init__(self, left_bars: int = 3, right_bars: int = 3, double_threshold: float = 0.001):
        self.left_bars = left_bars
        self.right_bars = right_bars
        self.double_threshold = double_threshold

    def find_raw_nodes(self, df: pd.DataFrame) -> List[Node]:
        """
        Identify initial swing highs and swing lows from OHLC data using candle wicks.
        """
        nodes: List[Node] = []
        n = len(df)
        if n < self.left_bars + self.right_bars + 1:
            return nodes

        highs = df['High'].values
        lows = df['Low'].values
        times = df.index if not isinstance(df.index, pd.RangeIndex) else df.get('Datetime', df.index)

        for i in range(self.left_bars, n - self.right_bars):
            # Check Peak (High is higher than neighbors)
            is_peak = True
            curr_high = highs[i]
            for l in range(1, self.left_bars + 1):
                if highs[i - l] >= curr_high:
                    is_peak = False
                    break
            if is_peak:
                for r in range(1, self.right_bars + 1):
                    if highs[i + r] > curr_high:
                        is_peak = False
                        break

            # Check Valley (Low is lower than neighbors)
            is_valley = True
            curr_low = lows[i]
            for l in range(1, self.left_bars + 1):
                if lows[i - l] <= curr_low:
                    is_valley = False
                    break
            if is_valley:
                for r in range(1, self.right_bars + 1):
                    if lows[i + r] < curr_low:
                        is_valley = False
                        break

            if is_peak and not is_valley:
                nodes.append(Node(index=i, timestamp=times[i], price=float(curr_high), node_type=NodeType.PEAK))
            elif is_valley and not is_peak:
                nodes.append(Node(index=i, timestamp=times[i], price=float(curr_low), node_type=NodeType.VALLEY))
            elif is_peak and is_valley:
                # Outside bar: determine by which extreme is further from previous
                if nodes:
                    if nodes[-1].node_type == NodeType.VALLEY:
                        nodes.append(Node(index=i, timestamp=times[i], price=float(curr_high), node_type=NodeType.PEAK))
                    else:
                        nodes.append(Node(index=i, timestamp=times[i], price=float(curr_low), node_type=NodeType.VALLEY))
                else:
                    nodes.append(Node(index=i, timestamp=times[i], price=float(curr_high), node_type=NodeType.PEAK))

        return self._merge_consecutive_same_type(nodes)

    def _merge_consecutive_same_type(self, raw_nodes: List[Node]) -> List[Node]:
        """
        Enforce strict alternation (Peak -> Valley -> Peak -> Valley)
        and merge Double Tops / Double Bottoms according to NDS Page 78.
        """
        if not raw_nodes:
            return []

        cleaned: List[Node] = []
        for curr in raw_nodes:
            if not cleaned:
                cleaned.append(curr)
                continue

            prev = cleaned[-1]
            if curr.node_type == prev.node_type:
                # Check for Double Top / Double Bottom (Page 78)
                diff_ratio = abs(curr.price - prev.price) / ((curr.price + prev.price) / 2)
                if diff_ratio <= self.double_threshold:
                    # Merge into a single node representing the double top/bottom
                    prev.is_double = True
                    # Keep the more extreme price
                    if curr.node_type == NodeType.PEAK and curr.price > prev.price:
                        prev.price = curr.price
                        prev.index = curr.index
                        prev.timestamp = curr.timestamp
                    elif curr.node_type == NodeType.VALLEY and curr.price < prev.price:
                        prev.price = curr.price
                        prev.index = curr.index
                        prev.timestamp = curr.timestamp
                else:
                    # Keep only the more extreme node
                    if curr.node_type == NodeType.PEAK:
                        if curr.price > prev.price:
                            cleaned[-1] = curr
                    else:
                        if curr.price < prev.price:
                            cleaned[-1] = curr
            else:
                cleaned.append(curr)

        return cleaned
