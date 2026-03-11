"""
Multi Exchange Stream Service

Streams real-time price and orderbook data from multiple exchanges
using ccxt.pro and writes to Redis.
"""
import asyncio
import logging
import signal
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List

from config import (
    EXCHANGES,
    COINS,
    RECONNECT_DELAY_SECONDS,
    MAX_RECONNECT_ATTEMPTS,
    STATS_LOG_INTERVAL,
    LOG_LEVEL,
    ENABLE_TICK_TRADES,
    get_symbol_for_exchange,
    get_orderbook_limit_for_exchange,
)
from exchange_ccxt import CCXTExchange
from redis_writer import RedisWriter
from db_writer import DBWriter
from ticker_candle_tracker import TickerCandleTracker
from trades_tracker import TradesTracker, Trade

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class MultiExchangeStream:
    """
    Main service class for streaming data from multiple exchanges.

    Manages exchange connections, watch loops, and statistics.
    """

    def __init__(self):
        self._exchanges: Dict[str, CCXTExchange] = {}
        self._redis_writer = RedisWriter()
        self._db_writer = DBWriter()
        self._running = False
        self._tasks: List[asyncio.Task] = []

        # Ticker-based candle trackers per exchange
        self._candle_trackers: Dict[str, TickerCandleTracker] = {}

        # Trades trackers per exchange (for tick-level data)
        self._trades_trackers: Dict[str, TradesTracker] = {}

        # Statistics tracking
        self._stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._last_stats_time = datetime.now(timezone.utc)

    async def run(self) -> None:
        """Main entry point - start all streams."""
        logger.info("=" * 60)
        logger.info("Multi Exchange Stream Service Starting")
        logger.info(f"Exchanges: {EXCHANGES}")
        logger.info(f"Coins: {COINS}")
        logger.info(f"Tick-Level Trades: {'ENABLED' if ENABLE_TICK_TRADES else 'DISABLED'}")
        logger.info("=" * 60)

        # Connect to Redis
        if not self._redis_writer.connect():
            logger.error("Failed to connect to Redis - exiting")
            return

        # Connect to DB
        if not self._db_writer.connect():
            logger.warning("Failed to connect to DB - continuing without DB writes")

        self._running = True

        # Initialize exchanges, candle trackers, and trades trackers
        for exchange_id in EXCHANGES:
            exchange = CCXTExchange(exchange_id)
            if await exchange.connect():
                self._exchanges[exchange_id] = exchange
                self._candle_trackers[exchange_id] = TickerCandleTracker(exchange_id)
                if ENABLE_TICK_TRADES:
                    self._trades_trackers[exchange_id] = TradesTracker(exchange_id)
            else:
                logger.warning(f"[{exchange_id.upper()}] Failed to initialize - skipping")

        if not self._exchanges:
            logger.error("No exchanges initialized - exiting")
            return

        # Create watch tasks for each exchange/coin combination
        for exchange_id, exchange in self._exchanges.items():
            for coin in COINS:
                # Get correct symbol for exchange
                symbol = self._get_symbol(exchange_id, coin)
                if symbol is None:
                    continue

                # Create ticker watch task
                task = asyncio.create_task(
                    self._ticker_watch_loop(exchange, coin, symbol),
                    name=f"ticker_{exchange_id}_{coin}",
                )
                self._tasks.append(task)

                # Create orderbook watch task
                task = asyncio.create_task(
                    self._orderbook_watch_loop(exchange, coin, symbol),
                    name=f"orderbook_{exchange_id}_{coin}",
                )
                self._tasks.append(task)

                # OHLCV watch task removed - using ticker-based candles instead

                # Create trades watch task (tick-level data) if enabled
                if ENABLE_TICK_TRADES:
                    task = asyncio.create_task(
                        self._trades_watch_loop(exchange, coin, symbol),
                        name=f"trades_{exchange_id}_{coin}",
                    )
                    self._tasks.append(task)

        # Stats logging task
        stats_task = asyncio.create_task(
            self._stats_loop(),
            name="stats_logger",
        )
        self._tasks.append(stats_task)

        # DB flush task
        db_flush_task = asyncio.create_task(
            self._db_flush_loop(),
            name="db_flush",
        )
        self._tasks.append(db_flush_task)

        logger.info(f"Started {len(self._tasks)} watch tasks")

        # Wait for all tasks
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            logger.info("Tasks cancelled - shutting down")

    def _get_symbol(self, exchange_id: str, coin: str) -> str:
        """Get the correct trading symbol for an exchange and coin."""
        return get_symbol_for_exchange(exchange_id, coin)

    async def _ticker_watch_loop(
        self, exchange: CCXTExchange, coin: str, symbol: str
    ) -> None:
        """
        Continuous ticker watch loop for a single exchange/coin.

        Handles reconnection on errors.
        """
        exchange_id = exchange.exchange_id
        reconnect_count = 0

        while self._running:
            try:
                ticker = await exchange.watch_ticker(symbol, coin)

                if ticker:
                    self._redis_writer.write_ticker(ticker)
                    # Buffer to DB
                    now = datetime.fromtimestamp(ticker.timestamp / 1000, tz=timezone.utc)
                    self._db_writer.buffer_price(
                        exchange=ticker.exchange,
                        coin=ticker.coin,
                        price=ticker.price,
                        bid=ticker.bid,
                        ask=ticker.ask,
                        timestamp=now,
                    )
                    # Update candles from ticker price (real-time)
                    candle_tracker = self._candle_trackers.get(exchange_id)
                    if candle_tracker:
                        candles = candle_tracker.update(ticker.coin, ticker.price, now)
                        for candle in candles:
                            self._redis_writer.write_current_candle(candle)
                    self._stats[exchange_id]["ticker_count"] += 1
                    reconnect_count = 0  # Reset on success
                else:
                    # None returned (error handled in watch_ticker)
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{exchange_id.upper()}] Ticker loop error for {coin}: {e}")
                reconnect_count += 1

                if reconnect_count >= MAX_RECONNECT_ATTEMPTS:
                    logger.error(
                        f"[{exchange_id.upper()}] Max reconnect attempts reached for {coin} ticker"
                    )
                    break

                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _orderbook_watch_loop(
        self, exchange: CCXTExchange, coin: str, symbol: str
    ) -> None:
        """
        Continuous orderbook watch loop for a single exchange/coin.

        Handles reconnection on errors.
        """
        exchange_id = exchange.exchange_id
        reconnect_count = 0

        while self._running:
            try:
                orderbook = await exchange.watch_order_book(
                    symbol, coin, limit=get_orderbook_limit_for_exchange(exchange_id)
                )

                if orderbook:
                    self._redis_writer.write_orderbook(orderbook)
                    self._stats[exchange_id]["orderbook_count"] += 1
                    reconnect_count = 0  # Reset on success
                else:
                    # None returned (error handled in watch_order_book)
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{exchange_id.upper()}] Orderbook loop error for {coin}: {e}")
                reconnect_count += 1

                if reconnect_count >= MAX_RECONNECT_ATTEMPTS:
                    logger.error(
                        f"[{exchange_id.upper()}] Max reconnect attempts reached for {coin} orderbook"
                    )
                    break

                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _trades_watch_loop(
        self, exchange: CCXTExchange, coin: str, symbol: str
    ) -> None:
        """
        Continuous trades watch loop for tick-level data.

        Provides ~16ms granularity price updates, VWAP, and candles
        built directly from trade data.
        """
        exchange_id = exchange.exchange_id
        reconnect_count = 0
        trades_tracker = self._trades_trackers[exchange_id]

        while self._running:
            try:
                trade_data_list = await exchange.watch_trades(symbol, coin)

                if trade_data_list:
                    # Convert TradeData to Trade objects
                    trades = [
                        Trade(
                            timestamp=td.timestamp,
                            price=td.price,
                            amount=td.amount,
                            side=td.side,
                            trade_id=td.trade_id,
                        )
                        for td in trade_data_list
                    ]

                    # Update tracker and get outputs
                    tick_price, tick_vwap, tick_candles = trades_tracker.update(
                        trades, coin
                    )

                    # Write to Redis (throttled internally)
                    if tick_price:
                        self._redis_writer.write_tick_price(tick_price)
                    if tick_vwap:
                        self._redis_writer.write_tick_vwap(tick_vwap)
                    for candle in tick_candles:
                        self._redis_writer.write_tick_candle(candle)

                    # Update stats
                    self._stats[exchange_id]["trades_count"] += len(trades)

                    reconnect_count = 0  # Reset on success
                else:
                    # None returned (error handled in watch_trades)
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{exchange_id.upper()}] Trades loop error for {coin}: {e}")
                reconnect_count += 1

                if reconnect_count >= MAX_RECONNECT_ATTEMPTS:
                    logger.error(
                        f"[{exchange_id.upper()}] Max reconnect attempts reached for {coin} trades"
                    )
                    break

                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _stats_loop(self) -> None:
        """Periodic statistics logging."""
        while self._running:
            await asyncio.sleep(STATS_LOG_INTERVAL)

            now = datetime.now(timezone.utc)
            elapsed = (now - self._last_stats_time).total_seconds()
            self._last_stats_time = now

            # Log stats for each exchange
            logger.info("-" * 50)
            logger.info(f"[STATS] Last {elapsed:.0f} seconds:")

            for exchange_id in EXCHANGES:
                if exchange_id not in self._stats:
                    continue

                stats = self._stats[exchange_id]
                ticker_count = stats.get("ticker_count", 0)
                orderbook_count = stats.get("orderbook_count", 0)
                trades_count = stats.get("trades_count", 0)

                ticker_rate = ticker_count / elapsed if elapsed > 0 else 0
                orderbook_rate = orderbook_count / elapsed if elapsed > 0 else 0
                trades_rate = trades_count / elapsed if elapsed > 0 else 0

                # Build log message
                log_msg = (
                    f"  [{exchange_id.upper():8}] "
                    f"Tickers: {ticker_count:6} ({ticker_rate:.1f}/s) | "
                    f"Orderbooks: {orderbook_count:6} ({orderbook_rate:.1f}/s)"
                )
                if ENABLE_TICK_TRADES:
                    log_msg += f" | Trades: {trades_count:6} ({trades_rate:.1f}/s)"
                logger.info(log_msg)

                # Reset counters
                stats["ticker_count"] = 0
                stats["orderbook_count"] = 0
                stats["trades_count"] = 0

            logger.info("-" * 50)

    async def _db_flush_loop(self) -> None:
        """Periodic DB buffer flush."""
        while self._running:
            await asyncio.sleep(5)

            try:
                if self._db_writer.should_flush():
                    count = self._db_writer.flush()
                    if count > 0:
                        logger.debug(f"[DB] Flushed {count} price records")
            except Exception as e:
                logger.error(f"DB flush error: {e}")

    async def shutdown(self) -> None:
        """Graceful shutdown - close all connections."""
        logger.info("Shutting down...")
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Close exchange connections
        for exchange_id, exchange in self._exchanges.items():
            await exchange.close()

        # Close Redis
        self._redis_writer.close()

        # Close DB
        self._db_writer.close()

        logger.info("Shutdown complete")


async def main():
    """Entry point with signal handling."""
    service = MultiExchangeStream()

    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(service.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await service.run()
    except Exception as e:
        logger.error(f"Service error: {e}")
    finally:
        await service.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
