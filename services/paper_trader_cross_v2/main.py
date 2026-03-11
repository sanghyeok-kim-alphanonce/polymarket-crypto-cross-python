"""
P4-Cross-V2: Paper Trader Crossing V2 Strategy Service

15분봉 시작가 crossing 전략 (무제한 버전).
- Redis subscribe: ch:crossing:*, ch:orderbook:*
- Crossing 이벤트 수신 시 즉시 taker 주문 실행
- Settlement: 60초 주기 폴링
- V2: 횟수 제한 없음, 14분까지 베팅

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
    CROSSING_FIRST_BET, CROSSING_SUBSEQUENT_BET,
    CROSSING_CUTOFF_SECONDS,
    CROSSING_MIN_ENTRY_PRICE, CROSSING_MAX_ENTRY_PRICE,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    REDIS_HOST, REDIS_PORT,
    STATS_INTERVAL, OB_CACHE_TTL,
)

from strategies import CrossingV2Strategy, CrossingV2Config

# Packages
from polymarket_common import generate_slug
from service_common import AsyncServiceBase, setup_logging

logger = setup_logging(__name__)


class CrossingV2TraderService(AsyncServiceBase):
    """P4-Cross-V2: Crossing V2 Strategy Paper Trader Service"""

    def __init__(self):
        super().__init__(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, REDIS_HOST, REDIS_PORT)

        # Orderbook cache (pub/sub에서 채움)
        # key: "{coin}_{tf}_{side}", value: dict with best_bid/best_ask/mid_price/slug/token_id
        self.ob_cache: Dict[str, Dict] = {}

        # Crossing V2 Strategy 인스턴스 (코인별)
        self.strategies: Dict[str, CrossingV2Strategy] = {}
        for coin in COINS:
            config = CrossingV2Config(
                first_bet_amount=CROSSING_FIRST_BET,
                subsequent_bet_amount=CROSSING_SUBSEQUENT_BET,
                cutoff_seconds=CROSSING_CUTOFF_SECONDS,
                min_entry_price=CROSSING_MIN_ENTRY_PRICE,
                max_entry_price=CROSSING_MAX_ENTRY_PRICE,
            )
            self.strategies[coin] = CrossingV2Strategy(config)

        # 통계
        self.stats = defaultdict(int)
        self.start_time = time.time()

        # P1 연결 감시
        self.last_ob_message_time: float = 0.0
        self.p1_disconnect_warned: bool = False

    def is_healthy(self) -> bool:
        return (self.db_pool is not None and self.redis_client is not None)

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
        """오더북 메시지 수신 → 캐시 업데이트"""
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

    async def _handle_candle_boundary(self, data: Dict):
        """캔들 경계 → 전략 리셋 + 캐시 클리어"""
        tf = data.get("timeframe", "?")
        logger.info(f"[CANDLE_BOUNDARY] Resetting strategies (tf={tf})")
        self.ob_cache.clear()
        for strategy in self.strategies.values():
            strategy.candle_state.clear()

    async def _handle_crossing_event(self, data: Dict):
        """
        Crossing 이벤트 수신 → 즉시 주문 실행

        data: {
            "coin": "btc",
            "timeframe": "15m",
            "direction": "up" | "down",
            "prev_price": float,
            "current_price": float,
            "candle_open": float,
            "candle_start": "2024-01-01T00:00:00+00:00",
            "candle_end": "2024-01-01T00:15:00+00:00",
            "timestamp": "2024-01-01T00:05:30+00:00",
        }
        """
        coin = data.get("coin")
        tf = data.get("timeframe")
        direction = data.get("direction")  # "up" or "down"
        candle_start_str = data.get("candle_start")
        candle_end_str = data.get("candle_end")

        if not all([coin, tf, direction, candle_start_str, candle_end_str]):
            logger.warning(f"[CROSSING] Invalid crossing data: {data}")
            return

        if coin not in self.strategies:
            return

        self.stats["crossing_events"] += 1

        # 경과 시간 계산 (초)
        try:
            candle_start = datetime.fromisoformat(candle_start_str.replace('Z', '+00:00'))
            candle_end = datetime.fromisoformat(candle_end_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            elapsed_seconds = int((now - candle_start).total_seconds())
        except Exception as e:
            logger.error(f"[CROSSING] Time parse error: {e}")
            return

        # 캔들 종료 후면 skip
        if now >= candle_end:
            return

        # 오더북 가져오기
        up_orderbook = self.get_orderbook(coin, tf, "up")
        down_orderbook = self.get_orderbook(coin, tf, "down")

        if not up_orderbook:
            logger.warning(f"[{coin}] CROSSING but no UP orderbook available")
            return

        # Stale orderbook guard
        expected_slug = generate_slug(coin, tf)
        ob_slug = up_orderbook.get('market_slug', '')
        if ob_slug != expected_slug:
            logger.warning(f"[{coin}] SLUG_MISMATCH expected={expected_slug} got={ob_slug}")
            return

        # 캔들 키 생성
        candle_key = f"{coin}_{tf}_{candle_start.isoformat()}"

        # 전략에서 진입 여부 판단
        strategy = self.strategies[coin]
        signal = strategy.should_enter_on_crossing(
            coin=coin,
            timeframe=tf,
            elapsed_seconds=elapsed_seconds,
            crossing_direction=direction,
            up_orderbook=up_orderbook,
            down_orderbook=down_orderbook,
            candle_key=candle_key,
        )

        if signal:
            # Crossing 정보 추출
            crossing_info = {
                "direction": direction,
                "candle_open": data.get("candle_open"),
                "prev_price": data.get("prev_price"),
                "current_price": data.get("current_price"),
                "elapsed_seconds": elapsed_seconds,
            }
            # Fire-and-forget: 동시 주문 허용 (AGG 신호 대응)
            asyncio.create_task(self.create_order(
                coin, tf, signal,
                up_orderbook, down_orderbook,
                candle_start, candle_end,
                crossing_info=crossing_info,
            ))

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
                           up_orderbook: Dict, down_orderbook: Optional[Dict],
                           candle_start: datetime, candle_end: datetime,
                           crossing_info: Optional[Dict] = None):
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

            # Snapshot에 crossing 정보 포함
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

                # 상세 로그 출력
                if crossing_info:
                    candle_open = crossing_info.get('candle_open', 0)
                    prev_price = crossing_info.get('prev_price', 0)
                    curr_price = crossing_info.get('current_price', 0)
                    logger.info(
                        f"[{coin}] ORDER: {side} @ {order_price:.4f} | "
                        f"open={candle_open:.2f} prev={prev_price:.2f} curr={curr_price:.2f} | "
                        f"{reason} | id={trade_id}"
                    )
                else:
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
                f"crossing_events={self.stats.get('crossing_events', 0)} | "
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
        logger.info("P4-Cross-V2: Crossing V2 Strategy Paper Trader Starting")
        logger.info("=" * 60)
        logger.info(f"Strategy: {STRATEGY_NAME}")
        logger.info(f"  {self.strategies[COINS[0]].description}")
        logger.info(f"  First bet: ${CROSSING_FIRST_BET}, Subsequent: ${CROSSING_SUBSEQUENT_BET}")
        logger.info(f"  NO LIMIT on crossings, Cutoff: {CROSSING_CUTOFF_SECONDS}s (14:00)")
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
    service = CrossingV2TraderService()
    asyncio.run(service.run())


if __name__ == "__main__":
    main()
