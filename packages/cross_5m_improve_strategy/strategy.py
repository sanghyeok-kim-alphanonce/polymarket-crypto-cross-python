"""
Pure strategy core for cross_5m_improve.

===== 설계 원칙 =====
- 외부 의존성 0개 (표준 라이브러리만)
- 순수 함수에 가까운 상태머신 (입력: elapsed + direction + state → 출력: TradeSignal or None)
- 시계는 ClockPort로 주입 (time.time() 직접 호출 금지)
- DB/Redis/CLOB/Telegram 등 모름

===== 전략 요약 =====
- 진입 구간 0~250s: #1=10, #2~4=20
- 헤지 구간 250~290s: 10 (HEDGE_TIME)
- 금지 구간 290~300s: 진입 없음
- Max5: 5번째 진입은 10 (HEDGE_MAX5) 후 캔들 잠금
- Speed filter: 최근 20s 내 crossing 3회 이상 → 10 (HEDGE_SPEED) 후 잠금
- Cooltime 1s: 거래 직후 들어온 crossing은 pending으로 저장, 반대방향이면 cooltime 종료 후 실행
"""
from typing import Any, Dict, Optional, Tuple

from .config import DEFAULT_PARAMS, StrategyParams
from .ports import IClockPort
from .types import CandleState, TradeSignal


class CrossingStrategy5MImprove:
    name = "crossing_5m_improve"
    description = "5분봉 crossing (Max5 + 속도필터 + 헤지 + 1s cooltime)"

    def __init__(
        self,
        clock: IClockPort,
        params: StrategyParams = DEFAULT_PARAMS,
    ):
        self._clock = clock
        self._p = params
        self._candle_state: Dict[str, CandleState] = {}

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------
    def get_state(self, candle_key: str) -> CandleState:
        """외부에서 상태 조회/초기화용 (runner가 통계 저장 시 사용)."""
        if candle_key not in self._candle_state:
            self._candle_state[candle_key] = CandleState()
        return self._candle_state[candle_key]

    def reset_all(self) -> None:
        """캔들 경계 시 호출. 모든 캔들 상태 초기화."""
        self._candle_state.clear()

    def record_crossing_time(
        self, candle_key: str, elapsed_seconds: int, direction: str
    ) -> None:
        """Speed filter/통계용 기록. 필터 통과 여부와 무관하게 항상 기록."""
        state = self.get_state(candle_key)
        state.crossing_times.append((elapsed_seconds, direction))

    def mark_failed(self, candle_key: str) -> None:
        self.get_state(candle_key).failed = True

    def snapshot(self) -> Dict[str, int]:
        return {
            k: v.count for k, v in self._candle_state.items() if v.count > 0
        }

    # ------------------------------------------------------------------
    # Main decision entry
    # ------------------------------------------------------------------
    def process_crossing_event(
        self,
        elapsed_seconds: int,
        crossing_direction: str,
        candle_key: str,
    ) -> Optional[TradeSignal]:
        """
        Crossing 이벤트 처리.
        returns TradeSignal if should trade, else None.
        """
        state = self.get_state(candle_key)
        now = self._clock.monotonic()

        if state.failed:
            return None

        # 290s+ 금지 구간
        if elapsed_seconds >= self._p.crossing_hedge_cutoff_seconds:
            return None

        if state.hedged:
            return None

        if elapsed_seconds < self._p.crossing_min_elapsed_seconds:
            return None

        # ----- Cooltime 상태머신 -----
        if state.in_cooltime:
            if now - state.cooltime_start >= self._p.cooltime_seconds:
                # cooltime 만료
                state.in_cooltime = False
                pending = state.pending_direction
                pending_elapsed = state.pending_elapsed
                state.pending_direction = None
                state.pending_elapsed = None

                # pending이 반대 방향이면 실행
                if pending and pending != state.last_trade_direction:
                    return self._create_trade_signal(
                        pending,
                        pending_elapsed if pending_elapsed is not None else elapsed_seconds,
                        candle_key,
                    )

                # 현재 crossing으로 판단
                if crossing_direction != state.last_trade_direction:
                    return self._create_trade_signal(
                        crossing_direction, elapsed_seconds, candle_key
                    )
                return None
            else:
                # cooltime 중 → pending 저장
                state.pending_direction = crossing_direction
                state.pending_elapsed = elapsed_seconds
                return None

        # ----- Cooltime 아님 → 방향 판단 -----
        if (
            state.last_trade_direction is None
            or crossing_direction != state.last_trade_direction
        ):
            return self._create_trade_signal(
                crossing_direction, elapsed_seconds, candle_key
            )

        return None

    # ------------------------------------------------------------------
    # Signal creation (헤지/수량 결정)
    # ------------------------------------------------------------------
    def _create_trade_signal(
        self,
        direction: str,
        elapsed_seconds: int,
        candle_key: str,
    ) -> TradeSignal:
        state = self.get_state(candle_key)
        side = direction  # "up" | "down"
        p = self._p

        is_hedge = False
        hedge_reason = ""

        if elapsed_seconds >= p.crossing_entry_cutoff_seconds:
            is_hedge = True
            hedge_reason = "HEDGE_TIME"
        elif state.count >= p.crossing_max_count - 1:
            is_hedge = True
            hedge_reason = "HEDGE_MAX5"
        elif (
            self._count_recent_crossings(state.crossing_times, elapsed_seconds)
            >= p.speed_filter_max_crossings
        ):
            is_hedge = True
            hedge_reason = "HEDGE_SPEED"

        state.count += 1

        if is_hedge:
            contracts = p.crossing_bet_contract_unit
            state.hedged = True
            reason = f"{hedge_reason}_{direction.upper()} #{state.count} @{elapsed_seconds}s"
        elif state.count == 1:
            contracts = p.crossing_bet_contract_unit
            reason = f"CROSS5M_{direction.upper()} #{state.count} @{elapsed_seconds}s"
        else:
            contracts = p.crossing_bet_contract_unit * 2
            reason = f"CROSS5M_{direction.upper()} #{state.count} @{elapsed_seconds}s"

        return TradeSignal(side=side, reason=reason, contracts=contracts)

    def _count_recent_crossings(
        self, crossing_times: list, current_elapsed: int
    ) -> int:
        cutoff = current_elapsed - self._p.speed_filter_window_seconds
        return sum(1 for t, _d in crossing_times if t >= cutoff)

    # ------------------------------------------------------------------
    # Cooltime hooks (runner가 주문 직전/후에 호출)
    # ------------------------------------------------------------------
    def start_cooltime(self, candle_key: str, direction: str) -> None:
        """주문 직전 호출. 이후 1초간 동시 주문 차단 + 같은 방향 중복 차단."""
        state = self.get_state(candle_key)
        state.in_cooltime = True
        state.cooltime_start = self._clock.monotonic()
        state.last_trade_direction = direction
        state.pending_direction = None
        state.pending_elapsed = None
        state.pending_crossing_info = None

    def set_pending_crossing_info(
        self, candle_key: str, crossing_info: Dict[str, Any]
    ) -> None:
        """Cooltime 중 들어온 crossing의 원본 정보 저장 (나중에 실행 시 로그/알림용)."""
        state = self.get_state(candle_key)
        if state.in_cooltime and state.pending_direction:
            state.pending_crossing_info = crossing_info

    def check_pending_after_cooltime(
        self, candle_key: str, elapsed_seconds: int
    ) -> Tuple[Optional[TradeSignal], Optional[Dict[str, Any]]]:
        """
        Cooltime 종료 타이머에서 호출.
        pending이 반대 방향이면 실행용 signal 반환.
        """
        state = self.get_state(candle_key)

        state.in_cooltime = False
        pending = state.pending_direction
        pending_elapsed = state.pending_elapsed
        pending_info = state.pending_crossing_info
        state.pending_direction = None
        state.pending_elapsed = None
        state.pending_crossing_info = None

        if state.failed or state.hedged:
            return None, None

        if elapsed_seconds >= self._p.crossing_hedge_cutoff_seconds:
            return None, None

        if pending and pending != state.last_trade_direction:
            signal = self._create_trade_signal(
                pending,
                pending_elapsed if pending_elapsed is not None else elapsed_seconds,
                candle_key,
            )
            return signal, pending_info

        return None, None
