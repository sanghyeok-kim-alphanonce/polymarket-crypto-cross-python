"""
Data models for cross_5m_improve strategy.

표준 라이브러리만 사용. 외부 의존성 0개.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TradeSignal:
    """전략이 생성하는 주문 의도."""
    side: str            # "up" | "down"
    reason: str          # "CROSS5M_UP #2 @45s" or "HEDGE_TIME_UP #4 @260s"
    contracts: int       # 10 or 20


@dataclass
class CrossingEvent:
    """
    외부에서 전략에 공급하는 crossing 이벤트.
    어떤 데이터 소스를 쓰든 이 형태로만 변환해서 넘기면 됨.
    """
    coin: str
    timeframe: str       # "5m"
    direction: str       # "up" | "down"
    candle_start: datetime
    candle_end: datetime
    elapsed_ms: int
    # 아래는 알림/통계용. 없으면 None 허용
    candle_open: Optional[float] = None
    prev_price: Optional[float] = None
    current_price: Optional[float] = None
    prev_elapsed_ms: Optional[int] = None
    curr_elapsed_ms: Optional[int] = None


@dataclass
class Orderbook:
    """market data port가 반환하는 호가 스냅샷."""
    best_bid: float
    best_ask: float
    best_bid_size: float
    best_ask_size: float
    mid_price: float
    token_id: str
    market_slug: str
    timestamp: float


@dataclass
class OrderResult:
    """주문 실행 결과."""
    success: bool
    target_contracts: int
    filled_contracts: float
    filled_cost: float
    fill_price: float
    order_price: float
    total_latency_ms: float
    status: str = "UNKNOWN"    # FILLED | PENDING | FAILED
    order_id: str = ""
    error: Optional[str] = None


@dataclass
class CandleState:
    """캔들 단위 전략 상태."""
    count: int = 0
    last_trade_direction: Optional[str] = None
    failed: bool = False
    hedged: bool = False
    crossing_times: List[Tuple[int, str]] = field(default_factory=list)

    # cooltime
    in_cooltime: bool = False
    cooltime_start: float = 0.0
    pending_direction: Optional[str] = None
    pending_elapsed: Optional[int] = None
    pending_crossing_info: Optional[Dict[str, Any]] = None
