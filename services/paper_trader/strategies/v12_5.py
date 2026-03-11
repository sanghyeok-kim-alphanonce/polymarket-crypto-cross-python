"""
V12-5 Strategy - Binance Price Delta Based Momentum

핵심 아이디어 (reference code 기반):
- Binance 가격 delta 기반 진입: 가격 상승 → UP, 가격 하락 → DOWN
- Entry Price >= 0.35 필터 (싼 가격 = 역행 베팅 = 손실)
- 전반(<=10분): 높은 threshold ($30 BTC, $1 ETH 등)
- 후반(>10분): Entry Price >= 0.55 + 낮은 threshold
- Cooldown으로 과다 거래 방지

Delta 계산:
- 1초 전 Binance close 가격과 현재 close 가격 비교
- delta = curr_price - prev_price (절대값 $)
- threshold는 $ 단위 (비율 아님)
"""
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass

from .base import BaseStrategy, TradeSignal


@dataclass
class V12_5Config:
    """V12-5 전략 설정"""
    # Entry Price 필터
    min_entry_price: float = 0.35  # 필수: 이보다 싼 가격은 진입 금지
    min_entry_price_late: float = 0.55  # 후반(>=10분) 진입시 필수

    # Elapsed Time 구분
    early_cutoff_minutes: int = 10  # 이하면 전반, 초과면 후반

    # 전반 Threshold ($ 단위, Binance 가격 delta)
    thresholds_early: Dict[str, float] = None
    # 후반 Threshold ($ 단위, Binance 가격 delta)
    thresholds_late: Dict[str, float] = None

    # 매매 설정
    bet_amount: float = 1.0  # $ per trade
    cooldown_seconds: float = 5.0  # Cooldown (초)

    # 필터
    max_spread: float = 0.05  # 스프레드 5% 이하
    min_liquidity: int = 10  # 최소 유동성

    name: str = "v12_5"

    def __post_init__(self):
        if self.thresholds_early is None:
            # $ 단위 threshold (Binance 가격 delta)
            self.thresholds_early = {
                'btc': 30.0,   # >= $30
                'eth': 1.0,    # >= $1.0
                'sol': 0.03,   # >= $0.03
                'xrp': 0.0005, # >= $0.0005
            }
        if self.thresholds_late is None:
            self.thresholds_late = {
                'btc': 1.0,    # >= $1
                'eth': 0.1,    # >= $0.1
                'sol': 0.02,   # >= $0.02
                'xrp': 0.0001, # >= $0.0001
            }


class V12_5Strategy(BaseStrategy):
    """
    V12-5 Strategy - Binance Price Delta Based Momentum

    Binance 가격 delta를 감지해서 모멘텀 방향으로 진입:
    - Binance 가격 상승 → UP 토큰 매수
    - Binance 가격 하락 → DOWN 토큰 매수
    """

    def __init__(self, cfg: V12_5Config = None):
        super().__init__()
        self.config = cfg or V12_5Config()
        self.name = self.config.name
        self.description = (
            f"V12-5 Binance Delta (EP≥{self.config.min_entry_price}, "
            f"late EP≥{self.config.min_entry_price_late}, $ threshold, "
            f"bet=${self.config.bet_amount})"
        )

        # 코인별 마지막 거래 시간: {coin: timestamp}
        self._last_trade_time: Dict[str, float] = {}

        # 현재 캔들 키 (캔들 변경 감지)
        self._current_candle: str = None

    def reset_for_candle(self, candle_key: str):
        """캔들 시작 시 상태 리셋"""
        self._current_candle = candle_key
        # 캔들 변경 시 prev_prices 유지 (delta 계산 연속성)
        # cooldown만 리셋
        self._last_trade_time.clear()

    def _check_cooldown(self, coin: str, sim_time: float = None) -> bool:
        """Cooldown 체크 - True면 거래 가능

        Args:
            coin: 코인
            sim_time: 시뮬레이션 시간 (unix timestamp). None이면 time.time() 사용 (실시간 모드)
        """
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
        delta_1s: Optional[float] = None,  # binance_ws에서 계산한 1초 delta ($)
        sim_time: Optional[float] = None,  # 시뮬레이션 시간 (backtest용)
    ) -> Optional[TradeSignal]:
        # 캔들 변경 체크
        if candle_key != self._current_candle:
            self.reset_for_candle(candle_key)

        # delta_1s 필수 (binance_ws에서 계산된 값)
        if delta_1s is None or delta_1s == 0:
            return None

        # 오더북 필수
        if not up_orderbook:
            return None

        # Cooldown 체크 (sim_time 전달)
        if not self._check_cooldown(coin, sim_time):
            return None

        # 전반/후반 구분
        is_late = elapsed_min > self.config.early_cutoff_minutes

        # Threshold 선택 ($ 단위)
        if is_late:
            threshold = self.config.thresholds_late.get(coin)
        else:
            threshold = self.config.thresholds_early.get(coin)

        if threshold is None:
            return None

        # Threshold 체크 (절대값 비교)
        if abs(delta_1s) < threshold:
            return None

        # 방향 결정: Binance 가격 상승 → UP, Binance 가격 하락 → DOWN
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

        # 비정상 가격 필터 (오더북 비어있을 때 best_ask=1.0)
        if entry_price >= 0.95:
            return None

        # === Entry Price 필터 ===
        # 필수: Entry Price >= 0.35
        if entry_price < self.config.min_entry_price:
            return None

        # 후반: Entry Price >= 0.55 필수
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

        # 매수 수량 계산: BET_AMOUNT / entry_price
        contracts = int(self.config.bet_amount / entry_price)
        if contracts <= 0:
            return None

        # 거래 기록 (sim_time 사용)
        self._last_trade_time[coin] = sim_time if sim_time is not None else time.time()

        phase = "late" if is_late else "early"
        delta_sign = "+" if delta_1s > 0 else ""

        return TradeSignal(
            side=side,
            reason=f"v12_5_{phase}_delta{delta_sign}${delta_1s:.4f}(>=${threshold})",
            contracts=contracts,
            price=entry_price,
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            'cooldowns': {k: round(time.time() - v, 1) for k, v in self._last_trade_time.items()},
        }
