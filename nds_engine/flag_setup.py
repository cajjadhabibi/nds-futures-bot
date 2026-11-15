from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
from .node_detector import Node, NodeType

class SetupDirection(Enum):
    LONG = "LONG"    # خرید (Bullish 123Flag)
    SHORT = "SHORT"  # فروش (Bearish 123Flag)

@dataclass
class FlagSetup:
    direction: SetupDirection
    node_1: Node
    node_2: Node
    node_3: Node
    flag_node: Node          # نقطه F (اسپایک ورود)
    entry_price: float       # قیمت ورود در نقطه F
    stop_loss: float         # حد ضرر بالای F برای Short، زیر F برای Long
    take_profit_1: float     # تارگت اول روی نودهای باز ۱ و ۲
    take_profit_2: float     # تارگت نهایی تراز ۸۶.۴٪
    risk_reward: float       # نسبت سود به ریسک
    timestamp: any

class FlagSetupDetector:
    """
    Detects 123Flag setups according to NDS specifications (Pages 82-99, 109-112).
    - 4 Nodes: Node 1 -> Node 2 -> Node 3 -> Flag (F)
    - Short Setup: P1 < P2 < P3 -> Spike to Flag F -> Enter Short with SL above F.
    - Long Setup: V1 > V2 > V3 -> Spike to Flag F -> Enter Long with SL below F.
    """
    def __init__(self, min_rr: float = 3.0):
        self.min_rr = min_rr

    def scan_setups(self, nodes: List[Node], level_864: Optional[float] = None) -> List[FlagSetup]:
        setups: List[FlagSetup] = []
        n = len(nodes)
        if n < 5:
            return setups

        for i in range(n - 4):
            # Scan for Short 123Flag Setup:
            # We look for 3 consecutive ascending peaks and a flag peak F
            sub = nodes[i : i + 5]
            peaks = [x for x in sub if x.node_type == NodeType.PEAK]
            valleys = [x for x in sub if x.node_type == NodeType.VALLEY]

            # Short Setup: 3 peaks where P1 < P2 < P3 and next extreme is Flag F
            if len(peaks) >= 3 and len(valleys) >= 1:
                p1, p2, p3 = peaks[0], peaks[1], peaks[2]
                if p1.price < p2.price < p3.price:
                    # Look ahead for Flag spike F
                    # If there is a subsequent peak F that exceeds P3 or creates a spike
                    flag_candidates = [x for x in nodes[p3.index + 1 :] if x.node_type == NodeType.PEAK]
                    if flag_candidates:
                        f_node = flag_candidates[0]
                        if f_node.price >= p3.price:
                            # Valid 123Flag Short setup
                            entry = f_node.price
                            sl = entry + abs(f_node.price - p3.price) * 0.3 + (entry * 0.001) # SL just above F
                            risk = abs(sl - entry)
                            
                            valleys_below = [v.price for v in valleys if v.price < entry]
                            tp1 = valleys_below[0] if valleys_below else (entry - risk * 1.5)
                            
                            if level_864 and level_864 < tp1:
                                tp2 = level_864
                            else:
                                tp2 = entry - risk * 3.5

                            reward = abs(entry - tp2)
                            rr = reward / risk if risk > 0 else 0.0

                            setups.append(FlagSetup(
                                direction=SetupDirection.SHORT,
                                node_1=p1,
                                node_2=p2,
                                node_3=p3,
                                flag_node=f_node,
                                entry_price=entry,
                                stop_loss=sl,
                                take_profit_1=tp1,
                                take_profit_2=tp2,
                                risk_reward=round(rr, 2),
                                timestamp=f_node.timestamp
                            ))

            # Long Setup: 3 valleys where V1 > V2 > V3 and next extreme is Flag F (spike down)
            if len(valleys) >= 3 and len(peaks) >= 1:
                v1, v2, v3 = valleys[0], valleys[1], valleys[2]
                if v1.price > v2.price > v3.price:
                    flag_candidates = [x for x in nodes[v3.index + 1 :] if x.node_type == NodeType.VALLEY]
                    if flag_candidates:
                        f_node = flag_candidates[0]
                        if f_node.price <= v3.price:
                            entry = f_node.price
                            sl = entry - abs(v3.price - f_node.price) * 0.3 - (entry * 0.001)
                            risk = abs(entry - sl)
                            
                            peaks_above = [p.price for p in peaks if p.price > entry]
                            tp1 = peaks_above[0] if peaks_above else (entry + risk * 1.5)
                            
                            if level_864 and level_864 > tp1:
                                tp2 = level_864
                            else:
                                tp2 = entry + risk * 3.5

                            reward = abs(tp2 - entry)
                            rr = reward / risk if risk > 0 else 0.0

                            setups.append(FlagSetup(
                                direction=SetupDirection.LONG,
                                node_1=v1,
                                node_2=v2,
                                node_3=v3,
                                flag_node=f_node,
                                entry_price=entry,
                                stop_loss=sl,
                                take_profit_1=tp1,
                                take_profit_2=tp2,
                                risk_reward=round(rr, 2),
                                timestamp=f_node.timestamp
                            ))

        return setups
