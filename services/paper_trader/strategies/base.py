"""Base strategy interface for paper trading."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class TradeSignal:
    """Trade signal returned by strategy."""
    side: str  # 'up' or 'down'
    reason: str  # 진입 사유 (밴드 정보 등)
    contracts: int  # 구매 토큰 수
    price: float  # 주문 가격


class BaseStrategy(ABC):
    """Abstract base class for trading strategies."""

    name: str = "base"
    description: str = "Base strategy"

    def __init__(self):
        pass

    @abstractmethod
    def should_enter(
        self,
        coin: str,
        timeframe: str,
        elapsed_min: int,
        up_orderbook: Optional[Dict[str, Any]],
        down_orderbook: Optional[Dict[str, Any]],
        candle_key: str,
    ) -> Optional[TradeSignal]:
        """
        Determine if we should enter a trade.

        Args:
            coin: 코인 (btc, eth, etc.)
            timeframe: 타임프레임 (15m, 1h, etc.)
            elapsed_min: 캔들 시작 후 경과 시간 (분)
            up_orderbook: UP 오더북 {best_bid, best_ask, ...}
            down_orderbook: DOWN 오더북
            candle_key: 캔들 식별자 (coin_timeframe_start_time)

        Returns:
            TradeSignal if should enter, None otherwise
        """
        pass

    def reset_for_candle(self, candle_key: str):
        """캔들 시작 시 상태 리셋 (필요한 전략만 override)"""
        pass

    def get_status(self) -> Dict[str, Any]:
        """현재 상태 반환 (디버깅용)"""
        return {}
