"""
V12-6 Strategy - Binance Price Delta + Momentum Match

V12-5 기반 + Momentum Match 조건 추가:
- delta 방향과 distance 방향이 일치할 때만 진입
- distance = current_price - candle_open_price

백테스트 결과:
- Momentum Match 없음: ~77% 승률
- Momentum Match 있음: ~90% 승률

핵심 인사이트:
- delta_1s > 0 AND distance > 0 → UP 진입 (상승 추세 + 상승 신호)
- delta_1s < 0 AND distance < 0 → DOWN 진입 (하락 추세 + 하락 신호)
- 역행 신호 (delta와 distance 방향 불일치)는 무시 → 승률 크게 향상
"""
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass

from .base import BaseStrategy, TradeSignal


@dataclass
class V12_6Config:
    """V12-6 전략 설정"""
    # Entry Price 필터
    min_entry_price: float = 0.35
    min_entry_price_late: float = 0.55

    # Elapsed Time 구분
    early_cutoff_minutes: int = 10

    # Threshold ($ 단위, Binance 가격 delta)
    thresholds_early: Dict[str, float] = None
    thresholds_late: Dict[str, float] = None

    # Momentum Match (핵심 변경점)
    require_momentum_match: bool = True

    # 매매 설정
    bet_amount: float = 1.0
    cooldown_seconds: float = 5.0

    # 필터
    max_spread: float = 0.05
    min_liquidity: int = 10

    name: str = "v12_6"

    def __post_init__(self):
        if self.thresholds_early is None:
            # Momentum match 덕분에 더 낮은 threshold 가능
            self.thresholds_early = {
                'btc': 20.0,   # >= $20 (v12_5는 $30)
                'eth': 0.8,    # >= $0.8
                'sol': 0.025,  # >= $0.025
                'xrp': 0.0004, # >= $0.0004
            }
        if self.thresholds_late is None:
            self.thresholds_late = {
                'btc': 1.0,
                'eth': 0.1,
                'sol': 0.02,
                'xrp': 0.0001,
            }


class V12_6Strategy(BaseStrategy):
    """
    V12-6 Strategy - Binance Price Delta + Momentum Match

    V12-5 + Momentum Match:
    - delta 방향 == distance 방향일 때만 진입
    - 역행 신호 무시로 승률 ~90%
    """

    def __init__(self, cfg: V12_6Config = None):
        super().__init__()
        self.config = cfg or V12_6Config()
        self.name = self.config.name
        self.description = (
            f"V12-6 Momentum (EP>={self.config.min_entry_price}, "
            f"momentum_match={self.config.require_momentum_match}, "
            f"bet=${self.config.bet_amount})"
        )

        self._last_trade_time: Dict[str, float] = {}
        self._current_candle: str = None

    def reset_for_candle(self, candle_key: str):
        """캔들 시작 시 상태 리셋"""
        self._current_candle = candle_key
        self._last_trade_time.clear()

    def _check_cooldown(self, coin: str, sim_time: float = None) -> bool:
        """Cooldown 체크 - True면 거래 가능"""
        if coin not in self._last_trade_time:
            return True
        now = sim_time if sim_time is not None else time.time()
        elapsed = now - self._last_trade_time[coin]
        return elapsed >= self.config.cooldown_seconds

    def should_enter(
        self,
        coin: str,
        timeframe: str,
        elapsed_min: int,
        up_orderbook: Optional[Dict[str, Any]],
        down_orderbook: Optional[Dict[str, Any]],
        candle_key: str,
        delta_1s: Optional[float] = None,
        sim_time: Optional[float] = None,
        candle_open: Optional[float] = None,  # 캔들 시작가 (momentum match용)
        current_price: Optional[float] = None,  # 현재가 (momentum match용)
    ) -> Optional[TradeSignal]:
        # 캔들 변경 체크
        if candle_key != self._current_candle:
            self.reset_for_candle(candle_key)

        # delta_1s 필수
        if delta_1s is None or delta_1s == 0:
            return None

        # 오더북 필수
        if not up_orderbook:
            return None

        # Cooldown 체크
        if not self._check_cooldown(coin, sim_time):
            return None

        # === Momentum Match 체크 (핵심 변경점) ===
        if self.config.require_momentum_match:
            if candle_open is None or current_price is None:
                return None

            distance = current_price - candle_open

            # delta > 0 (상승 신호) but distance <= 0 (하락 추세) → 역행, 무시
            if delta_1s > 0 and distance <= 0:
                return None

            # delta < 0 (하락 신호) but distance >= 0 (상승 추세) → 역행, 무시
            if delta_1s < 0 and distance >= 0:
                return None

        # 전반/후반 구분
        is_late = elapsed_min > self.config.early_cutoff_minutes

        # Threshold 선택
        if is_late:
            threshold = self.config.thresholds_late.get(coin)
        else:
            threshold = self.config.thresholds_early.get(coin)

        if threshold is None:
            return None

        # Threshold 체크
        if abs(delta_1s) < threshold:
            return None

        # 방향 결정
        if delta_1s > 0:
            side = 'up'
            orderbook = up_orderbook
        else:
            side = 'down'
            orderbook = down_orderbook

        if not orderbook:
            return None

        entry_price = orderbook.get('best_ask', 0)
        if entry_price <= 0:
            return None

        # 비정상 가격 필터
        if entry_price >= 0.95:
            return None

        # Entry Price 필터
        if entry_price < self.config.min_entry_price:
            return None

        if is_late and entry_price < self.config.min_entry_price_late:
            return None

        # 스프레드 체크
        best_bid = orderbook.get('best_bid', 0)
        spread = entry_price - best_bid
        if spread > self.config.max_spread:
            return None

        # 유동성 체크
        liquidity = orderbook.get('best_ask_size', 0)
        if liquidity < self.config.min_liquidity:
            return None

        # 매수 수량 계산
        contracts = int(self.config.bet_amount / entry_price)
        if contracts <= 0:
            return None

        # 거래 기록
        self._last_trade_time[coin] = sim_time if sim_time is not None else time.time()

        phase = "late" if is_late else "early"
        delta_sign = "+" if delta_1s > 0 else ""
        distance_info = ""
        if candle_open is not None and current_price is not None:
            dist = current_price - candle_open
            distance_info = f"_dist{'+' if dist > 0 else ''}{dist:.1f}"

        return TradeSignal(
            side=side,
            reason=f"v12_6_{phase}_delta{delta_sign}${delta_1s:.4f}(>=${threshold}){distance_info}",
            contracts=contracts,
            price=entry_price,
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            'cooldowns': {k: round(time.time() - v, 1) for k, v in self._last_trade_time.items()},
            'require_momentum_match': self.config.require_momentum_match,
        }
