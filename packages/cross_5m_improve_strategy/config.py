"""
전략 파라미터 (순수 상수).

os.getenv 없음. 인프라 설정은 repo B의 설정 시스템에서 관리.
이 값을 바꾸고 싶으면 dataclass(StrategyParams) 로 override 주입받는 형태로 확장 가능.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyParams:
    """
    기본값은 현 production(real_trader_5m_improve)과 동일.
    repo B에서 인스턴스화 시 override 가능.
    """
    # 베팅 단위
    crossing_bet_contract_unit: int = 10
    crossing_max_count: int = 5

    # 시간대 컷오프 (초)
    crossing_min_elapsed_seconds: int = 0
    crossing_entry_cutoff_seconds: int = 250   # 0~250s: 정상 진입
    crossing_hedge_cutoff_seconds: int = 290   # 250~290s: 헤지만 / 290+: 금지
    crossing_cutoff_seconds: int = 300         # 캔들 길이

    # 속도 필터
    speed_filter_window_seconds: int = 20
    speed_filter_max_crossings: int = 3

    # Cooltime
    cooltime_seconds: float = 1.0

    # GTC
    gtc_fixed_price: float = 0.80

    # Low volatility filter (이전 N캔들 중 crossing > threshold 면 skip)
    low_vol_history_size: int = 2
    low_vol_threshold: int = 3


DEFAULT_PARAMS = StrategyParams()
