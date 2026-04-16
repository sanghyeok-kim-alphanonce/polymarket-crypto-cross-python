"""
순수 전략 코어 유닛 테스트.

외부 의존성 없이 FakeClock만으로 결정론적 검증.
시나리오:
  1. 첫 진입: x10
  2. 2~4회차: x20 (방향 전환 시)
  3. HEDGE_TIME: 250s 이후 x10 + hedged=True
  4. HEDGE_MAX5: 5번째 진입은 x10 + hedged
  5. HEDGE_SPEED: 20s 내 3회 crossing → x10 + hedged
  6. 290s+ 금지 구간: None 반환
  7. 같은 방향 연속: skip
  8. Cooltime 중 반대방향 pending → cooltime 후 실행
"""
from datetime import datetime, timezone

import pytest

from cross_5m_improve_strategy.strategy import CrossingStrategy5MImprove
from cross_5m_improve_strategy.config import StrategyParams


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self._t = start

    def monotonic(self) -> float:
        return self._t

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def advance(self, seconds: float) -> None:
        self._t += seconds


CANDLE_KEY = "btc_5m_2026-04-15T00:00:00+00:00"


def make_strategy():
    return CrossingStrategy5MImprove(clock=FakeClock()), FakeClock()


def test_first_entry_is_x10():
    strat = CrossingStrategy5MImprove(clock=FakeClock())
    sig = strat.process_crossing_event(
        elapsed_seconds=10,
        crossing_direction="up",
        candle_key=CANDLE_KEY,
    )
    assert sig is not None
    assert sig.side == "up"
    assert sig.contracts == 10
    assert "CROSS5M_UP #1" in sig.reason


def test_second_entry_opposite_direction_is_x20():
    clock = FakeClock()
    strat = CrossingStrategy5MImprove(clock=clock)

    # 1st: up
    sig1 = strat.process_crossing_event(10, "up", CANDLE_KEY)
    assert sig1.contracts == 10
    strat.start_cooltime(CANDLE_KEY, "up")

    # cooltime 지나고 반대방향
    clock.advance(2.0)
    sig2 = strat.process_crossing_event(30, "down", CANDLE_KEY)
    assert sig2 is not None
    assert sig2.contracts == 20
    assert sig2.side == "down"
    assert "#2" in sig2.reason


def test_same_direction_repeat_is_skipped():
    clock = FakeClock()
    strat = CrossingStrategy5MImprove(clock=clock)

    strat.process_crossing_event(10, "up", CANDLE_KEY)
    strat.start_cooltime(CANDLE_KEY, "up")
    clock.advance(2.0)

    # 같은 방향 반복 → None
    sig = strat.process_crossing_event(30, "up", CANDLE_KEY)
    assert sig is None


def test_hedge_time_after_250s():
    strat = CrossingStrategy5MImprove(clock=FakeClock())

    sig = strat.process_crossing_event(
        elapsed_seconds=260,
        crossing_direction="up",
        candle_key=CANDLE_KEY,
    )
    assert sig is not None
    assert sig.contracts == 10
    assert "HEDGE_TIME" in sig.reason

    # 이후 진입 전면 차단
    sig2 = strat.process_crossing_event(270, "down", CANDLE_KEY)
    assert sig2 is None


def test_forbidden_zone_after_290s():
    strat = CrossingStrategy5MImprove(clock=FakeClock())
    sig = strat.process_crossing_event(291, "up", CANDLE_KEY)
    assert sig is None


def test_hedge_max5():
    clock = FakeClock()
    strat = CrossingStrategy5MImprove(clock=clock)

    # 4번 정상 진입 (방향 바꿔가며)
    directions = ["up", "down", "up", "down"]
    for i, d in enumerate(directions):
        sig = strat.process_crossing_event(10 + i * 5, d, CANDLE_KEY)
        assert sig is not None
        strat.start_cooltime(CANDLE_KEY, d)
        clock.advance(2.0)

    # 5번째는 HEDGE_MAX5
    sig5 = strat.process_crossing_event(50, "up", CANDLE_KEY)
    assert sig5 is not None
    assert sig5.contracts == 10
    assert "HEDGE_MAX5" in sig5.reason

    # 6번째는 차단
    clock.advance(2.0)
    strat.start_cooltime(CANDLE_KEY, "up")
    sig6 = strat.process_crossing_event(60, "down", CANDLE_KEY)
    assert sig6 is None


def test_hedge_speed_filter():
    clock = FakeClock()
    strat = CrossingStrategy5MImprove(clock=clock)

    # 통계 기록 (필터 판정용). 실제 사용 시 runner가 record_crossing_time 호출.
    # 20s 내 3회: 10s, 20s, 25s 에 crossing 기록
    strat.record_crossing_time(CANDLE_KEY, 10, "up")
    strat.record_crossing_time(CANDLE_KEY, 20, "down")
    strat.record_crossing_time(CANDLE_KEY, 25, "up")

    # 25초 시점에 crossing 이벤트 처리 → HEDGE_SPEED
    sig = strat.process_crossing_event(25, "up", CANDLE_KEY)
    assert sig is not None
    assert "HEDGE_SPEED" in sig.reason
    assert sig.contracts == 10


def test_cooltime_pending_opposite_direction():
    clock = FakeClock()
    strat = CrossingStrategy5MImprove(clock=clock)

    # 1st trade
    sig1 = strat.process_crossing_event(10, "up", CANDLE_KEY)
    assert sig1 is not None
    strat.start_cooltime(CANDLE_KEY, "up")

    # cooltime 중 반대방향 crossing → pending 저장, return None
    sig_mid = strat.process_crossing_event(15, "down", CANDLE_KEY)
    assert sig_mid is None

    # cooltime 만료 후 타이머가 check_pending_after_cooltime 호출
    clock.advance(1.5)
    signal, info = strat.check_pending_after_cooltime(CANDLE_KEY, 20)
    assert signal is not None
    assert signal.side == "down"
    assert signal.contracts == 20


def test_cooltime_pending_same_direction_is_dropped():
    clock = FakeClock()
    strat = CrossingStrategy5MImprove(clock=clock)

    strat.process_crossing_event(10, "up", CANDLE_KEY)
    strat.start_cooltime(CANDLE_KEY, "up")

    # 같은 방향 pending
    strat.process_crossing_event(15, "up", CANDLE_KEY)

    clock.advance(1.5)
    signal, _ = strat.check_pending_after_cooltime(CANDLE_KEY, 20)
    assert signal is None


def test_reset_all_clears_state():
    strat = CrossingStrategy5MImprove(clock=FakeClock())
    strat.process_crossing_event(10, "up", CANDLE_KEY)
    assert strat.get_state(CANDLE_KEY).count == 1

    strat.reset_all()
    assert strat.get_state(CANDLE_KEY).count == 0


def test_failed_candle_blocks_further_signals():
    strat = CrossingStrategy5MImprove(clock=FakeClock())
    strat.mark_failed(CANDLE_KEY)
    sig = strat.process_crossing_event(10, "up", CANDLE_KEY)
    assert sig is None


def test_custom_params_override():
    params = StrategyParams(
        crossing_bet_contract_unit=5,
        crossing_max_count=3,
        speed_filter_max_crossings=2,
    )
    strat = CrossingStrategy5MImprove(clock=FakeClock(), params=params)

    sig = strat.process_crossing_event(10, "up", CANDLE_KEY)
    assert sig.contracts == 5
