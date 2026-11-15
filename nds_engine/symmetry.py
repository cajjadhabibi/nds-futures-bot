from typing import Optional, Tuple
from .node_detector import Node

class SymmetryCalculator:
    """
    Implements NDS Symmetry formulas (Pages 53-65):
    - Direct Symmetry: R2 = R1, C2 = C1
    - Expansion Symmetry: B2 = (B1 * A2) / A1
    - General Induction: Bn = (B_{n-1} * An) / A_{n-1}
    - Reverse Symmetry: CD = AB -> Target D = C + (B - A)
    - Node Distance Symmetry: (N2 - N1) ≈ (N3 - N2)
    """

    @staticmethod
    def calculate_expansion_correction(a1: float, a2: float, b1: float) -> float:
        """
        Formula from Page 59-61:
        K = (A2 / A1) - 1
        B2 = (B1 * A2) / A1
        """
        if a1 == 0:
            return b1
        return (b1 * a2) / a1

    @staticmethod
    def calculate_expansion_factor(a1: float, a2: float) -> float:
        if a1 == 0:
            return 0.0
        return (a2 / a1) - 1.0

    @staticmethod
    def project_node_3(node_1: Node, node_2: Node) -> float:
        """
        Projects Node 3 price based on distance symmetry between N1 and N2 (Page 63):
        Price(N3) = Price(N2) + (Price(N2) - Price(N1))
        """
        distance = node_2.price - node_1.price
        return node_2.price + distance

    @staticmethod
    def reverse_symmetry_target(a: float, b: float, c: float, is_bullish: bool = True) -> float:
        """
        AB = CD pattern projection (Page 65).
        """
        ab_length = abs(b - a)
        if is_bullish:
            return c + ab_length
        else:
            return c - ab_length
