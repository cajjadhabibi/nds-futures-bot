from dataclasses import dataclass, field
from typing import List, Optional
from .node_detector import Node, NodeType

@dataclass
class Sequence:
    direction: str                     # "BULLISH" (rising peaks / rising valleys) or "BEARISH"
    peak_1: Optional[Node] = None
    valley_1: Optional[Node] = None
    peak_2: Optional[Node] = None
    valley_2: Optional[Node] = None
    peak_3: Optional[Node] = None
    valley_3: Optional[Node] = None
    is_complete: bool = False
    open_nodes: List[Node] = field(default_factory=list)

class SequenceEngine:
    """
    Implements NDS Sequence logic:
    - Peak 1 -> Peak 2 (Higher High) -> Peak 3 (Higher High)
    - Valley 1 -> Valley 2 (Lower Low) -> Valley 3 (Lower Low)
    - Rejects peaks/valleys that don't respect the NDS rule (Page 68).
    - Tracks open 1-2 nodes until node 3 closes them (Page 132).
    """
    def __init__(self):
        pass

    def process_nodes(self, nodes: List[Node]) -> List[Sequence]:
        sequences: List[Sequence] = []
        if len(nodes) < 3:
            return sequences

        # Track active Bullish (Hook+ / 123 rising) and Bearish (Hook- / 123 falling) sequences
        current_bull_seq = Sequence(direction="BULLISH")
        current_bear_seq = Sequence(direction="BEARISH")

        for node in nodes:
            if node.node_type == NodeType.PEAK:
                # Bullish peak logic (P2 > P1, P3 > P2)
                if current_bull_seq.peak_1 is None:
                    current_bull_seq.peak_1 = node
                    node.number = 1
                    current_bull_seq.open_nodes.append(node)
                elif current_bull_seq.peak_2 is None:
                    if node.price > current_bull_seq.peak_1.price:
                        current_bull_seq.peak_2 = node
                        node.number = 2
                        current_bull_seq.open_nodes.append(node)
                    # If lower, per page 68 it is not numbered as 2 in this sequence
                elif current_bull_seq.peak_3 is None:
                    if node.price > current_bull_seq.peak_2.price:
                        current_bull_seq.peak_3 = node
                        node.number = 3
                        # Node 3 reached! Sequence completed on peaks
                        current_bull_seq.is_complete = True
                        sequences.append(current_bull_seq)
                        # Reset for new sequence
                        current_bull_seq = Sequence(direction="BULLISH")

                # Bearish peak logic (lower peaks in descending or flag formation)
                if current_bear_seq.peak_1 is None:
                    current_bear_seq.peak_1 = node
                elif current_bear_seq.peak_2 is None:
                    current_bear_seq.peak_2 = node
                elif current_bear_seq.peak_3 is None:
                    current_bear_seq.peak_3 = node

            elif node.node_type == NodeType.VALLEY:
                # Bearish valley logic (V2 < V1, V3 < V2 - Hook Type A downside)
                if current_bull_seq.valley_1 is None:
                    current_bull_seq.valley_1 = node
                    node.number = 1
                elif current_bull_seq.valley_2 is None:
                    if node.price < current_bull_seq.valley_1.price:
                        current_bull_seq.valley_2 = node
                        node.number = 2
                elif current_bull_seq.valley_3 is None:
                    if current_bull_seq.valley_2 and node.price < current_bull_seq.valley_2.price:
                        current_bull_seq.valley_3 = node
                        node.number = 3

                # Track valley descending sequence
                if current_bear_seq.valley_1 is None:
                    current_bear_seq.valley_1 = node
                    node.number = 1
                    current_bear_seq.open_nodes.append(node)
                elif current_bear_seq.valley_2 is None:
                    if node.price < current_bear_seq.valley_1.price:
                        current_bear_seq.valley_2 = node
                        node.number = 2
                        current_bear_seq.open_nodes.append(node)
                elif current_bear_seq.valley_3 is None:
                    if node.price < current_bear_seq.valley_2.price:
                        current_bear_seq.valley_3 = node
                        node.number = 3
                        current_bear_seq.is_complete = True
                        sequences.append(current_bear_seq)
                        current_bear_seq = Sequence(direction="BEARISH")

        return sequences
