"""
cross_5m_improve_strategy — 5분봉 Crossing 전략 (Max5 + 속도필터 + 헤지 + 1s cooltime)

Public API:
    from cross_5m_improve_strategy import (
        CrossingStrategy5MImprove,   # 순수 전략 코어
        CrossingRunner,              # 이벤트 와이어링 facade
        StrategyParams, DEFAULT_PARAMS,
        TradeSignal, CrossingEvent, Orderbook, OrderResult, CandleState,
        IClockPort, IMarketDataPort, IOrderExecutor, ITradeRecorder, INotifier,
    )
"""
from .config import DEFAULT_PARAMS, StrategyParams
from .ports import (
    IClockPort,
    IMarketDataPort,
    INotifier,
    IOrderExecutor,
    ITradeRecorder,
)
from .runner import CrossingRunner
from .strategy import CrossingStrategy5MImprove
from .types import (
    CandleState,
    CrossingEvent,
    Orderbook,
    OrderResult,
    TradeSignal,
)

__all__ = [
    "CrossingStrategy5MImprove",
    "CrossingRunner",
    "StrategyParams",
    "DEFAULT_PARAMS",
    "TradeSignal",
    "CrossingEvent",
    "Orderbook",
    "OrderResult",
    "CandleState",
    "IClockPort",
    "IMarketDataPort",
    "IOrderExecutor",
    "ITradeRecorder",
    "INotifier",
]
