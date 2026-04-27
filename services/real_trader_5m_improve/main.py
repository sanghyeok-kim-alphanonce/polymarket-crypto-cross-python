"""
Real Trader 5M Improve: 5분봉 Crossing 개선 전략

- 0초부터 crossing에 진입 (0s ~ 300s)
- 5회 제한, 4분 50초 이후 → 바로 5회차
- 1회차: 10, 2~4회차: 20, 5회차: 10
- 밸런싱 없음 (순수 방향 베팅)
- GTC 0.80 고정
- 거래 후 1초 cooltime
- Redis subscribe: ch:crossing:*, ch:orderbook:*, ch:candle_boundary:5m
- 텔레그램 알림
- BTC only
"""
import asyncio
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional, Any

import aiohttp

from config import (
    STRATEGY_NAME, COINS, TIMEFRAMES,
    CROSSING_BET_CONTRACT_UNIT,
    CROSSING_MAX_COUNT, CROSSING_MIN_ELAPSED_SECONDS, CROSSING_CUTOFF_SECONDS,
    CROSSING_ENTRY_CUTOFF_SECONDS, CROSSING_HEDGE_CUTOFF_SECONDS,
    SPEED_FILTER_WINDOW_SECONDS, SPEED_FILTER_MAX_CROSSINGS,
    COOLTIME_SECONDS,
    GTC_FIXED_PRICE,
    BOOK_DEPTH_REQUIRED, BOOK_DEPTH_SAFETY, BOOK_DEPTH_PENDING_ENABLED,
    SIGNALS_LOG_PATH,
    POLYMARKET_HOST, POLYMARKET_CHAIN_ID,
    POLYMARKET_PRIVATE_KEY, POLYMARKET_PROXY_ADDRESS,
    POLYMARKET_BUILDER_CODE,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    REDIS_HOST, REDIS_PORT,
    STATS_INTERVAL,
)

# Packages
from polymarket_common import generate_slug
from service_common import AsyncServiceBase, setup_logging

logger = setup_logging(__name__)


# =============================================================================
# Crossing Strategy (5M Front)
# =============================================================================
@dataclass
class TradeSignal:
    side: str
    reason: str
    contracts: int


@dataclass
class PendingBookSignal:
    """A signal that was generated but couldn't fill due to insufficient book depth.
    Re-evaluated on every orderbook update for the matching coin/tf/side until either:
      - book recovers and we fire (book_depth_recovered_fire)
      - elapsed >= CROSSING_HEDGE_CUTOFF_SECONDS (book_depth_expired)
      - opposite-direction crossing arrives (book_depth_overridden)
      - strategy state becomes invalid e.g. hedged=True (book_depth_drop_invalid_state)

    Note: at fire time we re-call _create_trade_signal which re-computes contracts
    based on CURRENT state. So if state evolved (other crossings fired), the entry
    number / sizing adapts. The original `intended_contracts` field below is the
    snapshot at arm time, used only for the depth recheck threshold.
    """
    direction: str               # "up" | "down"
    side: str                    # "up" | "down" — same as direction
    intended_contracts: int      # snapshot at arm time (used for depth threshold)
    gtc_price: float
    armed_at_elapsed_s: int
    armed_at_ts: float
    crossing_info: Dict          # original crossing_info for downstream order/log fields


class CrossingStrategy5MFront:
    """
    5분봉 Crossing 전략 (Max5 + 속도필터)
    - 진입 구간: 0~250초 (첫 진입 x, 이후 2x)
    - 헤지 구간: 250~290초 (x로 헤지)
    - 금지 구간: 290~300초 (진입 안 함)
    - Max5: 4번 진입 후 5번째는 x로 헤지
    - 속도 필터: 20초 내 3회 crossing 시 x로 헤지
    - 1초 cooltime
    """

    name = "crossing_5m_improve"
    description = "5분봉 crossing (Max5 + 속도필터 + 헤지)"

    def __init__(self):
        self.candle_state: Dict[str, Dict[str, Any]] = {}

    def _get_candle_state(self, candle_key: str) -> Dict[str, Any]:
        if candle_key not in self.candle_state:
            self.candle_state[candle_key] = {
                "count": 0,
                "last_trade_direction": None,
                "failed": False,
                "hedged": False,  # 헤지 완료 여부
                "crossing_times": [],  # [(elapsed_seconds, direction), ...] 기록
                # cooltime 관련
                "in_cooltime": False,
                "cooltime_start": 0.0,
                "pending_direction": None,
                "pending_elapsed": None,  # pending 발생 시점의 elapsed
                "pending_crossing_info": None,  # pending 발생 시 원본 crossing_info
                # 책 깊이 부족으로 미체결된 시그널 (책 회복 tick에서 재시도)
                "pending_book_signal": None,  # type: Optional[PendingBookSignal]
            }
        return self.candle_state[candle_key]

    def _count_recent_crossings(self, crossing_times: list, current_elapsed: int) -> int:
        """최근 SPEED_FILTER_WINDOW_SECONDS 내 crossing 횟수"""
        cutoff = current_elapsed - SPEED_FILTER_WINDOW_SECONDS
        return sum(1 for t, d in crossing_times if t >= cutoff)

    def mark_candle_failed(self, candle_key: str):
        """주문 실패 시 호출"""
        state = self._get_candle_state(candle_key)
        state["failed"] = True

    def process_crossing_event(
        self,
        elapsed_seconds: int,
        crossing_direction: str,
        candle_key: str,
    ) -> Optional[TradeSignal]:
        """
        Crossing 이벤트 처리 (Max5 + 속도필터 + cooltime)

        1. 290초 이후 → 무시
        2. 이미 헤지 완료 → 무시
        3. 250초~290초 → x로 헤지
        4. 4번 진입 후 5번째 → x로 헤지
        5. 20초 내 3회 crossing → x로 헤지
        6. 정상 진입 → 첫 진입 x, 이후 2x
        """
        state = self._get_candle_state(candle_key)
        now = time.time()

        # 실패했으면 skip
        if state["failed"]:
            return None

        # 1. 290초 이후 → 무시 (금지 구간)
        if elapsed_seconds >= CROSSING_HEDGE_CUTOFF_SECONDS:
            return None

        # 2. 이미 헤지 완료 → 무시
        if state["hedged"]:
            return None

        # 최소 시간 이전 무시
        if elapsed_seconds < CROSSING_MIN_ELAPSED_SECONDS:
            return None

        # crossing_times 기록은 _handle_crossing_event에서 처리 (중복 방지)

        # === cooltime 로직 ===
        if state["in_cooltime"]:
            if now - state["cooltime_start"] >= COOLTIME_SECONDS:
                # cooltime 종료
                state["in_cooltime"] = False
                pending = state["pending_direction"]
                pending_elapsed = state["pending_elapsed"]
                state["pending_direction"] = None
                state["pending_elapsed"] = None

                # pending이 반대 방향이면 실행
                if pending and pending != state["last_trade_direction"]:
                    return self._create_trade_signal(pending, pending_elapsed or elapsed_seconds, candle_key)

                # 현재 crossing으로 판단
                if crossing_direction != state["last_trade_direction"]:
                    return self._create_trade_signal(crossing_direction, elapsed_seconds, candle_key)
                return None
            else:
                # cooltime 중 → pending 저장 (crossing_info는 외부에서 set_pending_crossing_info로 저장)
                state["pending_direction"] = crossing_direction
                state["pending_elapsed"] = elapsed_seconds
                return None

        # === cooltime 아님 → 진입 판단 ===
        if state["last_trade_direction"] is None or crossing_direction != state["last_trade_direction"]:
            return self._create_trade_signal(crossing_direction, elapsed_seconds, candle_key)

        return None

    def _create_trade_signal(
        self,
        direction: str,
        elapsed_seconds: int,
        candle_key: str,
    ) -> TradeSignal:
        """TradeSignal 생성 + 상태 업데이트.

        주의: 이 메서드는 state["count"]++ 와 (헤지면) state["hedged"]=True 를
        수행합니다. 호출 직후 책 깊이 가드에 막혀 발사를 못 하게 되면
        rollback_last_signal() 로 되돌려야 합니다.
        """
        state = self._get_candle_state(candle_key)

        side = "up" if direction == "up" else "down"

        # === 헤지 조건 체크 ===
        is_hedge = False
        hedge_reason = ""

        # 3. ENTRY_CUTOFF~HEDGE_CUTOFF 구간 → 헤지 구간
        if elapsed_seconds >= CROSSING_ENTRY_CUTOFF_SECONDS:
            is_hedge = True
            hedge_reason = "HEDGE_TIME"

        # 4. count >= MAX_COUNT - 1 → Max 헤지 (마지막 진입을 헤지로 종결)
        elif state["count"] >= CROSSING_MAX_COUNT - 1:
            is_hedge = True
            hedge_reason = "HEDGE_MAX"

        # 5. 20초 내 SPEED_FILTER_MAX_CROSSINGS 이상 → 속도 필터 헤지
        elif self._count_recent_crossings(state["crossing_times"], elapsed_seconds) >= SPEED_FILTER_MAX_CROSSINGS:
            is_hedge = True
            hedge_reason = "HEDGE_SPEED"

        # 횟수 증가
        state["count"] += 1

        # 수량 결정
        if is_hedge:
            contracts = CROSSING_BET_CONTRACT_UNIT  # 헤지: x
            state["hedged"] = True  # 이후 진입 방지
            reason = f"{hedge_reason}_{direction.upper()} #{state['count']} @{elapsed_seconds}s"
        elif state["count"] == 1:
            contracts = CROSSING_BET_CONTRACT_UNIT  # 첫 진입: x
            reason = f"CROSS5M_{direction.upper()} #{state['count']} @{elapsed_seconds}s"
        else:
            contracts = CROSSING_BET_CONTRACT_UNIT * 2  # 이후: 2x
            reason = f"CROSS5M_{direction.upper()} #{state['count']} @{elapsed_seconds}s"

        return TradeSignal(
            side=side,
            reason=reason,
            contracts=contracts,
        )

    def rollback_last_signal(self, candle_key: str, was_hedge: bool) -> None:
        """_create_trade_signal 의 mutation 을 되돌림.

        책 깊이 가드에 막혀 시그널을 발사하지 못한 경우 호출. 다음 동일 시그널이
        같은 entry_number 로 다시 평가될 수 있도록 count 와 hedged 를 복원.
        """
        state = self._get_candle_state(candle_key)
        if state["count"] > 0:
            state["count"] -= 1
        if was_hedge:
            state["hedged"] = False

    def fire_pending_direct(
        self,
        direction: str,
        elapsed_seconds: int,
        candle_key: str,
    ) -> Optional[TradeSignal]:
        """책 회복 시 pending 시그널을 직접 재평가하여 발사 여부 결정.

        process_crossing_event 의 cooltime / last_trade_direction 분기를 우회하지만
        다음 안전장치는 강제:
          - state["failed"] / state["hedged"] / count >= MAX_COUNT 면 None
          - elapsed >= CROSSING_HEDGE_CUTOFF_SECONDS 면 None (시간 만료)
        통과하면 _create_trade_signal 로 일반 시그널 생성 (count++, hedged 갱신).
        """
        state = self._get_candle_state(candle_key)

        if state["failed"] or state["hedged"]:
            return None
        if state["count"] >= CROSSING_MAX_COUNT:
            return None
        if elapsed_seconds >= CROSSING_HEDGE_CUTOFF_SECONDS:
            return None

        return self._create_trade_signal(direction, elapsed_seconds, candle_key)

    def start_cooltime(self, candle_key: str, direction: str):
        """거래 후 cooltime 시작"""
        state = self._get_candle_state(candle_key)
        state["in_cooltime"] = True
        state["cooltime_start"] = time.time()
        state["last_trade_direction"] = direction
        state["pending_direction"] = None
        state["pending_elapsed"] = None
        state["pending_crossing_info"] = None

    def set_pending_crossing_info(self, candle_key: str, crossing_info: Dict):
        """Cooltime 중 발생한 crossing의 원본 info 저장"""
        state = self._get_candle_state(candle_key)
        if state["in_cooltime"] and state["pending_direction"]:
            state["pending_crossing_info"] = crossing_info

    def check_pending_after_cooltime(self, candle_key: str, elapsed_seconds: int) -> tuple:
        """
        Cooltime 종료 후 pending 체크 (타이머에서 호출)
        Returns: (TradeSignal or None, pending_crossing_info or None)
        """
        state = self._get_candle_state(candle_key)

        # cooltime 종료 처리
        state["in_cooltime"] = False
        pending = state["pending_direction"]
        pending_elapsed = state["pending_elapsed"]
        pending_crossing_info = state["pending_crossing_info"]
        state["pending_direction"] = None
        state["pending_elapsed"] = None
        state["pending_crossing_info"] = None

        # 실패했거나 헤지 완료면 skip
        if state["failed"] or state["hedged"]:
            return None, None

        # 290초 이후면 skip (금지 구간)
        if elapsed_seconds >= CROSSING_HEDGE_CUTOFF_SECONDS:
            return None, None

        # pending이 반대 방향이면 실행
        if pending and pending != state["last_trade_direction"]:
            signal = self._create_trade_signal(pending, pending_elapsed or elapsed_seconds, candle_key)
            return signal, pending_crossing_info

        return None, None

    def get_status(self) -> Dict[str, Any]:
        active = {k: v["count"] for k, v in self.candle_state.items() if v["count"] > 0}
        return {"active_candles": active}


# =============================================================================
# Real Trader Service
# =============================================================================
class RealTrader5MCrossFrontService(AsyncServiceBase):
    """Real Trader: 5분봉 초반 Crossing Strategy"""

    def __init__(self):
        super().__init__(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, REDIS_HOST, REDIS_PORT)

        self.ob_cache: Dict[str, Dict] = {}

        self.strategies: Dict[str, CrossingStrategy5MFront] = {}
        for coin in COINS:
            self.strategies[coin] = CrossingStrategy5MFront()

        self.stats = defaultdict(int)
        self.start_time = time.time()

        self.last_ob_message_time: float = 0.0
        self.p1_disconnect_warned: bool = False

        # Telegram queue (non-blocking)
        self.telegram_queue: asyncio.Queue = asyncio.Queue()

        # CLOB client
        self.clob_client = None
        self._clob_initialized = False
        self.token_info_cache: Dict[str, Dict] = {}

        # Low volatility filter: 최근 2캔들 crossing 횟수 (coin별)
        self.recent_crossing_counts: Dict[str, deque] = {
            coin: deque(maxlen=2) for coin in COINS
        }

        # 책 깊이 가드 구조화 로그 파일 핸들
        # asyncio 단일 루프 환경이라 별도 lock 불필요 (append-line 한 줄 단위)
        self._signals_log_path: Optional[Path] = None
        if SIGNALS_LOG_PATH:
            try:
                p = Path(SIGNALS_LOG_PATH)
                p.parent.mkdir(parents=True, exist_ok=True)
                self._signals_log_path = p
            except Exception as e:
                logger.warning(f"[BOOK_GUARD] Could not prepare SIGNALS_LOG_PATH={SIGNALS_LOG_PATH}: {e}")
                self._signals_log_path = None

    # =========================================================================
    # 책 깊이 가드 헬퍼 (Polymarket FAK/GTC 매칭 보호)
    # =========================================================================
    @staticmethod
    def _check_book_depth(
        orderbook: Optional[Dict],
        gtc_price: float,
        intended_contracts: int,
    ) -> Dict[str, Any]:
        """진입 직전 top-of-book 충분성 검사.

        통과 조건 (둘 다):
          1. best_ask 가 존재 AND best_ask <= gtc_price (즉시 매칭 가능 가격대)
          2. best_ask_size * BOOK_DEPTH_SAFETY >= intended_contracts (수량 흡수 가능)

        반환 dict:
          passed (bool), reason (str), best_ask, best_ask_size, available, required
        """
        if not orderbook:
            return {
                "passed": False, "reason": "no_orderbook",
                "best_ask": None, "best_ask_size": 0,
                "available": 0, "required": float(intended_contracts),
            }
        best_ask = orderbook.get("best_ask")
        best_ask_size = float(orderbook.get("best_ask_size") or 0)
        if best_ask is None:
            return {
                "passed": False, "reason": "no_best_ask",
                "best_ask": None, "best_ask_size": best_ask_size,
                "available": 0, "required": float(intended_contracts),
            }
        try:
            best_ask_f = float(best_ask)
        except (TypeError, ValueError):
            return {
                "passed": False, "reason": "bad_best_ask",
                "best_ask": best_ask, "best_ask_size": best_ask_size,
                "available": 0, "required": float(intended_contracts),
            }
        if best_ask_f > gtc_price:
            return {
                "passed": False, "reason": "ask_above_limit",
                "best_ask": best_ask_f, "best_ask_size": best_ask_size,
                "available": 0, "required": float(intended_contracts),
            }
        # safety 분모 0 방어 (실수 입력 막기)
        safety = BOOK_DEPTH_SAFETY if BOOK_DEPTH_SAFETY > 0 else 1.0
        required = float(intended_contracts) / safety
        passed = best_ask_size >= required
        return {
            "passed": passed,
            "reason": "depth_ok" if passed else "depth_short",
            "best_ask": best_ask_f, "best_ask_size": best_ask_size,
            "available": best_ask_size, "required": required,
        }

    def _log_signal(self, label: str, **fields) -> None:
        """JSON-Lines 구조화 시그널 기록.

        분석 스크립트가 추후 book_depth_skip / recovered_fire / expired / overridden
        / drop_invalid_state / dup_ignored 비율을 집계할 때 사용.
        파일 IO는 동기지만, append 한 줄 (~수백 바이트) 이라 latency 무시 가능.
        """
        record = {
            "ts": time.time(),
            "strategy": STRATEGY_NAME,
            "label": label,
            **fields,
        }
        # 항상 일반 로거에도 한 줄로 남김 (가시성)
        logger.info(f"[SIGNAL] {label} {json.dumps(fields, default=str)}")
        if self._signals_log_path is None:
            return
        try:
            with open(self._signals_log_path, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.warning(f"[SIGNAL] write failed: {e}")

    def _init_clob_client(self):
        """Initialize Polymarket CLOB client"""
        if not POLYMARKET_PRIVATE_KEY:
            logger.warning("POLYMARKET_PRIVATE_KEY not set, live trading disabled")
            return

        if not POLYMARKET_PROXY_ADDRESS:
            logger.warning("POLYMARKET_PROXY_ADDRESS not set, live trading disabled")
            return

        try:
            from py_clob_client_v2 import ClobClient, BuilderConfig, BalanceAllowanceParams, AssetType

            builder_config = None
            if POLYMARKET_BUILDER_CODE:
                builder_config = BuilderConfig(builder_code=POLYMARKET_BUILDER_CODE)

            self.clob_client = ClobClient(
                host=POLYMARKET_HOST,
                key=POLYMARKET_PRIVATE_KEY,
                chain_id=POLYMARKET_CHAIN_ID,
                signature_type=2,
                funder=POLYMARKET_PROXY_ADDRESS,
                builder_config=builder_config,
            )

            api_creds = self.clob_client.create_or_derive_api_key()
            self.clob_client.set_api_creds(api_creds)

            # V2: 서버측 balance/allowance 인덱서 강제 refresh.
            # 누락 시 onchain approve가 끝나도 첫 주문이 stale cache로 reject됨.
            try:
                self.clob_client.update_balance_allowance(
                    BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                )
            except Exception as e:
                logger.warning(f"update_balance_allowance failed (non-fatal): {e}")

            self._clob_initialized = True
            logger.info(
                f"CLOB v2 client initialized (host={POLYMARKET_HOST}, chain={POLYMARKET_CHAIN_ID}, "
                f"builder_code={'set' if POLYMARKET_BUILDER_CODE else 'none'})"
            )

        except ImportError:
            logger.error("py-clob-client-v2 not installed, live trading disabled")
        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")

    @property
    def is_live_enabled(self) -> bool:
        return self._clob_initialized and self.clob_client is not None

    def is_healthy(self) -> bool:
        return (self.db_pool is not None and self.redis_client is not None)

    # =========================================================================
    # Telegram
    # =========================================================================
    async def telegram_sender(self):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.info("Telegram not configured, sender disabled")
            return

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    msg = await self.telegram_queue.get()
                    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                    payload = {
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": msg,
                        "parse_mode": "HTML"
                    }
                    if TELEGRAM_THREAD_ID:
                        payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)
                    await session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Telegram send error: {e}")

    def notify(self, message: str):
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            try:
                self.telegram_queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("Telegram queue full, dropping message")

    # =========================================================================
    # Redis Pub/Sub Subscriber
    # =========================================================================
    async def redis_subscriber(self):
        while True:
            try:
                pubsub = self.redis_client.pubsub()
                # 5m candle_boundary만 구독
                boundary_channels = [f"ch:candle_boundary:{tf}" for tf in TIMEFRAMES]
                await pubsub.psubscribe("ch:crossing:*", "ch:orderbook:*", *boundary_channels)
                logger.info(f"[PUBSUB] Subscribed to ch:crossing:*, ch:orderbook:*, ch:candle_boundary:{TIMEFRAMES}")

                async for message in pubsub.listen():
                    if message["type"] not in ("message", "pmessage"):
                        continue

                    try:
                        data = json.loads(message["data"])
                        channel = message.get("channel", "")

                        if "ch:candle_boundary" in channel:
                            await self._handle_candle_boundary(data)
                        elif "ch:crossing:" in channel:
                            await self._handle_crossing_event(data)
                        elif "ch:orderbook:" in channel:
                            await self._handle_orderbook_message(data)

                    except json.JSONDecodeError:
                        pass
                    except Exception as e:
                        logger.error(f"[PUBSUB] message error: {e}")
                        self.stats["pubsub_errors"] += 1

            except Exception as e:
                logger.error(f"[PUBSUB] subscriber error: {e}")
                await asyncio.sleep(3)

    async def _handle_orderbook_message(self, data: Dict):
        coin = data.get("coin")
        tf = data.get("timeframe")
        side = data.get("side")

        if not coin or not tf or not side:
            return

        if coin not in COINS:
            return

        # 5분봉만 처리
        if tf not in TIMEFRAMES:
            return

        cache_key = f"{coin}_{tf}_{side}"

        token_id = data["token_id"]
        self.ob_cache[cache_key] = {
            "best_bid": data["best_bid"],
            "best_ask": data["best_ask"],
            "best_bid_size": data.get("best_bid_size", 0),
            "best_ask_size": data.get("best_ask_size", 0),
            "mid_price": data["mid_price"],
            "market_slug": data["slug"],
            "token_id": token_id,
            "candle_start_ts": data.get("candle_start_ts"),
            "candle_end_ts": data.get("candle_end_ts"),
            "timestamp": data["timestamp"],
        }

        if token_id and token_id not in self.token_info_cache:
            asyncio.create_task(self._prefetch_token_info(token_id))

        self.stats["ob_messages"] += 1
        self.last_ob_message_time = time.time()
        if self.p1_disconnect_warned:
            logger.info("[P1] Connection restored")
            self.p1_disconnect_warned = False

        # === 책 깊이 pending 회복 시도 ===
        # 이 coin/tf/side 에 미처리 pending 이 있고 책이 충분해졌으면 발사.
        if BOOK_DEPTH_REQUIRED and BOOK_DEPTH_PENDING_ENABLED:
            await self._try_book_recovery(coin, tf, side, token_id)

    async def _prefetch_token_info(self, token_id: str):
        if not self.is_live_enabled:
            return
        if token_id in self.token_info_cache:
            return
        try:
            tick_size = self.clob_client.get_tick_size(token_id)
            neg_risk = self.clob_client.get_neg_risk(token_id)
            self.token_info_cache[token_id] = {
                "tick_size": tick_size,
                "neg_risk": neg_risk,
            }
            logger.info(f"[PREFETCH] Cached: {token_id[:16]}... tick={tick_size} neg_risk={neg_risk}")
        except Exception as e:
            logger.warning(f"[PREFETCH] Failed to cache token info: {e}")

    async def _try_book_recovery(self, coin: str, timeframe: str, side: str, token_id: str) -> None:
        """책 업데이트 tick 마다 호출 — 매칭되는 pending 이 있으면 재평가 후 발사.

        호출 빈도가 높으므로 빠른 경로 (no pending → 즉시 return) 가 핵심.
        """
        strategy = self.strategies.get(coin)
        if strategy is None:
            return

        # 5분봉 BTC 단일 코인이라 활성 candle_state 는 0~1개.
        # 그래도 일반화: 해당 coin/tf 에 매칭되는 candle_key 한 개를 찾는다.
        target_key = None
        target_pending: Optional[PendingBookSignal] = None
        prefix = f"{coin}_{timeframe}_"
        for ck, st in strategy.candle_state.items():
            if not ck.startswith(prefix):
                continue
            pending = st.get("pending_book_signal")
            if pending is None or pending.side != side:
                continue
            target_key = ck
            target_pending = pending
            break

        if target_pending is None:
            return

        state = strategy._get_candle_state(target_key)

        # candle_start 파싱 ("btc_5m_2026-04-21T00:05:00+00:00")
        try:
            candle_start_str = target_key.split("_", 2)[2]
            candle_start = datetime.fromisoformat(candle_start_str)
        except Exception as e:
            logger.error(f"[BOOK_RECOVERY] cannot parse candle_key={target_key}: {e}")
            state["pending_book_signal"] = None
            return

        candle_end = candle_start + timedelta(minutes=5)
        now = datetime.now(timezone.utc)
        if now >= candle_end:
            # 캔들이 이미 끝났으면 더 이상 발사 불가
            state["pending_book_signal"] = None
            return

        elapsed_now = int((now - candle_start).total_seconds())

        # 안전장치 1: 시간 만료
        if elapsed_now >= CROSSING_HEDGE_CUTOFF_SECONDS:
            self._log_signal(
                "book_depth_expired",
                coin=coin, side=side,
                elapsed_seconds=elapsed_now,
                armed_at_elapsed=target_pending.armed_at_elapsed_s,
                wait_seconds=elapsed_now - target_pending.armed_at_elapsed_s,
            )
            state["pending_book_signal"] = None
            return

        # 안전장치 2: 전략 상태 무효 (failed / hedged / max 도달)
        if state["failed"] or state["hedged"] or state["count"] >= CROSSING_MAX_COUNT:
            self._log_signal(
                "book_depth_drop_invalid_state",
                coin=coin, side=side,
                elapsed_seconds=elapsed_now,
                failed=state["failed"], hedged=state["hedged"], count=state["count"],
            )
            state["pending_book_signal"] = None
            return

        # 책 재체크
        orderbook = self.get_orderbook(coin, timeframe, side)
        chk = self._check_book_depth(orderbook, target_pending.gtc_price, target_pending.intended_contracts)
        if not chk["passed"]:
            return  # 계속 대기

        # 통과 → 직접 발사 (last_trade_direction parity 우회, MAX/HEDGE 등 재평가)
        signal = strategy.fire_pending_direct(
            direction=target_pending.direction,
            elapsed_seconds=elapsed_now,
            candle_key=target_key,
        )
        if signal is None:
            self._log_signal(
                "book_depth_drop_invalid_state",
                coin=coin, side=side,
                elapsed_seconds=elapsed_now,
                reason="fire_pending_direct_returned_none",
            )
            state["pending_book_signal"] = None
            return

        wait_s = elapsed_now - target_pending.armed_at_elapsed_s
        self._log_signal(
            "book_depth_recovered_fire",
            coin=coin, side=side,
            elapsed_seconds=elapsed_now,
            wait_seconds=wait_s,
            best_ask=chk["best_ask"],
            best_ask_size=chk["best_ask_size"],
            intended_contracts=target_pending.intended_contracts,
            actual_contracts=signal.contracts,
            signal_reason=signal.reason,
        )
        state["pending_book_signal"] = None
        self.stats["book_depth_recoveries"] += 1

        # crossing_info 에 회복 메타 추가
        crossing_info = dict(target_pending.crossing_info)
        crossing_info["from_book_recovery"] = True
        crossing_info["book_wait_seconds"] = wait_s
        crossing_info["recovered_elapsed_s"] = elapsed_now

        asyncio.create_task(self.execute_order(
            coin, timeframe, signal, token_id,
            candle_start, candle_end,
            crossing_info=crossing_info,
            candle_key=target_key,
        ))

    async def _handle_candle_boundary(self, data: Dict):
        """캔들 경계 → 전략 리셋"""
        tf = data.get("timeframe", "?")
        # 5분봉만 처리
        if tf not in TIMEFRAMES:
            return

        # 리셋 전에 crossing stats 저장
        await self._save_candle_crossing_stats(tf)

        logger.info(f"[CANDLE_BOUNDARY] Resetting strategies (tf={tf})")
        self.ob_cache.clear()
        self.token_info_cache.clear()
        for strategy in self.strategies.values():
            strategy.candle_state.clear()

    async def _handle_crossing_event(self, data: Dict):
        coin = data.get("coin")
        tf = data.get("timeframe")
        direction = data.get("direction")
        candle_start_str = data.get("candle_start")
        candle_end_str = data.get("candle_end")

        if not all([coin, tf, direction, candle_start_str, candle_end_str]):
            logger.warning(f"[CROSSING] Invalid data: {data}")
            return

        if coin not in COINS:
            return

        # 5분봉만 처리
        if tf not in TIMEFRAMES:
            return

        if coin not in self.strategies:
            return

        self.stats["crossing_events"] += 1

        try:
            candle_start = datetime.fromisoformat(candle_start_str.replace('Z', '+00:00'))
            candle_end = datetime.fromisoformat(candle_end_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            # binance_ws에서 보낸 elapsed_ms 사용 (없으면 직접 계산)
            elapsed_ms = data.get("elapsed_ms") or int((now - candle_start).total_seconds() * 1000)
            elapsed_seconds = elapsed_ms // 1000
        except Exception as e:
            logger.error(f"[CROSSING] Time parse error: {e}")
            return

        if now >= candle_end:
            return

        side = "up" if direction == "up" else "down"
        orderbook = self.get_orderbook(coin, tf, side)

        if not orderbook:
            logger.warning(f"[{coin}] CROSSING but no {side.upper()} orderbook cache")
            return

        expected_slug = generate_slug(coin, tf)
        ob_slug = orderbook.get('market_slug', '')
        if ob_slug != expected_slug:
            logger.warning(f"[{coin}] SLUG_MISMATCH expected={expected_slug} got={ob_slug}")
            return

        token_id = orderbook.get('token_id', '')
        if not token_id:
            logger.error(f"[{coin}] No token_id for {side.upper()}")
            return

        candle_key = f"{coin}_{tf}_{candle_start.isoformat()}"

        strategy = self.strategies[coin]

        # === 책 깊이 pending override ===
        # 같은 캔들에 미처리 pending 이 있으면:
        #   - 같은 방향 새 크로싱 → 무시 (pending 유지, 중복 발사 방지)
        #   - 반대 방향 새 크로싱 → pending 폐기 후 새 시그널 정상 처리
        state = strategy._get_candle_state(candle_key)
        existing_pending = state.get("pending_book_signal")
        if existing_pending is not None:
            if existing_pending.direction == direction:
                self._log_signal(
                    "book_pending_dup_ignored",
                    coin=coin, side=direction,
                    elapsed_seconds=elapsed_seconds,
                    pending_armed_at=existing_pending.armed_at_elapsed_s,
                )
                # crossing 기록은 통계용으로 추가하고 함수 종료
                state["crossing_times"].append((elapsed_seconds, direction))
                return
            else:
                self._log_signal(
                    "book_depth_overridden",
                    coin=coin, old_side=existing_pending.direction, new_side=direction,
                    elapsed_seconds=elapsed_seconds,
                    pending_armed_at=existing_pending.armed_at_elapsed_s,
                )
                state["pending_book_signal"] = None
                # 그대로 아래 정상 처리로 진행

        # crossing 기록 (통계용 - 필터와 무관하게 항상 기록)
        state["crossing_times"].append((elapsed_seconds, direction))

        # High volatility filter: 이전 2캔들 중 하나라도 crossing > 3이면 스킵
        if self._is_high_volatility(coin):
            logger.info(f"[{coin}] HIGH_VOL_SKIP: prev={list(self.recent_crossing_counts[coin])} @{elapsed_seconds}s")
            return

        signal = strategy.process_crossing_event(
            elapsed_seconds=elapsed_seconds,
            crossing_direction=direction,
            candle_key=candle_key,
        )

        # crossing_info 생성 (signal 여부와 무관하게)
        crossing_info = {
            "direction": direction,
            "candle_open": data.get("candle_open"),
            "prev_price": data.get("prev_price"),
            "current_price": data.get("current_price"),
            "elapsed_seconds": elapsed_seconds,
            "elapsed_ms": elapsed_ms,
            "prev_elapsed_ms": data.get("prev_elapsed_ms"),
            "curr_elapsed_ms": data.get("curr_elapsed_ms"),
        }

        if signal:
            # === 책 깊이 가드 ===
            if BOOK_DEPTH_REQUIRED:
                chk = self._check_book_depth(orderbook, GTC_FIXED_PRICE, signal.contracts)
                if not chk["passed"]:
                    was_hedge = "HEDGE" in signal.reason
                    strategy.rollback_last_signal(candle_key, was_hedge)
                    self._log_signal(
                        "book_depth_skip",
                        coin=coin, side=direction,
                        elapsed_seconds=elapsed_seconds,
                        intended_contracts=signal.contracts,
                        gtc_price=GTC_FIXED_PRICE,
                        reason=chk["reason"],
                        best_ask=chk["best_ask"],
                        best_ask_size=chk["best_ask_size"],
                        available=chk["available"],
                        required=chk["required"],
                        was_hedge=was_hedge,
                        signal_reason=signal.reason,
                    )
                    if BOOK_DEPTH_PENDING_ENABLED:
                        state["pending_book_signal"] = PendingBookSignal(
                            direction=direction,
                            side=direction,
                            intended_contracts=signal.contracts,
                            gtc_price=GTC_FIXED_PRICE,
                            armed_at_elapsed_s=elapsed_seconds,
                            armed_at_ts=time.time(),
                            crossing_info=crossing_info,
                        )
                    self.stats["book_depth_skips"] += 1
                    return
                # 통과 → 일반 발사 경로
                self.stats["book_depth_passes"] += 1
            asyncio.create_task(self.execute_order(
                coin, tf, signal, token_id,
                candle_start, candle_end,
                crossing_info=crossing_info,
                candle_key=candle_key,
            ))
        else:
            # signal이 없으면 cooltime 중 pending으로 저장된 것일 수 있음 → crossing_info 저장
            strategy.set_pending_crossing_info(candle_key, crossing_info)

    def get_orderbook(self, coin: str, timeframe: str, side: str = "up") -> Optional[Dict]:
        key = f"{coin}_{timeframe}_{side}"
        cached = self.ob_cache.get(key)
        if not cached:
            return None
        if time.time() - cached.get("timestamp", 0) > 60:
            return None
        return cached

    # =========================================================================
    # Real Order Execution
    # =========================================================================
    async def execute_order(
        self,
        coin: str,
        timeframe: str,
        signal: TradeSignal,
        token_id: str,
        candle_start: datetime,
        candle_end: datetime,
        crossing_info: Optional[Dict] = None,
        candle_key: Optional[str] = None,
    ):
        side = signal.side.upper()
        contracts = signal.contracts
        reason = signal.reason

        elapsed_seconds = crossing_info.get('elapsed_seconds', 60) if crossing_info else 60
        gtc_price = GTC_FIXED_PRICE  # 고정 0.80

        # 주문 시작 전에 cooltime 걸기 (동시 주문 방지)
        if candle_key:
            strategy = self.strategies.get(coin)
            if strategy:
                strategy.start_cooltime(candle_key, signal.side)
                logger.info(f"[{coin}] COOLTIME started (pre-order): {signal.side} for {COOLTIME_SECONDS}s")

        await asyncio.sleep(0.005)
        orderbook = self.get_orderbook(coin, timeframe, signal.side)
        best_ask_pre = orderbook.get('best_ask', 0) if orderbook else 0
        best_bid_pre = orderbook.get('best_bid', 0) if orderbook else 0
        logger.info(f"[{coin}] PRE-ORDER: {side} x{contracts} | best_ask={best_ask_pre:.3f} GTC={gtc_price:.2f} @{elapsed_seconds}s")

        order_result = None

        if not self.is_live_enabled:
            logger.warning(f"[{coin}] CLOB not enabled, skipping real order")
            order_result = {
                "success": False,
                "error": "CLOB not enabled",
                "target_contracts": contracts,
                "filled_contracts": 0,
                "filled_cost": 0.0,
                "fill_price": 0.0,
                "total_latency_ms": 0,
            }
        else:
            order_result = await self._place_gtc_order(
                coin=coin,
                token_id=token_id,
                target_contracts=contracts,
                gtc_price=gtc_price,
            )

        latency_ms = order_result.get("total_latency_ms", 0)
        filled = order_result.get("filled_contracts", 0)
        fill_price = order_result.get("fill_price", 0.0)

        await asyncio.sleep(0.005)
        orderbook_post = self.get_orderbook(coin, timeframe, signal.side)
        best_ask_post = orderbook_post.get('best_ask', 0) if orderbook_post else 0
        best_bid_post = orderbook_post.get('best_bid', 0) if orderbook_post else 0
        logger.info(f"[{coin}] POST-ORDER: best_ask {best_ask_pre:.3f}->{best_ask_post:.3f} | best_bid {best_bid_pre:.3f}->{best_bid_post:.3f}")

        await self._record_trade(
            coin, timeframe, signal, order_result,
            candle_start, candle_end,
            crossing_info, latency_ms
        )

        cross_dir = crossing_info.get('direction', '?').upper() if crossing_info else '?'
        candle_open = (crossing_info.get('candle_open') or 0) if crossing_info else 0
        prev_price = (crossing_info.get('prev_price') or 0) if crossing_info else 0
        curr_price = (crossing_info.get('current_price') or 0) if crossing_info else 0
        prev_elapsed_ms = crossing_info.get('prev_elapsed_ms') if crossing_info else None
        curr_elapsed_ms = crossing_info.get('curr_elapsed_ms') if crossing_info else None

        # ms를 m:ss.mmm 포맷으로 변환
        def fmt_ms(ms):
            if ms is None:
                return "?"
            s = ms // 1000
            return f"{s//60}:{s%60:02d}.{ms%1000:03d}"

        prev_time_str = fmt_ms(prev_elapsed_ms)
        curr_time_str = fmt_ms(curr_elapsed_ms)

        target = order_result.get("target_contracts", contracts)

        # reason에서 entry number 추출
        entry_num = None
        if "#" in reason:
            try:
                entry_num = int(reason.split("#")[1].split()[0])
            except:
                pass

        if order_result.get("success"):
            self.stats["orders_filled"] += 1

            logger.info(
                f"[{coin}] GTC ORDER: {side} x{contracts} | "
                f"filled={filled} @{fill_price:.3f} | latency={latency_ms:.0f}ms"
            )

            # cooltime 타이머 시작 (cooltime은 pre-order에서 이미 시작됨)
            if candle_key:
                asyncio.create_task(self._cooltime_timer(
                    coin, timeframe, candle_key, candle_start, candle_end
                ))

            status_mark = "OK" if filled >= target else "PARTIAL"
            entry_label = f"#{entry_num}" if entry_num else ""

            self.notify(
                f"<b>[5M]</b> {entry_label} {cross_dir} | open {candle_open:,.2f}\n"
                f"{prev_time_str} -> {curr_time_str}\n"
                f"{prev_price:,.2f} -> {curr_price:,.2f}\n"
                f"x{contracts} @{fill_price:.2f} {filled:.0f}/{target} {status_mark} | {latency_ms:.0f}ms"
            )
        else:
            self.stats["orders_failed"] += 1
            error = order_result.get("error", "Unknown")

            logger.error(f"[{coin}] GTC ORDER FAILED: {side} x{contracts} | error={error}")

            # 실패해도 다음 crossing에 계속 응답 (mark_candle_failed 호출 안 함)

            entry_label = f"#{entry_num}" if entry_num else ""
            self.notify(
                f"<b>[5M]</b> {entry_label} {cross_dir} | open {candle_open:,.2f}\n"
                f"{prev_time_str} -> {curr_time_str}\n"
                f"{prev_price:,.2f} -> {curr_price:,.2f}\n"
                f"x{contracts} FAILED: {error} | {latency_ms:.0f}ms"
            )

    async def _cooltime_timer(
        self,
        coin: str,
        timeframe: str,
        candle_key: str,
        candle_start: datetime,
        candle_end: datetime,
    ):
        """Cooltime 종료 후 pending 체크 및 실행"""
        await asyncio.sleep(COOLTIME_SECONDS)

        # 캔들 종료 체크
        now = datetime.now(timezone.utc)
        if now >= candle_end:
            logger.debug(f"[{coin}] COOLTIME_TIMER: candle ended, skip pending")
            return

        strategy = self.strategies.get(coin)
        if not strategy:
            return

        elapsed_seconds = int((now - candle_start).total_seconds())

        signal, pending_crossing_info = strategy.check_pending_after_cooltime(candle_key, elapsed_seconds)
        if not signal:
            logger.debug(f"[{coin}] COOLTIME_TIMER: no pending to execute")
            return

        logger.info(f"[{coin}] COOLTIME_TIMER: executing pending {signal.side.upper()} x{signal.contracts}")

        # orderbook에서 token_id 가져오기
        orderbook = self.get_orderbook(coin, timeframe, signal.side)
        if not orderbook:
            logger.warning(f"[{coin}] COOLTIME_TIMER: no orderbook for {signal.side}")
            return

        token_id = orderbook.get('token_id', '')
        if not token_id:
            logger.error(f"[{coin}] COOLTIME_TIMER: no token_id")
            return

        # pending 시 저장된 crossing_info 사용, 없으면 기본값
        if pending_crossing_info:
            crossing_info = pending_crossing_info.copy()
            crossing_info["from_cooltime_timer"] = True
        else:
            crossing_info = {
                "direction": signal.side,
                "candle_open": None,
                "prev_price": None,
                "current_price": None,
                "elapsed_seconds": elapsed_seconds,
                "elapsed_ms": elapsed_seconds * 1000,
                "prev_elapsed_ms": None,
                "curr_elapsed_ms": elapsed_seconds * 1000,
                "from_cooltime_timer": True,
            }

        # === 책 깊이 가드 (cooltime 경로도 동일) ===
        # check_pending_after_cooltime 이 _create_trade_signal 을 호출했으므로 state 는 이미 mutate 됨.
        # 책 부족이면 rollback 후 pending_book_signal 으로 보관 (책 회복 tick 에서 재발사).
        if BOOK_DEPTH_REQUIRED:
            chk = self._check_book_depth(orderbook, GTC_FIXED_PRICE, signal.contracts)
            if not chk["passed"]:
                was_hedge = "HEDGE" in signal.reason
                strategy.rollback_last_signal(candle_key, was_hedge)
                self._log_signal(
                    "book_depth_skip",
                    coin=coin, side=signal.side,
                    elapsed_seconds=elapsed_seconds,
                    intended_contracts=signal.contracts,
                    gtc_price=GTC_FIXED_PRICE,
                    reason=chk["reason"],
                    best_ask=chk["best_ask"],
                    best_ask_size=chk["best_ask_size"],
                    available=chk["available"],
                    required=chk["required"],
                    was_hedge=was_hedge,
                    signal_reason=signal.reason,
                    source="cooltime_timer",
                )
                if BOOK_DEPTH_PENDING_ENABLED:
                    state = strategy._get_candle_state(candle_key)
                    state["pending_book_signal"] = PendingBookSignal(
                        direction=signal.side,
                        side=signal.side,
                        intended_contracts=signal.contracts,
                        gtc_price=GTC_FIXED_PRICE,
                        armed_at_elapsed_s=elapsed_seconds,
                        armed_at_ts=time.time(),
                        crossing_info=crossing_info,
                    )
                self.stats["book_depth_skips"] += 1
                return
            self.stats["book_depth_passes"] += 1

        await self.execute_order(
            coin, timeframe, signal, token_id,
            candle_start, candle_end,
            crossing_info=crossing_info,
            candle_key=candle_key,
        )

    async def _place_gtc_order(
        self,
        coin: str,
        token_id: str,
        target_contracts: int,
        gtc_price: float,
    ) -> Dict[str, Any]:
        # V2: book을 크로스하는 GTC는 strict reject(400). 본 전략은 book guard로
        # best_ask <= gtc_price 임을 확인 후 발사하므로 의도가 항상 taker → FAK 사용.
        # FAK = Fill-And-Kill (IOC): 가능한 만큼 체결, 잔여 즉시 취소 (maker rest 없음).
        from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions, Side

        start_time = time.time()
        order_price = gtc_price

        try:
            cached = self.token_info_cache.get(token_id)
            order_args = OrderArgs(
                token_id=token_id,
                price=order_price,
                size=float(target_contracts),
                side=Side.BUY,
            )
            if cached:
                options = PartialCreateOrderOptions(
                    tick_size=cached["tick_size"],
                    neg_risk=cached["neg_risk"],
                )
                signed_order = self.clob_client.create_order(order_args, options)
            else:
                signed_order = self.clob_client.create_order(order_args)

            response = self.clob_client.post_order(signed_order, OrderType.FAK)
            logger.info(f"[FAK] CLOB response (price={order_price}, size={target_contracts}): {response}")

            if isinstance(response, dict):
                order_id = response.get("orderID") or response.get("orderId", "")

                if response.get("success", True) and order_id:
                    filled = 0
                    cost = 0.0
                    try:
                        making_amt = response.get("makingAmount", "0")
                        taking_amt = response.get("takingAmount", "0")
                        filled = float(taking_amt) if taking_amt else 0.0
                        cost = float(making_amt) if making_amt else 0.0
                    except (ValueError, TypeError):
                        pass

                    fill_price = cost / filled if filled > 0 else order_price

                    total_latency = (time.time() - start_time) * 1000

                    return {
                        "success": True,
                        "status": "FILLED" if filled > 0 else "PENDING",
                        "target_contracts": target_contracts,
                        "filled_contracts": filled,
                        "filled_cost": cost,
                        "fill_price": fill_price,
                        "order_id": order_id,
                        "order_price": order_price,
                        "total_latency_ms": total_latency,
                        "error": None,
                    }
                else:
                    error_msg = (
                        response.get("errorMsg")
                        or response.get("error")
                        or response.get("message", str(response))
                    )
                    total_latency = (time.time() - start_time) * 1000

                    return {
                        "success": False,
                        "target_contracts": target_contracts,
                        "filled_contracts": 0,
                        "filled_cost": 0.0,
                        "fill_price": 0.0,
                        "order_price": order_price,
                        "total_latency_ms": total_latency,
                        "error": error_msg,
                    }
            else:
                total_latency = (time.time() - start_time) * 1000
                return {
                    "success": False,
                    "target_contracts": target_contracts,
                    "filled_contracts": 0,
                    "filled_cost": 0.0,
                    "fill_price": 0.0,
                    "order_price": order_price,
                    "total_latency_ms": total_latency,
                    "error": f"Unexpected response type: {type(response)}",
                }

        except Exception as e:
            error_msg = str(e)
            total_latency = (time.time() - start_time) * 1000
            logger.error(f"[FAK] Exception: {error_msg}")

            return {
                "success": False,
                "target_contracts": target_contracts,
                "filled_contracts": 0,
                "filled_cost": 0.0,
                "fill_price": 0.0,
                "order_price": order_price,
                "total_latency_ms": total_latency,
                "error": error_msg,
            }

    async def _record_trade(
        self,
        coin: str,
        timeframe: str,
        signal: TradeSignal,
        order_result: Dict[str, Any],
        candle_start: datetime,
        candle_end: datetime,
        crossing_info: Optional[Dict],
        latency_ms: float,
    ):
        try:
            side = signal.side.upper()
            order_price = order_result.get('order_price', GTC_FIXED_PRICE)
            target_contracts = order_result.get('target_contracts', signal.contracts)
            reason = signal.reason

            filled_contracts = round(order_result.get('filled_contracts', 0))
            filled_cost = order_result.get('filled_cost', 0.0)
            fill_price = order_result.get('fill_price', 0.0)

            snapshot_data = {
                'crossing': crossing_info,
                'order_result': order_result,
                'latency_ms': latency_ms,
            }

            order_id = order_result.get('order_id', '')
            if order_result.get('success'):
                status = order_result.get('status', 'FILLED')
            else:
                status = 'FAILED'

            async with self.db_pool.acquire() as conn:
                trade_id = await conn.fetchval("""
                    INSERT INTO test_paper_trades (
                        time, strategy_name, coin, timeframe,
                        mid_price, up_mid_price, down_mid_price,
                        side, order_price, reason, orderbook_snapshot,
                        market_slug, token_id, order_id, candle_start_time, candle_end_time,
                        contracts, cost, filled_contracts, filled_cost, status
                    ) VALUES (
                        NOW(), $1, $2, $3,
                        $4, $5, $6,
                        $7, $8, $9, $10,
                        $11, $12, $13, $14, $15,
                        $16, $17, $18, $19, $20
                    )
                    RETURNING id
                """,
                    STRATEGY_NAME, coin, timeframe,
                    0.5, None, None,
                    side, order_price, reason, json.dumps(snapshot_data),
                    f'{coin}-{timeframe}', '', order_id, candle_start, candle_end,
                    target_contracts, target_contracts * order_price,
                    filled_contracts, filled_cost,
                    status,
                )

                if order_result.get('success') and filled_contracts > 0:
                    await conn.execute("""
                        UPDATE test_paper_trades
                        SET fill_time = NOW(), fill_price = $1
                        WHERE id = $2
                    """, fill_price, trade_id)

                logger.info(f"[{coin}] Trade recorded: id={trade_id} status={status} target={target_contracts} filled={filled_contracts}")

            self.stats["trades_created"] += 1

        except Exception as e:
            logger.error(f"[{coin}] _record_trade error: {e}")

    async def _save_candle_crossing_stats(self, timeframe: str):
        """캔들 종료 시 crossing 통계 저장"""
        try:
            for strategy in self.strategies.values():
                for candle_key, state in strategy.candle_state.items():
                    crossing_count = len(state.get("crossing_times", []))
                    trade_count = state.get("count", 0)

                    # candle_key: "btc_5m_2026-03-20T07:05:00+00:00"
                    parts = candle_key.split("_", 2)
                    coin = parts[0] if len(parts) > 0 else "?"
                    tf = parts[1] if len(parts) > 1 else timeframe
                    candle_start_str = parts[2] if len(parts) > 2 else None

                    candle_start = None
                    candle_end = None
                    if candle_start_str:
                        try:
                            candle_start = datetime.fromisoformat(candle_start_str)
                            candle_end = candle_start + timedelta(minutes=5)
                        except:
                            pass

                    async with self.db_pool.acquire() as conn:
                        await conn.execute("""
                            INSERT INTO candle_crossing_stats (
                                strategy_name, coin, timeframe, candle_key,
                                candle_start, candle_end, crossing_count, trade_count
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                            ON CONFLICT (strategy_name, candle_key) DO UPDATE SET
                                crossing_count = EXCLUDED.crossing_count,
                                trade_count = EXCLUDED.trade_count
                        """,
                            STRATEGY_NAME, coin, tf, candle_key,
                            candle_start, candle_end, crossing_count, trade_count
                        )

                    logger.info(f"[CROSSING_STATS] {candle_key}: crossings={crossing_count} trades={trade_count}")

                    # 큐에 추가 (low volatility filter용)
                    self.recent_crossing_counts[coin].append(crossing_count)
                    logger.info(f"[LOW_VOL_FILTER] {coin}: queue={list(self.recent_crossing_counts[coin])}")

                    # 텔레그램 알림
                    candle_start_str = candle_start.strftime('%H:%M') if candle_start else "?"
                    candle_end_str = candle_end.strftime('%H:%M') if candle_end else "?"
                    self.notify(f"[5M {candle_start_str}~{candle_end_str}] Crossings: {crossing_count}")

        except Exception as e:
            logger.error(f"[CROSSING_STATS] Save error: {e}")

    async def _init_recent_crossing_counts(self):
        """서비스 시작 시 DB에서 최근 2캔들 crossing 횟수 로드"""
        try:
            async with self.db_pool.acquire() as conn:
                for coin in COINS:
                    rows = await conn.fetch("""
                        SELECT crossing_count FROM candle_crossing_stats
                        WHERE strategy_name = $1 AND coin = $2
                        ORDER BY candle_start DESC
                        LIMIT 2
                    """, STRATEGY_NAME, coin)

                    # 오래된 것부터 넣기 (deque에 순서대로)
                    for row in reversed(rows):
                        self.recent_crossing_counts[coin].append(row['crossing_count'])

                    if rows:
                        logger.info(f"[LOW_VOL_FILTER] {coin}: loaded {list(self.recent_crossing_counts[coin])}")
                    else:
                        logger.info(f"[LOW_VOL_FILTER] {coin}: no history, filter disabled until 2 candles")

        except Exception as e:
            logger.error(f"[LOW_VOL_FILTER] Init error: {e}")

    def _is_high_volatility(self, coin: str) -> bool:
        """이전 2캔들 중 하나라도 crossing > 3이면 True (스킵 대상)"""
        q = self.recent_crossing_counts.get(coin)
        if not q or len(q) < 2:
            return False  # 데이터 없으면 진입 허용
        return any(c > 3 for c in q)

    # =========================================================================
    # Settlement
    # =========================================================================
    async def settle_trades(self, coin: str, timeframe: str,
                            candle_start: datetime, candle_end: datetime) -> int:
        market_result = await self.get_price_result(coin, timeframe, candle_start, candle_end)
        if not market_result:
            return 0

        settled_count = 0
        crossing_count = 0
        try:
            async with self.db_pool.acquire() as conn:
                # 해당 캔들의 crossing 횟수 조회
                candle_key = f"{coin}_{timeframe}_{candle_start.isoformat()}"
                crossing_row = await conn.fetchrow("""
                    SELECT crossing_count FROM candle_crossing_stats
                    WHERE strategy_name = $1 AND candle_key = $2
                """, STRATEGY_NAME, candle_key)
                if crossing_row:
                    crossing_count = crossing_row['crossing_count']

                trades = await conn.fetch("""
                    SELECT id, side, fill_price, contracts, cost,
                           filled_contracts, filled_cost, status, orderbook_snapshot
                    FROM test_paper_trades
                    WHERE coin = $1 AND timeframe = $2 AND strategy_name = $3
                      AND status IN ('FILLED', 'FAILED')
                      AND candle_start_time = $4
                    ORDER BY time ASC
                """, coin, timeframe, STRATEGY_NAME, candle_start)

                if not trades:
                    return 0

                total_pnl = 0.0
                results = []

                for trade in trades:
                    trade_id = trade['id']
                    side = trade['side']
                    status = trade['status']
                    fill_price = float(trade['fill_price']) if trade['fill_price'] is not None else 0.0
                    filled_contracts = float(trade['filled_contracts']) if trade['filled_contracts'] is not None else 0.0
                    filled_cost = float(trade['filled_cost']) if trade['filled_cost'] is not None else 0.0
                    if filled_contracts == 0 and trade['contracts']:
                        filled_contracts = float(trade['contracts'])
                        filled_cost = float(trade['cost']) if trade['cost'] else 0.0

                    snapshot = {}
                    if trade['orderbook_snapshot']:
                        try:
                            snapshot = json.loads(trade['orderbook_snapshot']) if isinstance(trade['orderbook_snapshot'], str) else trade['orderbook_snapshot']
                        except:
                            pass

                    crossing = snapshot.get('crossing', {})

                    if status == 'FAILED':
                        results.append({
                            "side": side,
                            "status": "FAILED",
                            "price": 0,
                            "pnl": 0,
                            "outcome": "FAILED",
                            "crossing": crossing,
                        })
                        continue

                    if market_result == 'FLAT':
                        exit_price = fill_price
                        pnl = 0.0
                        outcome = 'FLAT'
                    elif side == market_result:
                        exit_price = 1.0
                        pnl = filled_contracts * exit_price - filled_cost
                        outcome = 'WIN'
                    else:
                        exit_price = 0.0
                        pnl = -filled_cost
                        outcome = 'LOSS'

                    await conn.execute("""
                        UPDATE test_paper_trades
                        SET status = 'CLOSED', closed_at = NOW(),
                            exit_price = $1, pnl = $2, outcome = $3, market_result = $4
                        WHERE id = $5
                    """, exit_price, pnl, outcome, market_result, trade_id)

                    logger.info(f"[{coin}] SETTLED: {side} -> {outcome} ({market_result}) | PnL: ${pnl:.4f}")
                    self.stats["trades_settled"] += 1
                    settled_count += 1
                    total_pnl += pnl

                    results.append({
                        "side": side,
                        "status": "FILLED",
                        "price": fill_price,
                        "contracts": filled_contracts,
                        "cost": filled_cost,
                        "pnl": pnl,
                        "outcome": outcome,
                        "crossing": crossing,
                    })

                if results:
                    filled = [r for r in results if r['status'] == 'FILLED']
                    failed = [r for r in results if r['status'] == 'FAILED']
                    wins = len([r for r in filled if r['outcome'] == 'WIN'])
                    losses = len([r for r in filled if r['outcome'] == 'LOSS'])
                    candle_time_str = f"{candle_start.strftime('%H:%M')}~{candle_end.strftime('%H:%M')}"

                    # 전체 평균 진입가 계산 (방향 상관없이)
                    total_contracts = sum(r['contracts'] for r in filled)
                    total_cost = sum(r['cost'] for r in filled)
                    avg_entry = total_cost / total_contracts if total_contracts > 0 else 0

                    lines = [
                        f"<b>[5M {candle_time_str}] SETTLED: {market_result}</b>",
                        f"Filled: {len(filled)} | W:{wins} L:{losses}",
                        f"Qty: {total_contracts:.0f} | AvgEntry: ${avg_entry:.3f}",
                        f"<b>PnL: ${total_pnl:+.2f}</b>",
                    ]

                    self.notify("\n".join(lines))

            return settled_count
        except Exception as e:
            logger.error(f"[{coin}] settle_trades error: {e}")
            return 0

    async def get_price_result(self, coin: str, timeframe: str,
                               candle_start: datetime, candle_end: datetime) -> Optional[str]:
        ALLOWED_COLUMNS = {'binance_price', 'chainlink_price'}
        # Polymarket은 chainlink 기준으로 결과 결정
        price_column = 'chainlink_price'
        if price_column not in ALLOWED_COLUMNS:
            return None

        try:
            async with self.db_pool.acquire() as conn:
                cnt = await conn.fetchval(f"""
                    SELECT COUNT(*) FROM coin_prices
                    WHERE coin = $1 AND time > $2 AND {price_column} IS NOT NULL
                """, coin, candle_end + timedelta(seconds=5))

                if not cnt or cnt == 0:
                    return None

                start_row = await conn.fetchrow(f"""
                    SELECT {price_column} as price FROM coin_prices
                    WHERE coin = $1 AND time >= $2 AND {price_column} IS NOT NULL
                    ORDER BY time ASC LIMIT 1
                """, coin, candle_start)

                end_row = await conn.fetchrow(f"""
                    SELECT {price_column} as price FROM coin_prices
                    WHERE coin = $1 AND time >= $2 AND {price_column} IS NOT NULL
                    ORDER BY time ASC LIMIT 1
                """, coin, candle_end)

                if not start_row or not end_row:
                    return None

                start_price = float(start_row['price'])
                end_price = float(end_row['price'])

                if end_price > start_price:
                    result = 'UP'
                elif end_price < start_price:
                    result = 'DOWN'
                else:
                    result = 'FLAT'

                logger.info(f"[{coin}] Price result: {start_price:.2f} -> {end_price:.2f} = {result}")
                return result

        except Exception as e:
            logger.error(f"[{coin}] get_price_result error: {e}")
            return None

    async def settlement_loop(self):
        POLL_INTERVAL = 300  # 5분마다 정산

        # 시작 시 DB에서 최근 2캔들 crossing 횟수 로드
        await self._init_recent_crossing_counts()

        try:
            logger.info("[SETTLEMENT] Startup: settling pending trades...")
            await self._settle_pending_trades()
        except Exception as e:
            logger.error(f"[SETTLEMENT] Startup settle error: {e}")

        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL)
                await self._settle_pending_trades()
            except Exception as e:
                logger.error(f"Settlement loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)

    async def _settle_pending_trades(self):
        try:
            # 1. PENDING 주문 체결 여부 확인
            await self._check_pending_orders()

            # 2. 정산 대상 캔들 조회
            async with self.db_pool.acquire() as conn:
                pending = await conn.fetch("""
                    SELECT DISTINCT coin, timeframe, candle_start_time, candle_end_time
                    FROM test_paper_trades
                    WHERE strategy_name = $1
                      AND status = 'FILLED'
                      AND candle_end_time < NOW() - INTERVAL '10 seconds'
                    ORDER BY candle_start_time ASC
                """, STRATEGY_NAME)

                if pending:
                    logger.info(f"[SETTLEMENT] Found {len(pending)} pending candles")

            for row in pending:
                settled = await self.settle_trades(row['coin'], row['timeframe'],
                                                   row['candle_start_time'], row['candle_end_time'])
                if settled > 0:
                    logger.info(f"[SETTLEMENT] {row['coin']}/{row['timeframe']}: settled {settled} trades")

        except Exception as e:
            logger.error(f"[SETTLEMENT] _settle_pending_trades error: {e}")

    async def _check_pending_orders(self):
        """PENDING 상태의 maker 주문들 체결 여부 확인"""
        if not self.is_live_enabled:
            return

        try:
            async with self.db_pool.acquire() as conn:
                pending_orders = await conn.fetch("""
                    SELECT id, order_id, coin, side, contracts, order_price
                    FROM test_paper_trades
                    WHERE strategy_name = $1
                      AND status = 'PENDING'
                      AND order_id IS NOT NULL
                      AND candle_end_time < NOW() - INTERVAL '5 seconds'
                """, STRATEGY_NAME)

                if not pending_orders:
                    return

                logger.info(f"[PENDING] Checking {len(pending_orders)} pending orders...")

                for order in pending_orders:
                    trade_id = order['id']
                    order_id = order['order_id']
                    coin = order['coin']
                    side = order['side']

                    try:
                        result = self.clob_client.get_order(order_id)

                        if result:
                            size_matched = float(result.get('size_matched', 0) or 0)

                            if size_matched > 0:
                                fill_price = order['order_price']
                                filled_qty = round(size_matched)
                                await conn.execute("""
                                    UPDATE test_paper_trades
                                    SET status = 'FILLED',
                                        filled_contracts = $1,
                                        filled_cost = $2,
                                        fill_price = $3,
                                        fill_time = NOW()
                                    WHERE id = $4
                                """, filled_qty, size_matched * float(fill_price), float(fill_price), trade_id)
                                logger.info(f"[PENDING] {coin} {side} order {order_id[:16]}... FILLED: {size_matched}")
                            else:
                                await conn.execute("""
                                    UPDATE test_paper_trades
                                    SET status = 'EXPIRED'
                                    WHERE id = $1
                                """, trade_id)
                                logger.info(f"[PENDING] {coin} {side} order {order_id[:16]}... EXPIRED (no fill)")
                        else:
                            await conn.execute("""
                                UPDATE test_paper_trades
                                SET status = 'EXPIRED'
                                WHERE id = $1
                            """, trade_id)
                            logger.warning(f"[PENDING] {coin} {side} order {order_id[:16]}... not found, marking EXPIRED")

                    except Exception as e:
                        logger.error(f"[PENDING] Error checking order {order_id[:16]}...: {e}")

        except Exception as e:
            logger.error(f"[PENDING] _check_pending_orders error: {e}")

    # =========================================================================
    # Stats & Watchdog
    # =========================================================================
    async def stats_loop(self):
        while True:
            await asyncio.sleep(STATS_INTERVAL)
            uptime = int(time.time() - self.start_time)
            strategy_status = {c: s.get_status() for c, s in self.strategies.items()}
            logger.info(
                f"[STATS] uptime={uptime}s | strategy={STRATEGY_NAME} | "
                f"live={self.is_live_enabled} | "
                f"ob_msgs={self.stats.get('ob_messages', 0)} | "
                f"crossing={self.stats.get('crossing_events', 0)} | "
                f"filled={self.stats.get('orders_filled', 0)} | "
                f"failed={self.stats.get('orders_failed', 0)} | "
                f"book_pass={self.stats.get('book_depth_passes', 0)} | "
                f"book_skip={self.stats.get('book_depth_skips', 0)} | "
                f"book_recover={self.stats.get('book_depth_recoveries', 0)} | "
                f"trades={self.stats['trades_created']}/{self.stats['trades_settled']} | "
                f"status={strategy_status}"
            )

    async def p1_watchdog(self):
        P1_TIMEOUT = 30
        while True:
            await asyncio.sleep(10)
            if self.last_ob_message_time == 0:
                continue
            elapsed = time.time() - self.last_ob_message_time
            if elapsed > P1_TIMEOUT and not self.p1_disconnect_warned:
                logger.warning(f"[P1] No orderbook messages for {elapsed:.0f}s — P1 may be down")
                self.notify(f"<b>[WARNING]</b> No orderbook messages for {elapsed:.0f}s")
                self.p1_disconnect_warned = True

    # =========================================================================
    # Main
    # =========================================================================
    async def run(self):
        logger.info("=" * 60)
        logger.info(f"Real Trader 5M Improve: Max{CROSSING_MAX_COUNT} + 속도필터 + 책깊이 가드")
        logger.info("=" * 60)
        logger.info(f"Strategy: {STRATEGY_NAME}")
        logger.info(f"  Entry zone: 0s ~ {CROSSING_ENTRY_CUTOFF_SECONDS}s")
        logger.info(f"  Hedge zone: {CROSSING_ENTRY_CUTOFF_SECONDS}s ~ {CROSSING_HEDGE_CUTOFF_SECONDS}s")
        logger.info(f"  Forbidden: {CROSSING_HEDGE_CUTOFF_SECONDS}s+")
        logger.info(f"  Max entries: {CROSSING_MAX_COUNT} (last entry forced as HEDGE_MAX)")
        logger.info(f"  Sizes: #1=x{CROSSING_BET_CONTRACT_UNIT}, #2~{CROSSING_MAX_COUNT-1}=x{CROSSING_BET_CONTRACT_UNIT*2}, hedge=x{CROSSING_BET_CONTRACT_UNIT}")
        logger.info(f"  Speed filter: {SPEED_FILTER_MAX_CROSSINGS} crossings in {SPEED_FILTER_WINDOW_SECONDS}s → hedge")
        logger.info(f"  GTC price: {GTC_FIXED_PRICE} (fixed)")
        logger.info(f"  Cooltime: {COOLTIME_SECONDS}s")
        logger.info(f"  Book depth guard: required={BOOK_DEPTH_REQUIRED} safety={BOOK_DEPTH_SAFETY} pending={BOOK_DEPTH_PENDING_ENABLED}")
        logger.info(f"  Signals log: {self._signals_log_path}")
        logger.info(f"Coins: {COINS} | Timeframes: {TIMEFRAMES}")
        logger.info(f"Telegram: {'enabled' if TELEGRAM_BOT_TOKEN else 'disabled'}")
        logger.info("=" * 60)

        self._init_clob_client()
        if self.is_live_enabled:
            logger.info("LIVE TRADING ENABLED")
            self.notify(
                f"<b>[STARTUP]</b> {STRATEGY_NAME}\n"
                f"Entry: 0~{CROSSING_ENTRY_CUTOFF_SECONDS}s | Hedge: {CROSSING_ENTRY_CUTOFF_SECONDS}~{CROSSING_HEDGE_CUTOFF_SECONDS}s\n"
                f"Speed: {SPEED_FILTER_MAX_CROSSINGS}x/{SPEED_FILTER_WINDOW_SECONDS}s | Max {CROSSING_MAX_COUNT}x\n"
                f"GTC {GTC_FIXED_PRICE} | Cooltime {COOLTIME_SECONDS}s | Live: ON\n"
                f"BookGuard: {'ON' if BOOK_DEPTH_REQUIRED else 'OFF'} (safety {BOOK_DEPTH_SAFETY}, pending {'ON' if BOOK_DEPTH_PENDING_ENABLED else 'OFF'})"
            )
        else:
            logger.warning("LIVE TRADING DISABLED (no credentials)")
            self.notify(f"<b>[STARTUP]</b> {STRATEGY_NAME}\nLive: DISABLED")

        if not await self.connect_db():
            logger.error("Failed to connect to DB")
            return

        if not await self.connect_redis():
            logger.error("Failed to connect to Redis")
            return

        self.setup_signal_handlers()

        asyncio.create_task(self.telegram_sender())

        await asyncio.gather(
            self.redis_subscriber(),
            self.settlement_loop(),
            self.stats_loop(),
            self.p1_watchdog(),
            self._health_file_loop(),
        )


def main():
    service = RealTrader5MCrossFrontService()
    asyncio.run(service.run())


if __name__ == "__main__":
    main()
