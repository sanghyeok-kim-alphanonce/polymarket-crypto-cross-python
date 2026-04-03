"""
Paper Trader 1H Late: 1시간봉 후반(56분~) Crossing 전략

STRATEGY.md 최적 전략:
- 진입: 3360초(56분) ~ 3590초(59분50초)
- 헤지: 3590초 ~ 3600초
- Max K: 5, 속도: 20초 내 3회 → 헤지
- Taker 체결 (best_ask)
"""
import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Any
from dataclasses import dataclass

from config import (
    STRATEGY_NAME, COINS, TIMEFRAMES,
    CROSSING_BET_CONTRACT_UNIT,
    CROSSING_MAX_COUNT, CROSSING_MIN_ELAPSED_SECONDS, CROSSING_CUTOFF_SECONDS,
    CROSSING_ENTRY_CUTOFF_SECONDS, CROSSING_HEDGE_CUTOFF_SECONDS,
    SPEED_FILTER_WINDOW_SECONDS, SPEED_FILTER_MAX_CROSSINGS,
    CROSSING_MIN_ENTRY_PRICE, CROSSING_MAX_ENTRY_PRICE,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    REDIS_HOST, REDIS_PORT,
    STATS_INTERVAL, OB_CACHE_TTL,
)

# Packages
from polymarket_common import generate_slug
from service_common import AsyncServiceBase, setup_logging

logger = setup_logging(__name__)


# =============================================================================
# Crossing Strategy (1H Late - Max5 + Speed Filter)
# =============================================================================
@dataclass
class TradeSignal:
    side: str
    reason: str
    contracts: int
    price: float


class CrossingLateStrategy:
    """
    1시간봉 후반 Crossing 전략 (Max5 + 속도필터)
    - 진입 구간: 3360~3590초 (첫 진입 x, 이후 2x)
    - 헤지 구간: 3590~3600초 (x로 헤지)
    - Max5: 4번 진입 후 5번째는 x로 헤지
    - 속도 필터: 20초 내 3회 crossing 시 x로 헤지
    """

    name = "crossing_1h_late"
    description = "1시간봉 후반(56분~) crossing (Max5 + 속도필터)"

    def __init__(self):
        self.candle_state: Dict[str, Dict[str, Any]] = {}

    def _get_candle_state(self, candle_key: str) -> Dict[str, Any]:
        if candle_key not in self.candle_state:
            self.candle_state[candle_key] = {
                "count": 0,
                "last_trade_direction": None,
                "hedged": False,
                "crossing_times": [],  # [(elapsed_seconds, direction), ...]
            }
        return self.candle_state[candle_key]

    def _count_recent_crossings(self, crossing_times: list, current_elapsed: int) -> int:
        """최근 SPEED_FILTER_WINDOW_SECONDS 내 crossing 횟수"""
        cutoff = current_elapsed - SPEED_FILTER_WINDOW_SECONDS
        return sum(1 for t, d in crossing_times if t >= cutoff)

    def should_enter_on_crossing(
        self,
        elapsed_seconds: int,
        crossing_direction: str,
        candle_key: str,
        orderbook: Optional[Dict],
    ) -> Optional[TradeSignal]:
        """
        Crossing 이벤트 처리 (Max5 + 속도필터)
        """
        state = self._get_candle_state(candle_key)

        # 56분(3360초) 이전 → 무시
        if elapsed_seconds < CROSSING_MIN_ELAPSED_SECONDS:
            return None

        # 60분(3600초) 이후 → 무시 (금지 구간)
        if elapsed_seconds >= CROSSING_HEDGE_CUTOFF_SECONDS:
            return None

        # 이미 헤지 완료 → 무시
        if state["hedged"]:
            return None

        # 오더북 확인
        if not orderbook:
            return None

        # 진입 가격 (taker = best_ask)
        entry_price = orderbook.get("best_ask")
        if entry_price is None:
            return None

        # 가격 범위 체크
        if entry_price < CROSSING_MIN_ENTRY_PRICE or entry_price > CROSSING_MAX_ENTRY_PRICE:
            return None

        # crossing_times 기록 (속도 필터용)
        state["crossing_times"].append((elapsed_seconds, crossing_direction))

        # === 헤지 조건 체크 ===
        is_hedge = False
        hedge_reason = ""

        # 59분50초~60분 → 헤지 구간
        if elapsed_seconds >= CROSSING_ENTRY_CUTOFF_SECONDS:
            is_hedge = True
            hedge_reason = "HEDGE_TIME"

        # 4번 진입 후 5번째 → Max5 헤지
        elif state["count"] >= CROSSING_MAX_COUNT - 1:
            is_hedge = True
            hedge_reason = "HEDGE_MAX5"

        # 20초 내 3회 이상 → 속도 필터 헤지
        elif self._count_recent_crossings(state["crossing_times"], elapsed_seconds) >= SPEED_FILTER_MAX_CROSSINGS:
            is_hedge = True
            hedge_reason = "HEDGE_SPEED"

        # 횟수 증가
        state["count"] += 1
        state["last_trade_direction"] = crossing_direction

        side = "up" if crossing_direction == "up" else "down"

        # 수량 결정
        if is_hedge:
            contracts = CROSSING_BET_CONTRACT_UNIT  # 헤지: x
            state["hedged"] = True  # 이후 진입 방지
            reason = f"{hedge_reason}_{crossing_direction.upper()} #{state['count']} @{elapsed_seconds}s"
        elif state["count"] == 1:
            contracts = CROSSING_BET_CONTRACT_UNIT  # 첫 진입: x
            reason = f"CROSS1H_LATE_{crossing_direction.upper()} #{state['count']} @{elapsed_seconds}s"
        else:
            contracts = CROSSING_BET_CONTRACT_UNIT * 2  # 이후: 2x
            reason = f"CROSS1H_LATE_{crossing_direction.upper()} #{state['count']} @{elapsed_seconds}s"

        return TradeSignal(
            side=side,
            reason=reason,
            contracts=contracts,
            price=entry_price,
        )

    def get_status(self) -> Dict[str, Any]:
        active = {k: v["count"] for k, v in self.candle_state.items() if v["count"] > 0}
        return {"active_candles": active}


# =============================================================================
# Paper Trader Service
# =============================================================================
class PaperTrader1HLateService(AsyncServiceBase):
    """Paper Trader: 1시간봉 후반(56분~) Crossing Strategy"""

    def __init__(self):
        super().__init__(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, REDIS_HOST, REDIS_PORT)

        self.ob_cache: Dict[str, Dict] = {}

        self.strategies: Dict[str, CrossingLateStrategy] = {}
        for coin in COINS:
            self.strategies[coin] = CrossingLateStrategy()

        self.stats = defaultdict(int)
        self.start_time = time.time()

        self.last_ob_message_time: float = 0.0
        self.p1_disconnect_warned: bool = False

    def is_healthy(self) -> bool:
        return (self.db_pool is not None and self.redis_client is not None)

    # =========================================================================
    # Redis Pub/Sub Subscriber
    # =========================================================================
    async def redis_subscriber(self):
        while True:
            try:
                pubsub = self.redis_client.pubsub()
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

        if tf not in TIMEFRAMES:
            return

        cache_key = f"{coin}_{tf}_{side}"

        self.ob_cache[cache_key] = {
            "best_bid": data["best_bid"],
            "best_ask": data["best_ask"],
            "best_bid_size": data.get("best_bid_size", 0),
            "best_ask_size": data.get("best_ask_size", 0),
            "mid_price": data["mid_price"],
            "market_slug": data["slug"],
            "token_id": data["token_id"],
            "candle_start_ts": data.get("candle_start_ts"),
            "candle_end_ts": data.get("candle_end_ts"),
            "timestamp": data["timestamp"],
        }

        self.stats["ob_messages"] += 1
        self.last_ob_message_time = time.time()
        if self.p1_disconnect_warned:
            logger.info("[P1] Connection restored")
            self.p1_disconnect_warned = False

    async def _handle_candle_boundary(self, data: Dict):
        """캔들 경계 → 전략 리셋"""
        tf = data.get("timeframe", "?")
        if tf not in TIMEFRAMES:
            return
        logger.info(f"[CANDLE_BOUNDARY] Resetting strategies (tf={tf})")
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

        if tf not in TIMEFRAMES:
            return

        if coin not in COINS:
            return

        if coin not in self.strategies:
            return

        self.stats["crossing_events"] += 1

        try:
            candle_start = datetime.fromisoformat(candle_start_str.replace('Z', '+00:00'))
            candle_end = datetime.fromisoformat(candle_end_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
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
            # 56분 이전이면 로그 생략 (정상)
            if elapsed_seconds >= CROSSING_MIN_ELAPSED_SECONDS:
                logger.warning(f"[{coin}] CROSSING but no {side.upper()} orderbook cache @{elapsed_seconds}s")
            return

        expected_slug = generate_slug(coin, tf)
        ob_slug = orderbook.get('market_slug', '')
        if ob_slug != expected_slug:
            logger.warning(f"[{coin}] SLUG_MISMATCH expected={expected_slug} got={ob_slug}")
            return

        candle_key = f"{coin}_{tf}_{candle_start.isoformat()}"

        strategy = self.strategies[coin]
        signal = strategy.should_enter_on_crossing(
            elapsed_seconds=elapsed_seconds,
            crossing_direction=direction,
            candle_key=candle_key,
            orderbook=orderbook,
        )

        if signal:
            crossing_info = {
                "direction": direction,
                "candle_open": data.get("candle_open"),
                "prev_price": data.get("prev_price"),
                "current_price": data.get("current_price"),
                "elapsed_seconds": elapsed_seconds,
                "elapsed_ms": elapsed_ms,
            }

            asyncio.create_task(self.create_order(
                coin, tf, signal,
                orderbook,
                candle_start, candle_end,
                crossing_info=crossing_info,
            ))

    def get_orderbook(self, coin: str, timeframe: str, side: str = "up") -> Optional[Dict]:
        key = f"{coin}_{timeframe}_{side}"
        cached = self.ob_cache.get(key)
        if not cached:
            return None
        if time.time() - cached.get("timestamp", 0) > OB_CACHE_TTL:
            return None
        return cached

    # =========================================================================
    # Paper Order Execution (Taker)
    # =========================================================================
    async def create_order(
        self,
        coin: str,
        timeframe: str,
        signal: TradeSignal,
        orderbook: Dict,
        candle_start: datetime,
        candle_end: datetime,
        crossing_info: Optional[Dict] = None,
    ):
        try:
            side = signal.side.upper()
            order_price = signal.price  # best_ask (taker)
            contracts = signal.contracts
            reason = signal.reason
            cost = contracts * order_price

            market_slug = orderbook.get('market_slug', f'{coin}-{timeframe}')
            token_id = orderbook.get('token_id', '')

            up_orderbook = self.get_orderbook(coin, timeframe, "up")
            down_orderbook = self.get_orderbook(coin, timeframe, "down")

            up_mid_price = up_orderbook.get('mid_price') if up_orderbook else None
            down_mid_price = down_orderbook.get('mid_price') if down_orderbook else None
            mid_price = up_mid_price or 0.5

            snapshot_data = {
                'up': up_orderbook,
                'down': down_orderbook,
                'crossing': crossing_info,
            }

            async with self.db_pool.acquire() as conn:
                trade_id = await conn.fetchval("""
                    INSERT INTO test_paper_trades (
                        time, strategy_name, coin, timeframe,
                        mid_price, up_mid_price, down_mid_price,
                        side, order_price, reason, orderbook_snapshot,
                        market_slug, token_id, candle_start_time, candle_end_time,
                        contracts, cost, status
                    ) VALUES (
                        NOW(), $1, $2, $3,
                        $4, $5, $6,
                        $7, $8, $9, $10,
                        $11, $12, $13, $14,
                        $15, $16, 'PENDING'
                    )
                    RETURNING id
                """,
                    STRATEGY_NAME, coin, timeframe,
                    mid_price, up_mid_price, down_mid_price,
                    side, order_price, reason, json.dumps(snapshot_data),
                    market_slug, token_id, candle_start, candle_end,
                    contracts, cost,
                )

                # Taker 즉시 체결
                await conn.execute("""
                    UPDATE test_paper_trades
                    SET status = 'FILLED', fill_time = NOW(), fill_price = order_price,
                        filled_contracts = $1, filled_cost = $2
                    WHERE id = $3
                """, contracts, cost, trade_id)

                if crossing_info:
                    candle_open = crossing_info.get('candle_open', 0)
                    prev_price = crossing_info.get('prev_price', 0)
                    curr_price = crossing_info.get('current_price', 0)
                    elapsed_seconds = crossing_info.get('elapsed_seconds', 0)
                    elapsed_min = elapsed_seconds // 60
                    elapsed_sec = elapsed_seconds % 60
                    logger.info(
                        f"[{coin}] ORDER: {side} x{contracts} @ {order_price:.3f} | "
                        f"open={candle_open:.2f} prev={prev_price:.2f} curr={curr_price:.2f} | "
                        f"{reason} ({elapsed_min}:{elapsed_sec:02d}) | id={trade_id}"
                    )
                else:
                    logger.info(f"[{coin}] ORDER: {side} x{contracts} @ {order_price:.3f} | {reason} | id={trade_id}")

            self.stats["trades_created"] += 1
            return trade_id
        except Exception as e:
            logger.error(f"[{coin}] create_order error: {e}")
            return None

    # =========================================================================
    # Settlement
    # =========================================================================
    async def settle_trades(self, coin: str, timeframe: str,
                            candle_start: datetime, candle_end: datetime) -> int:
        market_result = await self.get_price_result(coin, timeframe, candle_start, candle_end)
        if not market_result:
            return 0

        settled_count = 0
        try:
            async with self.db_pool.acquire() as conn:
                trades = await conn.fetch("""
                    SELECT id, side, fill_price, contracts, cost,
                           filled_contracts, filled_cost
                    FROM test_paper_trades
                    WHERE coin = $1 AND timeframe = $2 AND strategy_name = $3
                      AND status = 'FILLED'
                      AND candle_start_time = $4
                    ORDER BY time ASC
                """, coin, timeframe, STRATEGY_NAME, candle_start)

                if not trades:
                    return 0

                total_pnl = 0.0

                for trade in trades:
                    trade_id = trade['id']
                    side = trade['side']
                    fill_price = float(trade['fill_price']) if trade['fill_price'] is not None else 0.0
                    filled_contracts = float(trade['filled_contracts']) if trade['filled_contracts'] is not None else 0.0
                    filled_cost = float(trade['filled_cost']) if trade['filled_cost'] is not None else 0.0

                    if filled_contracts == 0 and trade['contracts']:
                        filled_contracts = float(trade['contracts'])
                        filled_cost = float(trade['cost']) if trade['cost'] else 0.0

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

                if settled_count > 0:
                    logger.info(f"[{coin}] SETTLEMENT COMPLETE: {settled_count} trades, total PnL: ${total_pnl:.2f}")

            return settled_count
        except Exception as e:
            logger.error(f"[{coin}] settle_trades error: {e}")
            return 0

    async def get_price_result(self, coin: str, timeframe: str,
                               candle_start: datetime, candle_end: datetime) -> Optional[str]:
        ALLOWED_COLUMNS = {'binance_price', 'chainlink_price'}
        # 1시간봉은 binance_price 사용 (STRATEGY.md 참고)
        price_column = 'binance_price'
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
                f"ob_msgs={self.stats.get('ob_messages', 0)} | "
                f"crossing={self.stats.get('crossing_events', 0)} | "
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
                self.p1_disconnect_warned = True

    # =========================================================================
    # Main
    # =========================================================================
    async def run(self):
        logger.info("=" * 60)
        logger.info("Paper Trader 1H Late: 1시간봉 후반(56분~) Crossing 전략")
        logger.info("=" * 60)
        logger.info(f"Strategy: {STRATEGY_NAME}")
        logger.info(f"  Entry: {CROSSING_MIN_ELAPSED_SECONDS}s(56분) ~ {CROSSING_ENTRY_CUTOFF_SECONDS}s(59분50초)")
        logger.info(f"  Hedge: {CROSSING_ENTRY_CUTOFF_SECONDS}s ~ {CROSSING_HEDGE_CUTOFF_SECONDS}s")
        logger.info(f"  #1: x{CROSSING_BET_CONTRACT_UNIT}, #2~4: x{CROSSING_BET_CONTRACT_UNIT*2}, #5: x{CROSSING_BET_CONTRACT_UNIT}")
        logger.info(f"  Speed filter: {SPEED_FILTER_MAX_CROSSINGS} crossings in {SPEED_FILTER_WINDOW_SECONDS}s → hedge")
        logger.info(f"  Max count: {CROSSING_MAX_COUNT}")
        logger.info(f"Coins: {COINS} | Timeframes: {TIMEFRAMES}")
        logger.info("=" * 60)

        if not await self.connect_db():
            logger.error("Failed to connect to DB")
            return

        if not await self.connect_redis():
            logger.error("Failed to connect to Redis")
            return

        self.setup_signal_handlers()

        await asyncio.gather(
            self.redis_subscriber(),
            self.settlement_loop(),
            self.stats_loop(),
            self.p1_watchdog(),
            self._health_file_loop(),
        )


def main():
    service = PaperTrader1HLateService()
    asyncio.run(service.run())


if __name__ == "__main__":
    main()
