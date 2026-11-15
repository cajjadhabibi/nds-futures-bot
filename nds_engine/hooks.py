from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
from .node_detector import Node, NodeType

class HookType(Enum):
    POSITIVE_A = "POSITIVE_A"  # صعودی نوع A (ایده‌آل)
    POSITIVE_B = "POSITIVE_B"  # صعودی نوع B (قله ۳ تشکیل نمی‌شود)
    NEGATIVE_A = "NEGATIVE_A"  # نزولی نوع A (ایده‌آل)
    NEGATIVE_B = "NEGATIVE_B"  # نزولی نوع B (دره ۳ تشکیل نمی‌شود)

@dataclass
class Hook:
    hook_type: HookType
    start_node: Node                # نقطه T آغاز هوک
    peaks: List[Node]               # قله‌های هوک [1, 2, 3]
    valleys: List[Node]             # دره‌های هوک [1, 2, 3]
    level_864: float                # تراز ۸۶.۴٪
    is_closed: bool = False         # آیا در نود ۳ روی تراز ۸۶.۴ بسته شده؟
    closing_node: Optional[Node] = None

class HookDetector:
    """
    Identifies NDS Hooks (Type A and Type B) and calculates the 86.4% cycle close level.
    Per NDS rules (Pages 22-29):
    - Positive Hook Type A: P1 < P2 < P3 and V1 > V2 > V3. Closes at V3 around 86.4% of (T -> P3).
    - Negative Hook Type A: V1 > V2 > V3 and P1 < P2 < P3. Closes at P3 around 86.4% of (T -> V3).
    - Type B: Only 1 and 2 form; 3 does not form (Page 26-28).
    """
    def __init__(self, tolerance_864: float = 0.05):
        self.tolerance_864 = tolerance_864

    def detect_hooks(self, nodes: List[Node]) -> List[Hook]:
        hooks: List[Hook] = []
        n = len(nodes)
        if n < 7: # Need at least T + 3 peaks + 3 valleys
            return hooks

        for i in range(n - 6):
            # Check for Positive Hook Type A (Starts with Valley T)
            if nodes[i].node_type == NodeType.VALLEY:
                t_node = nodes[i]
                sub = nodes[i+1 : min(i + 8, n)]
                peaks = [x for x in sub if x.node_type == NodeType.PEAK]
                valleys = [x for x in sub if x.node_type == NodeType.VALLEY]

                if len(peaks) >= 3 and len(valleys) >= 3:
                    p1, p2, p3 = peaks[0], peaks[1], peaks[2]
                    v1, v2, v3 = valleys[0], valleys[1], valleys[2]

                    # Verify Type A conditions: P1 < P2 < P3 and V1 > V2 > V3
                    if p1.price < p2.price < p3.price and v1.price > v2.price > v3.price:
                        # Calculate 86.4% retracement level
                        total_move = p3.price - t_node.price
                        level_864 = p3.price - 0.864 * total_move

                        # Check if V3 closes near 86.4%
                        close_diff = abs(v3.price - level_864) / total_move if total_move > 0 else 1.0
                        is_closed = close_diff <= self.tolerance_864

                        hooks.append(Hook(
                            hook_type=HookType.POSITIVE_A,
                            start_node=t_node,
                            peaks=[p1, p2, p3],
                            valleys=[v1, v2, v3],
                            level_864=level_864,
                            is_closed=is_closed,
                            closing_node=v3 if is_closed else None
                        ))

            # Check for Negative Hook Type A (Starts with Peak T)
            elif nodes[i].node_type == NodeType.PEAK:
                t_node = nodes[i]
                sub = nodes[i+1 : min(i + 8, n)]
                peaks = [x for x in sub if x.node_type == NodeType.PEAK]
                valleys = [x for x in sub if x.node_type == NodeType.VALLEY]

                if len(valleys) >= 3 and len(peaks) >= 3:
                    v1, v2, v3 = valleys[0], valleys[1], valleys[2]
                    p1, p2, p3 = peaks[0], peaks[1], peaks[2]

                    # Verify Negative Type A conditions: V1 > V2 > V3 and P1 < P2 < P3
                    if v1.price > v2.price > v3.price and p1.price < p2.price < p3.price:
                        total_move = t_node.price - v3.price
                        level_864 = v3.price + 0.864 * total_move

                        close_diff = abs(p3.price - level_864) / total_move if total_move > 0 else 1.0
                        is_closed = close_diff <= self.tolerance_864

                        hooks.append(Hook(
                            hook_type=HookType.NEGATIVE_A,
                            start_node=t_node,
                            peaks=[p1, p2, p3],
                            valleys=[v1, v2, v3],
                            level_864=level_864,
                            is_closed=is_closed,
                            closing_node=p3 if is_closed else None
                        ))

        return hooks
