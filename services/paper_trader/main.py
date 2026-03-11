"""
P4: Paper Trader Service

Redis pub/sub으로 P1(rtds)에서 오더북 수신 → 전략 실행 + 정산.
- Redis subscribe: ch:orderbook:*, ch:candle_boundary
- Event-driven: 오더북 메시지 수신 즉시 _check_entry() 호출
- Settlement: 60초 주기 폴링

단일 쓰레드 asyncio 구조
"""
import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple, Any

from config import (
    STRATEGY_NAME, COINS, TIMEFRAMES,
    V12_5_MIN_ENTRY_PRICE, V12_5_MIN_ENTRY_PRICE_LATE,
    V12_5_EARLY_CUTOFF_MINUTES, V12_5_COOLDOWN_SECONDS, V12_5_MAX_SPREAD,
    V12_5_THRESHOLDS_EARLY, V12_5_THRESHOLDS_LATE,
    V12_6_MIN_ENTRY_PRICE, V12_6_MIN_ENTRY_PRICE_LATE,
    V12_6_EARLY_CUTOFF_MINUTES, V12_6_COOLDOWN_SECONDS, V12_6_MAX_SPREAD,
    V12_6_REQUIRE_MOMENTUM_MATCH,
    V12_6_THRESHOLDS_EARLY, V12_6_THRESHOLDS_LATE,
    BET_AMOUNT, MIN_LIQUIDITY,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    REDIS_HOST, REDIS_PORT,
    STATS_INTERVAL, OB_CACHE_TTL,
)

from strategies import get_strategy, V12_5Config, V12_6Config

# Packages
from polymarket_common import generate_slug
from service_common import AsyncServiceBase, setup_logging

logger = setup_logging(__name__)


class PaperTraderService(AsyncServiceBase):
    """P4: Paper Trader Service — Redis pub/sub 기반 전략 실행"""

    def __init__(self):
        super().__init__(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, REDIS_HOST, REDIS_PORT)

        # Orderbook cache (pub/sub에서 채움)
        # key: "{coin}_{tf}_{side}", value: dict with best_bid/best_ask/mid_price/slug/token_id
        self.ob_cache: Dict[str, Dict] = {}

        # Strategy (코인별 인스턴스)
        self.strategies: Dict[str, Any] = {}
        for coin in COINS:
            self.strategies[coin] = self._create_strategy()

        # 통계
        self.stats = defaultdict(int)
        self.start_time = time.time()

        # P1 연결 감시
        self.last_ob_message_time: float = 0.0
        self.p1_disconnect_warned: bool = False

    def _create_strategy(self):
        if STRATEGY_NAME == 'v12_6':
            return get_strategy(
                STRATEGY_NAME,
                min_entry_price=V12_6_MIN_ENTRY_PRICE,
                min_entry_price_late=V12_6_MIN_ENTRY_PRICE_LATE,
                early_cutoff_minutes=V12_6_EARLY_CUTOFF_MINUTES,
                cooldown_seconds=V12_6_COOLDOWN_SECONDS,
                max_spread=V12_6_MAX_SPREAD,
                require_momentum_match=V12_6_REQUIRE_MOMENTUM_MATCH,
                thresholds_early=V12_6_THRESHOLDS_EARLY,
                thresholds_late=V12_6_THRESHOLDS_LATE,
                bet_amount=BET_AMOUNT,
                min_liquidity=MIN_LIQUIDITY,
            )
        else:
            # v12_5 (default)
            return get_strategy(
                STRATEGY_NAME,
                min_entry_price=V12_5_MIN_ENTRY_PRICE,
                min_entry_price_late=V12_5_MIN_ENTRY_PRICE_LATE,
                early_cutoff_minutes=V12_5_EARLY_CUTOFF_MINUTES,
                cooldown_seconds=V12_5_COOLDOWN_SECONDS,
                max_spread=V12_5_MAX_SPREAD,
                thresholds_early=V12_5_THRESHOLDS_EARLY,
                thresholds_late=V12_5_THRESHOLDS_LATE,
                bet_amount=BET_AMOUNT,
                min_liquidity=MIN_LIQUIDITY,
            )

    def is_healthy(self) -> bool:
        return (self.db_pool is not None and self.redis_client is not None)

    # =========================================================================
    # Redis Pub/Sub Subscriber
    # =========================================================================
    async def redis_subscriber(self):
        """ch:orderbook:*, ch:candle:*, ch:candle_boundary 구독 → event-driven 진입 체크"""
        while True:
            try:
                pubsub = self.redis_client.pubsub()
                # ch:candle:* 추가: binance 가격 변화 시에도 entry 체크
                # TIMEFRAMES에 맞는 candle_boundary만 구독
                boundary_channels = [f"ch:candle_boundary:{tf}" for tf in TIMEFRAMES]
                await pubsub.psubscribe("ch:orderbook:*", "ch:candle:*", *boundary_channels)
                logger.info(f"[PUBSUB] Subscribed to ch:orderbook:*, ch:candle:*, ch:candle_boundary:{TIMEFRAMES}")

                async for message in pubsub.listen():
                    if message["type"] not in ("message", "pmessage"):
                        continue

                    try:
                        data = json.loads(message["data"])
                        channel = message.get("channel", "")

                        if "ch:candle_boundary" in channel:
                            await self._handle_candle_boundary(data)
                        elif "ch:orderbook:" in channel:
                            await self._handle_orderbook_message(data)
                        elif "ch:candle:" in channel:
                            await self._handle_binance_candle(data)

                    except json.JSONDecodeError:
                        pass
                    except Exception as e:
                        logger.error(f"[PUBSUB] message error: {e}")
                        self.stats["pubsub_errors"] += 1

            except Exception as e:
                logger.error(f"[PUBSUB] subscriber error: {e}")
                await asyncio.sleep(3)

    async def _handle_orderbook_message(self, data: Dict):
        """오더북 메시지 수신 → 캐시 업데이트 → 진입 체크"""
        coin = data.get("coin")
        tf = data.get("timeframe")
        side = data.get("side")

        if not coin or not tf or not side:
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
            logger.info("[P1] Connection restored — receiving orderbook messages again")
            self.p1_disconnect_warned = False

        # Event-driven entry check
        await self._check_entry(coin, tf)

    async def _handle_candle_boundary(self, data: Dict):
        """캔들 경계 → 전략 리셋 + 캐시 클리어"""
        tf = data.get("timeframe", "?")
        logger.info(f"[CANDLE_BOUNDARY] Resetting strategies (tf={tf})")
        self.ob_cache.clear()

    async def _handle_binance_candle(self, data: Dict):
        """Binance 가격 업데이트 → entry 체크 (delta 포함)

        ch:candle:{coin}_{tf} 메시지 수신 시 호출.
        delta_1s가 포함되어 있어 가격 변화 즉시 entry 체크 가능.
        """
        coin = data.get("coin")
        tf = data.get("timeframe")
        delta_1s = data.get("delta_1s")
        candle_open = data.get("open")  # 캔들 시작가 (v12_6 momentum match용)
        current_price = data.get("close")  # 현재가 (v12_6 momentum match용)

        if not coin or not tf:
            return

        # delta_1s가 None이거나 0이면 skip (체크할 필요 없음)
        if delta_1s is None or delta_1s == 0:
            return

        self.stats["candle_messages"] = self.stats.get("candle_messages", 0) + 1

        # Entry 체크 - delta_1s, candle_open, current_price 전달
        await self._check_entry(
            coin, tf,
            delta_1s=delta_1s,
            candle_open=candle_open,
            current_price=current_price,
        )

    # =========================================================================
    # Orderbook 조회 (ob_cache 기반)
    # =========================================================================
    def get_orderbook(self, coin: str, timeframe: str, side: str = "up") -> Optional[Dict]:
        key = f"{coin}_{timeframe}_{side}"
        cached = self.ob_cache.get(key)
        if not cached:
            return None
        # TTL guard: stale 데이터 방지
        if time.time() - cached.get("timestamp", 0) > OB_CACHE_TTL:
            return None
        return cached

    # =========================================================================
    # Binance delta_1s 조회 (Redis에서)
    # =========================================================================
    async def get_binance_delta(self, coin: str, timeframe: str = "15m") -> Optional[float]:
        """Redis에서 binance_ws가 계산한 1초 delta 조회"""
        if not self.redis_client:
            return None

        try:
            # binance_delta 전용 키 사용 (rtds와 충돌 방지)
            key = f"binance_delta:{coin}_{timeframe}"
            data_str = await self.redis_client.get(key)
            if not data_str:
                return None

            data = json.loads(data_str)
            delta_1s = data.get('delta_1s')
            if delta_1s is not None:
                return float(delta_1s)
            return None
        except Exception as e:
            logger.error(f"[{coin}] get_binance_delta error: {e}")
            return None

    # =========================================================================
    # Event-driven 진입 체크
    # =========================================================================
    async def _check_entry(self, coin: str, timeframe: str, delta_1s: Optional[float] = None,
                           candle_open: Optional[float] = None, current_price: Optional[float] = None):
        try:
            candle_start, candle_end, elapsed_min = self.get_candle_times(timeframe)
            now = datetime.now(timezone.utc)

            if now >= candle_end:
                return

            up_orderbook = self.get_orderbook(coin, timeframe, "up")
            down_orderbook = self.get_orderbook(coin, timeframe, "down")

            if not up_orderbook:
                # DEBUG: orderbook이 없을 때 로그
                if delta_1s is not None and abs(delta_1s) >= 1:
                    logger.warning(f"[{coin}] NO_ORDERBOOK delta=${delta_1s:.2f}")
                return

            # Stale orderbook guard
            expected_slug = generate_slug(coin, timeframe)
            ob_slug = up_orderbook.get('market_slug', '')
            if ob_slug != expected_slug:
                if delta_1s is not None and abs(delta_1s) >= 1:
                    logger.warning(f"[{coin}] SLUG_MISMATCH expected={expected_slug} got={ob_slug} delta=${delta_1s:.2f}")
                return

            # Binance 1초 delta 조회 (ch:candle에서 전달받거나 Redis에서 조회)
            if delta_1s is None:
                delta_1s = await self.get_binance_delta(coin, timeframe)

            # DEBUG: 높은 delta 로그
            if delta_1s is not None and abs(delta_1s) >= 1:
                is_late = elapsed_min > 10
                entry_price = up_orderbook.get('best_ask', 0) if delta_1s > 0 else (down_orderbook.get('best_ask', 0) if down_orderbook else 0)
                logger.info(f"[{coin}] HIGH_DELTA delta=${delta_1s:.2f} elapsed={elapsed_min}m late={is_late} entry={entry_price:.3f}")

            candle_key = f"{coin}_{timeframe}_{candle_start.isoformat()}"
            signal = self.strategies[coin].should_enter(
                coin=coin,
                timeframe=timeframe,
                elapsed_min=elapsed_min,
                up_orderbook=up_orderbook,
                down_orderbook=down_orderbook,
                candle_key=candle_key,
                delta_1s=delta_1s,  # binance_ws가 계산한 1초 delta
                candle_open=candle_open,  # v12_6 momentum match용
                current_price=current_price,  # v12_6 momentum match용
            )

            if signal:
                await self.create_order(
                    coin, timeframe, signal,
                    up_orderbook, down_orderbook,
                    candle_start, candle_end,
                )

        except Exception as e:
            logger.error(f"[{coin}] _check_entry error: {e}")

    def get_candle_times(self, timeframe: str) -> Tuple[datetime, datetime, int]:
        now = datetime.now(timezone.utc)

        if timeframe == '15m':
            minutes = 15
        elif timeframe == '1h':
            minutes = 60
        else:
            minutes = 15

        total_minutes = now.hour * 60 + now.minute
        candle_index = total_minutes // minutes
        candle_start_minute = candle_index * minutes

        candle_start = now.replace(
            hour=candle_start_minute // 60,
            minute=candle_start_minute % 60,
            second=0, microsecond=0
        )
        candle_end = candle_start + timedelta(minutes=minutes)
        elapsed_min = int((now - candle_start).total_seconds() // 60)

        return candle_start, candle_end, elapsed_min

    # =========================================================================
    # 주문 생성
    # =========================================================================
    async def create_order(self, coin: str, timeframe: str, signal,
                           up_orderbook: Dict, down_orderbook: Dict,
                           candle_start: datetime, candle_end: datetime):
        try:
            side = signal.side.upper()
            order_price = signal.price
            contracts = signal.contracts
            reason = signal.reason
            cost = contracts * order_price

            orderbook = up_orderbook if side == 'UP' else down_orderbook
            market_slug = orderbook.get('market_slug', f'{coin}-{timeframe}') if orderbook else f'{coin}-{timeframe}'
            token_id = orderbook.get('token_id', '') if orderbook else ''

            # UP/DOWN 각각의 mid_price 저장
            up_mid_price = up_orderbook.get('mid_price') if up_orderbook else None
            down_mid_price = down_orderbook.get('mid_price') if down_orderbook else None
            mid_price = up_mid_price or 0.5  # 호환성: 기존 mid_price 컬럼용

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
                    side, order_price, reason, json.dumps({
                        'up': up_orderbook,
                        'down': down_orderbook,
                    }),
                    market_slug, token_id, candle_start, candle_end,
                    contracts, cost,
                )

                logger.info(f"[{coin}] ORDER: {side} @ {order_price:.4f} | {reason} | id={trade_id}")

                await conn.execute("""
                    UPDATE test_paper_trades
                    SET status = 'FILLED', fill_time = NOW(), fill_price = order_price
                    WHERE id = $1
                """, trade_id)

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
                    SELECT id, side, fill_price, contracts, cost
                    FROM test_paper_trades
                    WHERE coin = $1 AND timeframe = $2 AND strategy_name = $3
                      AND status = 'FILLED'
                      AND candle_start_time = $4
                """, coin, timeframe, STRATEGY_NAME, candle_start)

                if not trades:
                    return 0

                for trade in trades:
                    trade_id = trade['id']
                    side = trade['side']
                    fill_price = float(trade['fill_price']) if trade['fill_price'] is not None else 0.0
                    contracts_val = float(trade['contracts']) if trade['contracts'] is not None else 0.0
                    cost_val = float(trade['cost']) if trade['cost'] is not None else 0.0

                    if market_result == 'FLAT':
                        exit_price = fill_price
                        pnl = 0.0
                        outcome = 'FLAT'
                    elif side == market_result:
                        exit_price = 1.0
                        pnl = contracts_val * exit_price - cost_val
                        outcome = 'WIN'
                    else:
                        exit_price = 0.0
                        pnl = -cost_val
                        outcome = 'LOSS'

                    await conn.execute("""
                        UPDATE test_paper_trades
                        SET status = 'CLOSED', closed_at = NOW(),
                            exit_price = $1, pnl = $2, outcome = $3, market_result = $4
                        WHERE id = $5
                    """, exit_price, pnl, outcome, market_result, trade_id)

                    logger.info(f"[{coin}] SETTLED: {side} → {outcome} ({market_result}) | PnL: ${pnl:.4f}")
                    self.stats["trades_settled"] += 1
                    settled_count += 1

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
                    logger.debug(f"[{coin}] Waiting for price data after {candle_end}")
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
                    logger.debug(f"[{coin}] Missing start/end price for settlement")
                    return None

                start_price = float(start_row['price'])
                end_price = float(end_row['price'])

                if end_price > start_price:
                    result = 'UP'
                elif end_price < start_price:
                    result = 'DOWN'
                else:
                    result = 'FLAT'

                logger.info(f"[{coin}] Price result: {start_price:.2f} → {end_price:.2f} = {result} ({price_column})")
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
                    logger.info(f"[SETTLEMENT] Found {len(pending)} pending candles to settle")

            for row in pending:
                settled = await self.settle_trades(row['coin'], row['timeframe'],
                                                   row['candle_start_time'], row['candle_end_time'])
                if settled > 0:
                    logger.info(f"[SETTLEMENT] {row['coin']}/{row['timeframe']}: settled {settled} trades")

        except Exception as e:
            logger.error(f"[SETTLEMENT] _settle_pending_trades error: {e}")

    # =========================================================================
    # Stats
    # =========================================================================
    async def stats_loop(self):
        while True:
            await asyncio.sleep(STATS_INTERVAL)
            uptime = int(time.time() - self.start_time)
            strategy_status = {c: s.get_status() for c, s in self.strategies.items()}
            logger.info(
                f"[STATS] uptime={uptime}s | strategy={STRATEGY_NAME} | "
                f"ob_msgs={self.stats.get('ob_messages', 0)} | "
                f"candle_msgs={self.stats.get('candle_messages', 0)} | "
                f"trades={self.stats['trades_created']}/{self.stats['trades_settled']} | "
                f"errors={self.stats.get('pubsub_errors', 0)} | "
                f"status={strategy_status}"
            )

    # =========================================================================
    # P1 연결 감시
    # =========================================================================
    async def p1_watchdog(self):
        """P1(rtds)에서 오더북 메시지가 끊기면 경고"""
        P1_TIMEOUT = 30  # 30초 동안 메시지 없으면 경고
        while True:
            await asyncio.sleep(10)
            if self.last_ob_message_time == 0:
                continue  # 아직 첫 메시지를 받지 못함
            elapsed = time.time() - self.last_ob_message_time
            if elapsed > P1_TIMEOUT and not self.p1_disconnect_warned:
                logger.warning(f"[P1] No orderbook messages for {elapsed:.0f}s — P1 may be down")
                self.p1_disconnect_warned = True

    # =========================================================================
    # 메인 실행
    # =========================================================================
    async def run(self):
        logger.info("=" * 60)
        logger.info("P4: Paper Trader Service Starting")
        logger.info("=" * 60)
        logger.info(f"Strategy: {STRATEGY_NAME}")
        logger.info(f"  {self.strategies[COINS[0]].description}")
        logger.info(f"Coins: {COINS}")
        logger.info(f"Timeframes: {TIMEFRAMES}")
        logger.info("=" * 60)

        if not await self.connect_db():
            logger.error("Failed to connect to DB")
            return

        if not await self.connect_redis():
            logger.error("Failed to connect to Redis (required for pub/sub)")
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
    service = PaperTraderService()
    asyncio.run(service.run())


if __name__ == "__main__":
    main()
