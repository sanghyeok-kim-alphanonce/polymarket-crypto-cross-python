"""
Crossing V2 Strategy: 15분봉 시작가 crossing 전략 (무제한 버전)

가격이 15분봉 시작가(open)를 교차할 때 진입:
- Crossing UP (아래→위): UP token taker 매수
- Crossing DOWN (위→아래): DOWN token taker 매수
- 첫 번째 crossing: $10
- 이후 crossing: $20
- 횟수 제한 없음 (무제한)
- 14분 전까지 베팅
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any
from .base import BaseStrategy, TradeSignal


@dataclass
class CrossingV2Config:
    """Crossing V2 전략 설정"""
    first_bet_amount: float = 10.0  # 첫 crossing 베팅 금액
    subsequent_bet_amount: float = 20.0  # 이후 crossing 베팅 금액
    cutoff_seconds: int = 840  # 14분 = 840초 (이후 베팅 중지)
    min_entry_price: float = 0.10  # 최소 진입 가격 (너무 싼 가격 방지)
    max_entry_price: float = 0.90  # 최대 진입 가격 (너무 비싼 가격 방지)


class CrossingV2Strategy(BaseStrategy):
    """15분봉 시작가 Crossing V2 전략 (무제한 횟수)"""

    name = "crossing_v2"
    description = "15분봉 시작가 crossing 시 방향 베팅 ($10/$20, 무제한, ~14:00)"

    def __init__(self, config: Optional[CrossingV2Config] = None):
        super().__init__()
        self.config = config or CrossingV2Config()

        # 캔들별 상태: {candle_key: {"total_trades": int, "last_direction": str}}
        self.candle_state: Dict[str, Dict[str, Any]] = {}

    def _get_candle_state(self, candle_key: str) -> Dict[str, Any]:
        """캔들별 상태 가져오기 (없으면 초기화)"""
        if candle_key not in self.candle_state:
            self.candle_state[candle_key] = {
                "total_trades": 0,  # 전체 거래 횟수 (금액 결정용)
                "last_direction": None,
            }
        return self.candle_state[candle_key]

    def should_enter_on_crossing(
        self,
        coin: str,
        timeframe: str,
        elapsed_seconds: int,
        crossing_direction: str,  # "up" or "down"
        up_orderbook: Optional[Dict[str, Any]],
        down_orderbook: Optional[Dict[str, Any]],
        candle_key: str,
    ) -> Optional[TradeSignal]:
        """
        Crossing 이벤트 발생 시 진입 여부 판단

        Args:
            coin: 코인 (btc, eth, etc.)
            timeframe: 타임프레임 (15m)
            elapsed_seconds: 캔들 시작 후 경과 시간 (초)
            crossing_direction: "up" or "down"
            up_orderbook: UP 토큰 오더북
            down_orderbook: DOWN 토큰 오더북
            candle_key: 캔들 식별자

        Returns:
            TradeSignal if should enter, None otherwise
        """
        cfg = self.config
        state = self._get_candle_state(candle_key)

        # 1. 시간 체크: cutoff 이후 베팅 중지 (14분 = 840초)
        if elapsed_seconds >= cfg.cutoff_seconds:
            return None

        # V2: 횟수 제한 체크 없음 (무제한)

        # 2. 방향에 따른 오더북 선택
        if crossing_direction == "up":
            orderbook = up_orderbook
            side = "up"
        else:
            orderbook = down_orderbook
            side = "down"

        if not orderbook:
            return None

        # 3. 진입 가격 확인 (taker = best_ask)
        entry_price = orderbook.get("best_ask")
        if entry_price is None:
            return None

        # 4. 가격 범위 체크
        if entry_price < cfg.min_entry_price or entry_price > cfg.max_entry_price:
            return None

        # 5. 베팅 금액 결정 (첫 거래 $10, 두 번째부터 $20)
        is_first_trade = state["total_trades"] == 0
        bet_amount = cfg.first_bet_amount if is_first_trade else cfg.subsequent_bet_amount

        # 6. 계약 수 계산
        contracts = int(bet_amount / entry_price)
        if contracts <= 0:
            return None

        # 7. 상태 업데이트
        state["total_trades"] += 1
        state["last_direction"] = crossing_direction

        # 8. 이유 생성
        trade_num = state["total_trades"]
        reason = (
            f"CROSSv2_{crossing_direction.upper()} T{trade_num} "
            f"${bet_amount:.0f} @{entry_price:.3f} "
            f"elapsed={elapsed_seconds}s"
        )

        return TradeSignal(
            side=side,
            reason=reason,
            contracts=contracts,
            price=entry_price,
        )

    def should_enter(
        self,
        coin: str,
        timeframe: str,
        elapsed_min: int,
        up_orderbook: Optional[Dict[str, Any]],
        down_orderbook: Optional[Dict[str, Any]],
        candle_key: str,
        **kwargs,
    ) -> Optional[TradeSignal]:
        """기존 인터페이스 호환용 - Crossing 전략에서는 사용 안함"""
        return None

    def reset_for_candle(self, candle_key: str):
        """캔들 시작 시 상태 리셋"""
        self.candle_state[candle_key] = {
            "total_trades": 0,
            "last_direction": None,
        }

    def cleanup_old_candles(self, current_candle_key: str):
        """오래된 캔들 상태 정리 (메모리 관리)"""
        keys_to_remove = [k for k in self.candle_state if k != current_candle_key]
        for k in keys_to_remove:
            del self.candle_state[k]

    def get_status(self) -> Dict[str, Any]:
        """현재 상태 반환"""
        return {
            "candle_states": {
                k: {"trades": v["total_trades"], "last_dir": v["last_direction"]}
                for k, v in self.candle_state.items()
            }
        }
