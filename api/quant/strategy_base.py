from typing import List, Dict, Any, Optional

class BaseStrategy:
    """
    Base class for all quantitative strategies.
    Strategies should inherit from this class and implement generate_signal.
    """
    def __init__(self, params: Dict[str, Any] = None, constraints: Dict[str, Any] = None):
        """
        Initialize strategy with parameters and constraints.
        
        Args:
            params (dict): Strategy-specific parameters (e.g., {"ma_short": 5}).
            constraints (dict): Risk management constraints (e.g., {"stop_loss_pct": 5}).
        """
        self.params = params or {}
        self.constraints = constraints or {}

    def generate_signal(
        self, 
        klines: List[Dict[str, Any]], 
        position: Optional[Dict[str, Any]], 
        account_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate a trading signal based on market data and current state.

        Args:
            klines (list): List of kline data (OHLCV).
            position (dict): Current position for this symbol (quantity, avg_cost). None if no position.
            account_context (dict): Account info (cash_balance, total_equity, etc.).

        Returns:
            dict: Signal dictionary with keys:
                  - side: "BUY", "SELL", "HOLD"
                  - confidence: float (0.0 - 1.0)
                  - reason: str
                  - suggested_quantity: int (optional)
        """
        raise NotImplementedError("Strategies must implement generate_signal")

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _avg(self, values: List[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)
