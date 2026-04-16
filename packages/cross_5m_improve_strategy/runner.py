"""
Event wiring facade.

역할: 외부에서 오는 3가지 이벤트(crossing/orderbook/candle_boundary) 를 받아
전략 코어에 위임하고, 결과를 IOrderExecutor/ITradeRecorder로 넘긴다.

repo B는 자기 데이터 처리 방식(pubsub/websocket/queue 등)에 맞춰 이 runner를
호출해주기만 하면 됨.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .config import DEFAULT_PARAMS, StrategyParams
from .ports import (
    IClockPort,
    IMarketDataPort,
    INotifier,
    IOrderExecutor,
    ITradeRecorder,
)
from .strategy import CrossingStrategy5MImprove
from .types import CrossingEvent, TradeSignal

logger = logging.getLogger(__name__)


class CrossingRunner:
    """
    전략 + 포트들을 묶는 thin facade.

    repo B가 해줘야 하는 일:
      1. 자기 crossing 이벤트 → CrossingEvent로 변환해서 `handle_crossing()` 호출
      2. 캔들 경계 시점에 `handle_candle_boundary()` 호출
      3. ports 구현체 5개 주입
    """

    def __init__(
        self,
        coin: str,
        timeframe: str,
        clock: IClockPort,
        market_data: IMarketDataPort,
        executor: IOrderExecutor,
        recorder: ITradeRecorder,
        notifier: INotifier,
        params: StrategyParams = DEFAULT_PARAMS,
    ):
        self.coin = coin
        self.timeframe = timeframe
        self._clock = clock
        self._md = market_data
        self._exec = executor
        self._rec = recorder
        self._notify = notifier
        self._p = params

        self.strategy = CrossingStrategy5MImprove(clock=clock, params=params)

        # Low-vol filter: 최근 N캔들 crossing 수. recorder.get_recent_crossing_counts로
        # 서비스 시작 시 로드, 캔들 경계마다 append.
        self._recent_crossing_counts: list[int] = []

    # ------------------------------------------------------------------
    # Boot
    # ------------------------------------------------------------------
    async def load_recent_crossing_counts(self) -> None:
        counts = await self._rec.get_recent_crossing_counts(
            self.coin, limit=self._p.low_vol_history_size
        )
        self._recent_crossing_counts = counts[-self._p.low_vol_history_size:]
        logger.info(
            f"[{self.coin}] low-vol history: {self._recent_crossing_counts}"
        )

    # ------------------------------------------------------------------
    # Event: candle boundary (5m 마다)
    # ------------------------------------------------------------------
    async def handle_candle_boundary(
        self,
        ended_candle_start: Optional[datetime],
        ended_candle_end: Optional[datetime],
    ) -> None:
        """
        캔들이 닫혔을 때 호출.
        순서: stats flush → 상태 리셋.
        """
        # 1. 직전 캔들 통계 저장 + 히스토리 갱신
        for candle_key, state in list(self.strategy._candle_state.items()):
            crossing_count = len(state.crossing_times)
            trade_count = state.count

            await self._rec.save_crossing_stats(
                coin=self.coin,
                timeframe=self.timeframe,
                candle_key=candle_key,
                candle_start=ended_candle_start,
                candle_end=ended_candle_end,
                crossing_count=crossing_count,
                trade_count=trade_count,
            )
            self._recent_crossing_counts.append(crossing_count)

        # 윈도우 유지
        if len(self._recent_crossing_counts) > self._p.low_vol_history_size:
            self._recent_crossing_counts = self._recent_crossing_counts[
                -self._p.low_vol_history_size:
            ]

        # 2. 전략 상태 리셋
        self.strategy.reset_all()

    # ------------------------------------------------------------------
    # Event: crossing
    # ------------------------------------------------------------------
    async def handle_crossing(self, event: CrossingEvent) -> None:
        if event.coin != self.coin or event.timeframe != self.timeframe:
            return

        # 캔들 종료 지났으면 skip
        now = self._clock.now_utc()
        if now >= event.candle_end:
            return

        elapsed_seconds = event.elapsed_ms // 1000
        side = event.direction  # "up" | "down"

        orderbook = self._md.get_orderbook(self.coin, self.timeframe, side)
        if not orderbook:
            logger.warning(f"[{self.coin}] crossing but no {side} orderbook")
            return

        token_id = orderbook.token_id
        if not token_id:
            logger.error(f"[{self.coin}] no token_id for {side}")
            return

        candle_key = self._make_candle_key(event.candle_start)

        # 기록 (통계용 - 필터와 무관)
        self.strategy.record_crossing_time(
            candle_key, elapsed_seconds, event.direction
        )

        # High-volatility filter
        if self._is_high_volatility():
            logger.info(
                f"[{self.coin}] HIGH_VOL_SKIP: prev={self._recent_crossing_counts} "
                f"@{elapsed_seconds}s"
            )
            return

        signal = self.strategy.process_crossing_event(
            elapsed_seconds=elapsed_seconds,
            crossing_direction=event.direction,
            candle_key=candle_key,
        )

        crossing_info = self._build_crossing_info(event, elapsed_seconds)

        if signal:
            asyncio.create_task(
                self._execute(
                    signal=signal,
                    token_id=token_id,
                    candle_start=event.candle_start,
                    candle_end=event.candle_end,
                    candle_key=candle_key,
                    crossing_info=crossing_info,
                )
            )
        else:
            # cooltime 중 pending으로 저장됐을 수 있음
            self.strategy.set_pending_crossing_info(candle_key, crossing_info)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    async def _execute(
        self,
        signal: TradeSignal,
        token_id: str,
        candle_start: datetime,
        candle_end: datetime,
        candle_key: str,
        crossing_info: Dict[str, Any],
    ) -> None:
        # 동시주문 방지: 주문 전 cooltime 선걸기
        self.strategy.start_cooltime(candle_key, signal.side)

        order_result = await self._exec.place_gtc(
            coin=self.coin,
            token_id=token_id,
            price=self._p.gtc_fixed_price,
            contracts=signal.contracts,
        )

        await self._rec.record_trade(
            coin=self.coin,
            timeframe=self.timeframe,
            signal=signal,
            order_result=order_result,
            candle_start=candle_start,
            candle_end=candle_end,
            crossing_info=crossing_info,
            latency_ms=order_result.total_latency_ms,
        )

        self._notify_result(signal, order_result, crossing_info)

        # 성공/실패 관계없이 cooltime 후 pending 체크 타이머 기동
        # (실패해도 다음 crossing 계속 응답)
        if order_result.success:
            asyncio.create_task(
                self._cooltime_timer(
                    candle_key=candle_key,
                    candle_start=candle_start,
                    candle_end=candle_end,
                )
            )

    async def _cooltime_timer(
        self,
        candle_key: str,
        candle_start: datetime,
        candle_end: datetime,
    ) -> None:
        await asyncio.sleep(self._p.cooltime_seconds)

        now = self._clock.now_utc()
        if now >= candle_end:
            return

        elapsed = int((now - candle_start).total_seconds())
        signal, pending_info = self.strategy.check_pending_after_cooltime(
            candle_key, elapsed
        )
        if not signal:
            return

        orderbook = self._md.get_orderbook(self.coin, self.timeframe, signal.side)
        if not orderbook or not orderbook.token_id:
            logger.warning(
                f"[{self.coin}] cooltime timer: no orderbook/token for {signal.side}"
            )
            return

        info = pending_info.copy() if pending_info else {
            "direction": signal.side,
            "elapsed_seconds": elapsed,
            "elapsed_ms": elapsed * 1000,
        }
        info["from_cooltime_timer"] = True

        await self._execute(
            signal=signal,
            token_id=orderbook.token_id,
            candle_start=candle_start,
            candle_end=candle_end,
            candle_key=candle_key,
            crossing_info=info,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _make_candle_key(self, candle_start: datetime) -> str:
        return f"{self.coin}_{self.timeframe}_{candle_start.isoformat()}"

    def _is_high_volatility(self) -> bool:
        if len(self._recent_crossing_counts) < self._p.low_vol_history_size:
            return False
        return any(
            c > self._p.low_vol_threshold for c in self._recent_crossing_counts
        )

    def _build_crossing_info(
        self, event: CrossingEvent, elapsed_seconds: int
    ) -> Dict[str, Any]:
        return {
            "direction": event.direction,
            "candle_open": event.candle_open,
            "prev_price": event.prev_price,
            "current_price": event.current_price,
            "elapsed_seconds": elapsed_seconds,
            "elapsed_ms": event.elapsed_ms,
            "prev_elapsed_ms": event.prev_elapsed_ms,
            "curr_elapsed_ms": event.curr_elapsed_ms,
        }

    def _notify_result(
        self,
        signal: TradeSignal,
        result,
        crossing_info: Dict[str, Any],
    ) -> None:
        cross_dir = (crossing_info.get("direction") or "?").upper()
        candle_open = crossing_info.get("candle_open") or 0
        prev_p = crossing_info.get("prev_price") or 0
        curr_p = crossing_info.get("current_price") or 0

        entry_num = ""
        if "#" in signal.reason:
            try:
                entry_num = "#" + signal.reason.split("#")[1].split()[0]
            except Exception:
                pass

        if result.success:
            status = "OK" if result.filled_contracts >= result.target_contracts else "PARTIAL"
            self._notify.notify(
                f"[5M] {entry_num} {cross_dir} | open {candle_open}\n"
                f"{prev_p} -> {curr_p}\n"
                f"x{signal.contracts} @{result.fill_price:.2f} "
                f"{result.filled_contracts:.0f}/{result.target_contracts} {status} | "
                f"{result.total_latency_ms:.0f}ms"
            )
        else:
            self._notify.notify(
                f"[5M] {entry_num} {cross_dir} FAILED: {result.error} | "
                f"{result.total_latency_ms:.0f}ms"
            )
