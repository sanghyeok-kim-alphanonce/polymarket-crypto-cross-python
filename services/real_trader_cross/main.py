"""
Real Trader Cross V2: 13분 이후 첫 Crossing 전략

- 13분~14분30초 사이 첫 crossing에만 진입
- 고정 금액 GTC 주문 (예: $10 @ 0.58)
- Redis subscribe: ch:crossing:*, ch:orderbook:*
- 텔레그램 알림 (별도 코루틴, 블로킹 없음)
- BTC only
"""
import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass

import aiohttp

from config import (
    STRATEGY_NAME, COINS, TIMEFRAMES,
    CROSSING_BET_SIZE, CROSSING_MIN_ELAPSED_SECONDS, CROSSING_CUTOFF_SECONDS,
    CROSSING_MAX_ENTRIES,
    HEDGE_ENABLED, HEDGE_GTC_PRICE, HEDGE_GTC_SIZE,
    get_gtc_price,
    POLYMARKET_HOST, POLYMARKET_CHAIN_ID,
    POLYMARKET_PRIVATE_KEY, POLYMARKET_PROXY_ADDRESS,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    REDIS_HOST, REDIS_PORT,
    STATS_INTERVAL,
)

# Packages
from polymarket_common import generate_slug
from service_common import AsyncServiceBase, setup_logging

logger = setup_logging(__name__)


# =============================================================================
# Crossing Strategy (paper_trader_cross에서 복사)
# =============================================================================
@dataclass
class TradeSignal:
    side: str
    reason: str
    bet_size: float  # 베팅 금액 ($)


class CrossingStrategy:
    """
    13분 이후 Crossing 전략 (최대 N회)
    - 캔들당 최대 CROSSING_MAX_ENTRIES회 진입
    - MAX_ENTRIES 도달 시 hedge GTC 트리거
    - 고정 금액 GTC 주문
    """

    name = "crossing_v2"
    description = "13분 이후 crossing에 GTC 진입 (max N회)"

    def __init__(self):
        self.candle_state: Dict[str, Dict[str, Any]] = {}

    def _get_candle_state(self, candle_key: str) -> Dict[str, Any]:
        if candle_key not in self.candle_state:
            self.candle_state[candle_key] = {
                "entry_count": 0,
                "directions": [],  # 진입한 방향들
                "failed": False,
                "hedge_triggered": False,  # hedge GTC 이미 실행됨
            }
        return self.candle_state[candle_key]

    def mark_candle_failed(self, candle_key: str):
        """주문 실패 시 호출"""
        state = self._get_candle_state(candle_key)
        state["failed"] = True

    def should_enter_on_crossing(
        self,
        elapsed_seconds: int,
        crossing_direction: str,
        candle_key: str,
    ) -> Optional[TradeSignal]:
        state = self._get_candle_state(candle_key)

        # 실패했으면 skip
        if state["failed"]:
            return None

        # 최대 진입 횟수 도달
        if state["entry_count"] >= CROSSING_MAX_ENTRIES:
            return None

        # 최소 시간 이전 무시
        if elapsed_seconds < CROSSING_MIN_ELAPSED_SECONDS:
            return None

        # cutoff 이후 무시
        if elapsed_seconds >= CROSSING_CUTOFF_SECONDS:
            return None

        # crossing 진입
        state["entry_count"] += 1
        state["directions"].append(crossing_direction)

        side = "up" if crossing_direction == "up" else "down"
        reason = f"CROSS_{crossing_direction.upper()} #{state['entry_count']} @{elapsed_seconds}s"

        return TradeSignal(
            side=side,
            reason=reason,
            bet_size=CROSSING_BET_SIZE,
        )

    def should_trigger_hedge(self, candle_key: str) -> bool:
        """MAX_ENTRIES 도달 시 hedge GTC 트리거 여부"""
        state = self._get_candle_state(candle_key)
        if state["entry_count"] >= CROSSING_MAX_ENTRIES and not state["hedge_triggered"]:
            state["hedge_triggered"] = True
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        active = [(k, v["entry_count"]) for k, v in self.candle_state.items() if v["entry_count"] > 0]
        return {"active_candles": len(active), "entries": dict(active)}


# =============================================================================
# Real Trader Service
# =============================================================================
class RealTraderCrossService(AsyncServiceBase):
    """Real Trader: Crossing Strategy with Live Trading"""

    def __init__(self):
        super().__init__(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, REDIS_HOST, REDIS_PORT)

        self.ob_cache: Dict[str, Dict] = {}

        self.strategies: Dict[str, CrossingStrategy] = {}
        for coin in COINS:
            self.strategies[coin] = CrossingStrategy()

        self.stats = defaultdict(int)
        self.start_time = time.time()

        self.last_ob_message_time: float = 0.0
        self.p1_disconnect_warned: bool = False

        # Telegram queue (non-blocking)
        self.telegram_queue: asyncio.Queue = asyncio.Queue()

        # CLOB client
        self.clob_client = None
        self._clob_initialized = False
        self.token_info_cache: Dict[str, Dict] = {}  # 캔들 시작 시 조회 (tick_size, neg_risk, fee_rate)

    def _init_clob_client(self):
        """Initialize Polymarket CLOB client"""
        if not POLYMARKET_PRIVATE_KEY:
            logger.warning("POLYMARKET_PRIVATE_KEY not set, live trading disabled")
            return

        if not POLYMARKET_PROXY_ADDRESS:
            logger.warning("POLYMARKET_PROXY_ADDRESS not set, live trading disabled")
            return

        try:
            from py_clob_client.client import ClobClient

            # signature_type=2 (Poly Proxy)
            self.clob_client = ClobClient(
                host=POLYMARKET_HOST,
                key=POLYMARKET_PRIVATE_KEY,
                chain_id=POLYMARKET_CHAIN_ID,
                signature_type=2,  # Poly Proxy
                funder=POLYMARKET_PROXY_ADDRESS,
            )

            api_creds = self.clob_client.create_or_derive_api_creds()
            self.clob_client.set_api_creds(api_creds)

            self._clob_initialized = True
            logger.info(f"CLOB client initialized (host={POLYMARKET_HOST}, chain={POLYMARKET_CHAIN_ID})")

        except ImportError:
            logger.error("py-clob-client not installed, live trading disabled")
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
        """별도 코루틴: 큐에서 메시지 꺼내서 전송 (메인 로직 블로킹 없음)"""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.info("Telegram not configured, sender disabled")
            return

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    msg = await self.telegram_queue.get()
                    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                    await session.post(url, json={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": msg,
                        "parse_mode": "HTML"
                    }, timeout=aiohttp.ClientTimeout(total=10))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Telegram send error: {e}")

    def notify(self, message: str):
        """텔레그램 알림 (non-blocking)"""
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            try:
                self.telegram_queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("Telegram queue full, dropping message")

    # =========================================================================
    # Redis Pub/Sub Subscriber
    # =========================================================================
    async def redis_subscriber(self):
        """ch:crossing:*, ch:orderbook:*, ch:candle_boundary 구독"""
        while True:
            try:
                pubsub = self.redis_client.pubsub()
                # TIMEFRAMES에 맞는 candle_boundary만 구독
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

        # BTC only
        if coin not in COINS:
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

        # 토큰 정보 미리 캐싱 (주문 시 HTTP 요청 생략)
        if token_id and token_id not in self.token_info_cache:
            asyncio.create_task(self._prefetch_token_info(token_id))

        self.stats["ob_messages"] += 1
        self.last_ob_message_time = time.time()
        if self.p1_disconnect_warned:
            logger.info("[P1] Connection restored")
            self.p1_disconnect_warned = False

    async def _prefetch_token_info(self, token_id: str):
        """캔들 시작 시 토큰 정보 조회해서 캐싱 (주문 시 HTTP 요청 생략)"""
        if not self.is_live_enabled:
            return
        if token_id in self.token_info_cache:
            return
        try:
            tick_size = self.clob_client.get_tick_size(token_id)
            neg_risk = self.clob_client.get_neg_risk(token_id)
            fee_rate = self.clob_client.get_fee_rate_bps(token_id)
            self.token_info_cache[token_id] = {
                "tick_size": tick_size,
                "neg_risk": neg_risk,
                "fee_rate": fee_rate,
            }
            logger.info(f"[PREFETCH] Cached: {token_id[:16]}... tick={tick_size} neg_risk={neg_risk} fee={fee_rate}bps")
        except Exception as e:
            logger.warning(f"[PREFETCH] Failed to cache token info: {e}")

    async def _handle_candle_boundary(self, data: Dict):
        """캔들 경계 → 전략 리셋"""
        tf = data.get("timeframe", "?")
        logger.info(f"[CANDLE_BOUNDARY] Resetting strategies (tf={tf})")
        self.ob_cache.clear()
        self.token_info_cache.clear()
        for strategy in self.strategies.values():
            strategy.candle_state.clear()

    async def _handle_crossing_event(self, data: Dict):
        """Crossing 이벤트 수신 → 즉시 주문 실행"""
        coin = data.get("coin")
        tf = data.get("timeframe")
        direction = data.get("direction")
        candle_start_str = data.get("candle_start")
        candle_end_str = data.get("candle_end")

        if not all([coin, tf, direction, candle_start_str, candle_end_str]):
            logger.warning(f"[CROSSING] Invalid data: {data}")
            return

        # BTC only
        if coin not in COINS:
            return

        if coin not in self.strategies:
            return

        self.stats["crossing_events"] += 1

        try:
            candle_start = datetime.fromisoformat(candle_start_str.replace('Z', '+00:00'))
            candle_end = datetime.fromisoformat(candle_end_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            elapsed_seconds = int((now - candle_start).total_seconds())
        except Exception as e:
            logger.error(f"[CROSSING] Time parse error: {e}")
            return

        if now >= candle_end:
            return

        # token_id 확인용으로만 orderbook 캐시 사용 (가격은 안 봄)
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
        signal = strategy.should_enter_on_crossing(
            elapsed_seconds=elapsed_seconds,
            crossing_direction=direction,
            candle_key=candle_key,
        )

        if signal:
            crossing_info = {
                "direction": direction,
                "candle_open": data.get("candle_open"),
                "prev_price": data.get("prev_price"),
                "current_price": data.get("current_price"),
                "elapsed_seconds": elapsed_seconds,
            }

            # Fire-and-forget: 주문 실행 (메인 루프 블로킹 없음)
            asyncio.create_task(self.execute_order(
                coin, tf, signal, token_id,
                candle_start, candle_end,
                crossing_info=crossing_info,
                candle_key=candle_key,
            ))

            # 10회 채우면 hedge GTC 트리거
            if HEDGE_ENABLED and strategy.should_trigger_hedge(candle_key):
                logger.info(f"[{coin}] MAX_ENTRIES reached, triggering hedge GTC")
                asyncio.create_task(self.execute_hedge_orders(
                    coin, tf, candle_start, candle_end,
                ))

    def get_orderbook(self, coin: str, timeframe: str, side: str = "up") -> Optional[Dict]:
        """orderbook 캐시에서 token_id, market_slug 등 가져오기"""
        key = f"{coin}_{timeframe}_{side}"
        cached = self.ob_cache.get(key)
        if not cached:
            return None
        # 60초 TTL (token_id 확인용)
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
        """실제 주문 실행 (GTC) - 시간대별 동적 가격"""
        side = signal.side.upper()
        bet_size = signal.bet_size
        reason = signal.reason

        # 경과 시간에 따른 GTC 가격 결정
        elapsed_seconds = crossing_info.get('elapsed_seconds', 870) if crossing_info else 870
        gtc_price = get_gtc_price(elapsed_seconds)

        # bet_size($) -> contracts 변환: contracts = bet_size / price
        contracts = int(bet_size / gtc_price)
        if contracts < 1:
            contracts = 1

        # 주문 전 5ms sleep + best_ask 로그
        await asyncio.sleep(0.005)
        orderbook = self.get_orderbook(coin, timeframe, signal.side)
        best_ask_pre = orderbook.get('best_ask', 0) if orderbook else 0
        best_bid_pre = orderbook.get('best_bid', 0) if orderbook else 0
        logger.info(f"[{coin}] PRE-ORDER: {side} ${bet_size} (x{contracts}) | best_ask={best_ask_pre:.3f} GTC={gtc_price:.2f} @{elapsed_seconds}s")

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
            # GTC 주문 실행
            order_result = await self._place_gtc_order(
                coin=coin,
                token_id=token_id,
                target_contracts=contracts,
                gtc_price=gtc_price,
            )

        latency_ms = order_result.get("total_latency_ms", 0)
        filled = order_result.get("filled_contracts", 0)
        fill_price = order_result.get("fill_price", 0.0)

        # 주문 후 5ms sleep + best_ask 로그
        await asyncio.sleep(0.005)
        orderbook_post = self.get_orderbook(coin, timeframe, signal.side)
        best_ask_post = orderbook_post.get('best_ask', 0) if orderbook_post else 0
        best_bid_post = orderbook_post.get('best_bid', 0) if orderbook_post else 0
        logger.info(f"[{coin}] POST-ORDER: best_ask {best_ask_pre:.3f}→{best_ask_post:.3f} | best_bid {best_bid_pre:.3f}→{best_bid_post:.3f}")

        # stop_price 로직 제거됨 (GTC에서는 의미 없음)

        # DB 기록
        await self._record_trade(
            coin, timeframe, signal, order_result,
            candle_start, candle_end,
            crossing_info, latency_ms
        )

        # crossing 정보 추출
        cross_dir = crossing_info.get('direction', '?').upper() if crossing_info else '?'
        candle_open = (crossing_info.get('candle_open') or 0) if crossing_info else 0
        prev_price = (crossing_info.get('prev_price') or 0) if crossing_info else 0
        curr_price = (crossing_info.get('current_price') or 0) if crossing_info else 0
        elapsed_sec = (crossing_info.get('elapsed_seconds') or 0) if crossing_info else 0

        target = order_result.get("target_contracts", contracts)

        # 결과 로그 및 텔레그램
        if order_result.get("success"):
            self.stats["orders_filled"] += 1

            logger.info(
                f"[{coin}] GTC ORDER: {side} ${bet_size} (x{contracts}) | "
                f"filled={filled} @{fill_price:.3f} | latency={latency_ms:.0f}ms"
            )

            status_mark = "✓" if filled >= target else "⚠️"
            self.notify(
                f"✓ <b>{cross_dir}</b> @{elapsed_sec//60}m{elapsed_sec%60}s | "
                f"{prev_price:.2f}→{curr_price:.2f} (open:{candle_open:.2f})\n"
                f"${bet_size} x{contracts} @{fill_price:.3f} {filled}/{target} {status_mark}"
            )
        else:
            self.stats["orders_failed"] += 1
            error = order_result.get("error", "Unknown")

            logger.error(f"[{coin}] GTC ORDER FAILED: {side} ${bet_size} (x{contracts}) | error={error}")

            # 주문 실패 시 해당 캔들 더 이상 거래 안함
            if candle_key and coin in self.strategies:
                self.strategies[coin].mark_candle_failed(candle_key)

            self.notify(
                f"✗ <b>{cross_dir}</b> @{elapsed_sec//60}m{elapsed_sec%60}s | "
                f"{prev_price:.2f}→{curr_price:.2f} (open:{candle_open:.2f})\n"
                f"${bet_size} FAILED: {error}"
            )

    async def execute_hedge_orders(
        self,
        coin: str,
        timeframe: str,
        candle_start: datetime,
        candle_end: datetime,
    ):
        """
        10회 채운 후 양쪽에 저가 GTC 주문
        - UP @ 0.4 x 4개
        - DOWN @ 0.4 x 4개
        진동 중 체결되면 수익
        """
        if not self.is_live_enabled:
            logger.warning(f"[{coin}] HEDGE: CLOB not enabled, skipping")
            return

        logger.info(f"[{coin}] HEDGE: Placing GTC orders @ {HEDGE_GTC_PRICE} x {HEDGE_GTC_SIZE} each side")

        results = []

        for side in ["up", "down"]:
            orderbook = self.get_orderbook(coin, timeframe, side)
            if not orderbook:
                logger.warning(f"[{coin}] HEDGE: No {side.upper()} orderbook cache")
                continue

            token_id = orderbook.get('token_id', '')
            if not token_id:
                logger.error(f"[{coin}] HEDGE: No token_id for {side.upper()}")
                continue

            order_result = await self._place_gtc_order(
                coin=coin,
                token_id=token_id,
                target_contracts=HEDGE_GTC_SIZE,
                gtc_price=HEDGE_GTC_PRICE,
            )

            filled = order_result.get("filled_contracts", 0)
            order_id = order_result.get("order_id", "")

            results.append({
                "side": side.upper(),
                "filled": filled,
                "order_id": order_id,
                "success": order_result.get("success", False),
            })

            if order_result.get("success"):
                logger.info(f"[{coin}] HEDGE {side.upper()}: filled={filled} order_id={order_id[:16]}...")
            else:
                logger.warning(f"[{coin}] HEDGE {side.upper()}: error={order_result.get('error')}")

            # DB 기록
            await self._record_hedge_trade(
                coin, timeframe, side.upper(), order_result,
                candle_start, candle_end,
            )

        # 텔레그램 알림
        up_result = next((r for r in results if r["side"] == "UP"), {})
        down_result = next((r for r in results if r["side"] == "DOWN"), {})

        self.notify(
            f"🛡 <b>HEDGE GTC</b> (10회 도달)\n"
            f"UP @{HEDGE_GTC_PRICE} x{HEDGE_GTC_SIZE}: {'✓' if up_result.get('success') else '✗'}\n"
            f"DOWN @{HEDGE_GTC_PRICE} x{HEDGE_GTC_SIZE}: {'✓' if down_result.get('success') else '✗'}"
        )

    async def _record_hedge_trade(
        self,
        coin: str,
        timeframe: str,
        side: str,
        order_result: Dict[str, Any],
        candle_start: datetime,
        candle_end: datetime,
    ):
        """Hedge 거래 기록 (DB)"""
        try:
            order_price = HEDGE_GTC_PRICE
            target_contracts = HEDGE_GTC_SIZE
            filled_contracts = order_result.get('filled_contracts', 0)
            filled_cost = order_result.get('filled_cost', 0.0)

            snapshot_data = {
                'hedge': True,
                'order_result': order_result,
            }

            status = 'FILLED' if order_result.get('success') else 'FAILED'

            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO test_paper_trades (
                        time, strategy_name, coin, timeframe,
                        mid_price, up_mid_price, down_mid_price,
                        side, order_price, reason, orderbook_snapshot,
                        market_slug, token_id, candle_start_time, candle_end_time,
                        contracts, cost, filled_contracts, filled_cost, status
                    ) VALUES (
                        NOW(), $1, $2, $3,
                        $4, $5, $6,
                        $7, $8, $9, $10,
                        $11, $12, $13, $14,
                        $15, $16, $17, $18, $19
                    )
                """,
                    STRATEGY_NAME, coin, timeframe,
                    0.5, None, None,
                    side, order_price, f"HEDGE @{order_price}", json.dumps(snapshot_data),
                    f'{coin}-{timeframe}', '', candle_start, candle_end,
                    target_contracts, target_contracts * order_price,
                    filled_contracts, filled_cost,
                    status,
                )

            logger.info(f"[{coin}] HEDGE trade recorded: {side} status={status}")

        except Exception as e:
            logger.error(f"[{coin}] _record_hedge_trade error: {e}")

    async def _place_gtc_order(
        self,
        coin: str,
        token_id: str,
        target_contracts: int,
        gtc_price: float,
    ) -> Dict[str, Any]:
        """
        GTC 주문 실행 (재시도 없음)
        - 가격: 시간대별 동적 가격
        - 수량 기준 체결
        - 미체결분은 오더북에 bid로 남음
        """
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        start_time = time.time()
        order_price = gtc_price

        try:
            # 토큰 정보 캐시 확인
            cached = self.token_info_cache.get(token_id)
            if cached:
                from py_clob_client.clob_types import PartialCreateOrderOptions
                order_args = OrderArgs(
                    token_id=token_id,
                    price=order_price,
                    size=float(target_contracts),
                    side=BUY,
                    fee_rate_bps=cached["fee_rate"],
                )
                options = PartialCreateOrderOptions(
                    tick_size=cached["tick_size"],
                    neg_risk=cached["neg_risk"],
                )
                signed_order = self.clob_client.create_order(order_args, options)
            else:
                order_args = OrderArgs(
                    token_id=token_id,
                    price=order_price,
                    size=float(target_contracts),
                    side=BUY,
                )
                signed_order = self.clob_client.create_order(order_args)

            # GTC 주문 실행
            response = self.clob_client.post_order(signed_order, OrderType.GTC)
            logger.info(f"[GTC] CLOB response (price={order_price}, size={target_contracts}): {response}")

            if isinstance(response, dict):
                order_id = response.get("orderID") or response.get("orderId", "")

                if response.get("success", True) and order_id:
                    # 체결량 파싱
                    filled = 0
                    cost = 0.0
                    try:
                        making_amt = response.get("makingAmount", "0")
                        taking_amt = response.get("takingAmount", "0")
                        # BUY: takingAmount=받은 shares, makingAmount=지불한 USDC
                        filled = float(taking_amt) if taking_amt else 0.0
                        cost = float(making_amt) if making_amt else 0.0
                    except (ValueError, TypeError):
                        pass

                    # 실제 체결가 계산
                    fill_price = cost / filled if filled > 0 else order_price

                    total_latency = (time.time() - start_time) * 1000

                    return {
                        "success": filled > 0,
                        "target_contracts": target_contracts,
                        "filled_contracts": filled,
                        "filled_cost": cost,
                        "fill_price": fill_price,
                        "order_id": order_id,
                        "order_price": order_price,
                        "total_latency_ms": total_latency,
                        "error": None if filled > 0 else "No fill",
                    }
                else:
                    # 실패
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
                # 응답이 dict가 아닌 경우
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
            logger.error(f"[GTC] Exception: {error_msg}")

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
        """거래 기록 (DB)"""
        try:
            side = signal.side.upper()
            order_price = order_result.get('order_price', 0.70)  # fallback
            target_contracts = order_result.get('target_contracts', int(signal.bet_size / order_price))
            reason = signal.reason

            # 실제 체결량 (order_result에서 가져옴)
            filled_contracts = order_result.get('filled_contracts', 0)
            filled_cost = order_result.get('filled_cost', 0.0)
            fill_price = order_result.get('fill_price', 0.0)

            snapshot_data = {
                'crossing': crossing_info,
                'order_result': order_result,
                'latency_ms': latency_ms,
            }

            # 부분 체결도 FILLED로 (filled_contracts > 0)
            status = 'FILLED' if order_result.get('success') else 'FAILED'

            async with self.db_pool.acquire() as conn:
                trade_id = await conn.fetchval("""
                    INSERT INTO test_paper_trades (
                        time, strategy_name, coin, timeframe,
                        mid_price, up_mid_price, down_mid_price,
                        side, order_price, reason, orderbook_snapshot,
                        market_slug, token_id, candle_start_time, candle_end_time,
                        contracts, cost, filled_contracts, filled_cost, status
                    ) VALUES (
                        NOW(), $1, $2, $3,
                        $4, $5, $6,
                        $7, $8, $9, $10,
                        $11, $12, $13, $14,
                        $15, $16, $17, $18, $19
                    )
                    RETURNING id
                """,
                    STRATEGY_NAME, coin, timeframe,
                    0.5, None, None,  # mid_price (GTC라 orderbook 안 봄)
                    side, order_price, reason, json.dumps(snapshot_data),
                    f'{coin}-{timeframe}', '', candle_start, candle_end,
                    target_contracts, target_contracts * order_price,  # 주문 수량/비용
                    filled_contracts, filled_cost,  # 실제 체결 수량/비용
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

    # =========================================================================
    # Settlement (Paper trade와 동일)
    # =========================================================================
    async def settle_trades(self, coin: str, timeframe: str,
                            candle_start: datetime, candle_end: datetime) -> int:
        market_result = await self.get_price_result(coin, timeframe, candle_start, candle_end)
        if not market_result:
            return 0

        settled_count = 0
        try:
            async with self.db_pool.acquire() as conn:
                # FILLED + FAILED 모두 가져오기
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
                    # 실제 체결량 사용 (없으면 주문 수량으로 fallback)
                    filled_contracts = float(trade['filled_contracts']) if trade['filled_contracts'] is not None else 0.0
                    filled_cost = float(trade['filled_cost']) if trade['filled_cost'] is not None else 0.0
                    # fallback: 구버전 데이터는 contracts/cost 사용
                    if filled_contracts == 0 and trade['contracts']:
                        filled_contracts = float(trade['contracts'])
                        filled_cost = float(trade['cost']) if trade['cost'] else 0.0

                    # orderbook_snapshot 파싱
                    snapshot = {}
                    if trade['orderbook_snapshot']:
                        try:
                            snapshot = json.loads(trade['orderbook_snapshot']) if isinstance(trade['orderbook_snapshot'], str) else trade['orderbook_snapshot']
                        except:
                            pass

                    crossing = snapshot.get('crossing', {})
                    order_result = snapshot.get('order_result', {})
                    ob_side = snapshot.get('up' if side == 'UP' else 'down', {})

                    # FAILED는 정산하지 않음
                    if status == 'FAILED':
                        results.append({
                            "side": side,
                            "status": "FAILED",
                            "price": 0,
                            "pnl": 0,
                            "outcome": "FAILED",
                            "crossing": crossing,
                            "best_ask": ob_side.get('best_ask'),
                        })
                        continue

                    if market_result == 'FLAT':
                        exit_price = fill_price
                        pnl = 0.0
                        outcome = 'FLAT'
                    elif side == market_result:
                        exit_price = 1.0
                        # 수수료는 filled_cost에 이미 포함됨 (체결 시 차감)
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
                        "pnl": pnl,
                        "outcome": outcome,
                        "crossing": crossing,
                        "best_ask": ob_side.get('best_ask'),
                        "final_price": order_result.get('final_price'),
                    })

                # 텔레그램 알림: 정산 결과 요약
                if results:
                    filled = [r for r in results if r['status'] == 'FILLED']
                    failed = [r for r in results if r['status'] == 'FAILED']
                    wins = len([r for r in filled if r['outcome'] == 'WIN'])
                    losses = len([r for r in filled if r['outcome'] == 'LOSS'])
                    total_cost_actual = sum(float(t['cost']) for t in trades if t['status'] == 'FILLED')
                    candle_time_str = candle_start.strftime('%H:%M')

                    lines = [
                        f"<b>[{candle_time_str}] SETTLED: {market_result}</b>",
                        f"",
                    ]

                    # 각 거래 상세 (가격 변화 없이 진입 정보만)
                    for r in results:
                        crossing = r.get('crossing', {})
                        direction = crossing.get('direction', '?').upper()
                        best_ask = r.get('best_ask') or 0
                        final_price = r.get('final_price') or r.get('price') or 0

                        if r['status'] == 'FAILED':
                            lines.append(f"FAIL {direction} ask:{best_ask:.2f}")
                        else:
                            mark = "W" if r['outcome'] == 'WIN' else "L"
                            lines.append(f"{mark} {direction} ask:{best_ask:.2f} fill:{final_price:.2f} -> ${r['pnl']:+.2f}")

                    # 요약
                    lines.append(f"")
                    lines.append(f"Filled: {len(filled)} | Failed: {len(failed)} | W:{wins} L:{losses}")
                    lines.append(f"<b>PnL: ${total_pnl:+.2f}</b>")

                    self.notify("\n".join(lines))

            return settled_count
        except Exception as e:
            logger.error(f"[{coin}] settle_trades error: {e}")
            return 0

    async def get_price_result(self, coin: str, timeframe: str,
                               candle_start: datetime, candle_end: datetime) -> Optional[str]:
        ALLOWED_COLUMNS = {'binance_price', 'chainlink_price'}
        price_column = 'binance_price' if timeframe == '1h' else 'chainlink_price'
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
        POLL_INTERVAL = 60

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
            async with self.db_pool.acquire() as conn:
                pending = await conn.fetch("""
                    SELECT DISTINCT coin, timeframe, candle_start_time, candle_end_time
                    FROM test_paper_trades
                    WHERE strategy_name = $1
                      AND status = 'FILLED'
                      AND candle_end_time < NOW() - INTERVAL '20 seconds'
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
        logger.info("Real Trader Cross V2: 13분 이후 Crossing 전략")
        logger.info("=" * 60)
        logger.info(f"Strategy: {STRATEGY_NAME}")
        logger.info(f"  Bet size: ${CROSSING_BET_SIZE}")
        logger.info(f"  GTC price: dynamic (0.63~0.80 based on elapsed time)")
        logger.info(f"  Entry: {CROSSING_MIN_ELAPSED_SECONDS}s ~ {CROSSING_CUTOFF_SECONDS}s (max {CROSSING_MAX_ENTRIES}회)")
        logger.info(f"  Hedge: {'enabled' if HEDGE_ENABLED else 'disabled'} @ {HEDGE_GTC_PRICE} x {HEDGE_GTC_SIZE}")
        logger.info(f"Coins: {COINS} | Timeframes: {TIMEFRAMES}")
        logger.info(f"Telegram: {'enabled' if TELEGRAM_BOT_TOKEN else 'disabled'}")
        logger.info("=" * 60)

        # Initialize CLOB client
        self._init_clob_client()
        if self.is_live_enabled:
            logger.info("LIVE TRADING ENABLED")
            self.notify(
                f"<b>[STARTUP]</b> {STRATEGY_NAME}\n"
                f"Entry: {CROSSING_MIN_ELAPSED_SECONDS//60}m~{CROSSING_CUTOFF_SECONDS//60}m{CROSSING_CUTOFF_SECONDS%60}s (max {CROSSING_MAX_ENTRIES}회)\n"
                f"Bet: ${CROSSING_BET_SIZE} (GTC dynamic 0.63~0.80)\n"
                f"Hedge: @{HEDGE_GTC_PRICE} x{HEDGE_GTC_SIZE} (after max)\n"
                f"Live: ENABLED"
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
    service = RealTraderCrossService()
    asyncio.run(service.run())


if __name__ == "__main__":
    main()
