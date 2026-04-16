"""
Port interfaces (hexagonal architecture).

repo B(trade-infra)가 구현해야 하는 5개 계약.
전략 코어는 이 Protocol만 알고, 구체 구현(Redis/CLOB/Telegram 등)은 모름.
"""
from datetime import datetime
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from .types import Orderbook, OrderResult, TradeSignal


@runtime_checkable
class IClockPort(Protocol):
    """
    시계 추상화.
    - monotonic: cooltime 경과 측정 (단조증가, 되감기 없음)
    - now_utc: candle 경계 판단 / 로그 타임스탬프

    백테스트에서는 시뮬 시계를 주입해서 결정론적 테스트 가능.
    """

    def monotonic(self) -> float: ...
    def now_utc(self) -> datetime: ...


@runtime_checkable
class IMarketDataPort(Protocol):
    """
    호가 캐시 조회 + token_id 해석.
    Polymarket CLOB 뿐만 아니라 어떤 binary option 거래소든 이 인터페이스만 맞추면 됨.
    """

    def get_orderbook(
        self, coin: str, timeframe: str, side: str
    ) -> Optional[Orderbook]: ...

    def get_token_id(
        self, coin: str, timeframe: str, side: str
    ) -> Optional[str]: ...


@runtime_checkable
class IOrderExecutor(Protocol):
    """
    실제 주문 실행.
    - place_gtc: GTC maker 주문. 실패 시 OrderResult(success=False, error=...) 반환.
    """

    async def place_gtc(
        self,
        coin: str,
        token_id: str,
        price: float,
        contracts: int,
    ) -> OrderResult: ...


@runtime_checkable
class ITradeRecorder(Protocol):
    """
    거래 기록 + 캔들당 crossing 통계 저장.
    DB 미구현시 no-op으로 둬도 전략 동작에 영향 없음.
    """

    async def record_trade(
        self,
        coin: str,
        timeframe: str,
        signal: TradeSignal,
        order_result: OrderResult,
        candle_start: datetime,
        candle_end: datetime,
        crossing_info: Optional[Dict[str, Any]],
        latency_ms: float,
    ) -> None: ...

    async def save_crossing_stats(
        self,
        coin: str,
        timeframe: str,
        candle_key: str,
        candle_start: Optional[datetime],
        candle_end: Optional[datetime],
        crossing_count: int,
        trade_count: int,
    ) -> None: ...

    async def get_recent_crossing_counts(
        self,
        coin: str,
        limit: int = 2,
    ) -> list[int]: ...


@runtime_checkable
class INotifier(Protocol):
    """
    텔레그램/슬랙 등 알림 채널. 미구현시 no-op.
    """

    def notify(self, message: str) -> None: ...
