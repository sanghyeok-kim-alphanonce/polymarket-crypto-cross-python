"""
Redis Writer

Write-only Redis operations for exchange price and orderbook data.
"""
import json
import logging
import time
from typing import Dict, Optional

import redis

from config import (
    REDIS_CONFIG, REDIS_KEY_PRICE, REDIS_KEY_ORDERBOOK,
    REDIS_KEY_CURRENT_CANDLE, REDIS_TTL_SECONDS,
    REDIS_KEY_TICK_PRICE, REDIS_KEY_TICK_VWAP, REDIS_KEY_TICK_CANDLE,
    REDIS_TTL_TICK, TICK_THROTTLE_MS
)
from exchange_base import TickerData, OrderbookData
from candle_tracker import CurrentCandle
from trades_tracker import TickPrice, TickVWAP, TickCandle

logger = logging.getLogger(__name__)


class RedisWriter:
    """
    Write-only Redis interface for exchange data.

    Writes ticker and orderbook data with TTL for automatic expiration.
    Includes throttling for tick-level data to prevent Redis overload.
    """

    def __init__(self):
        self._client: Optional[redis.Redis] = None
        # Throttling state: {key: last_write_time_ms}
        self._last_tick_write: Dict[str, int] = {}

    def connect(self) -> bool:
        """
        Connect to Redis.

        Retries up to 10 times with 3 second delays.

        Returns:
            True if connected successfully.
        """
        import time

        for i in range(10):
            try:
                self._client = redis.Redis(
                    host=REDIS_CONFIG["host"],
                    port=REDIS_CONFIG["port"],
                    db=REDIS_CONFIG["db"],
                    decode_responses=True,
                )
                self._client.ping()
                logger.info(f"Connected to Redis at {REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}")
                return True
            except Exception as e:
                logger.warning(f"Redis connection attempt {i + 1}/10 failed: {e}")
                if i < 9:
                    time.sleep(3)

        logger.error("Failed to connect to Redis after 10 attempts")
        return False

    def _check_connection(self) -> bool:
        """Check Redis connection and reconnect if needed."""
        try:
            if self._client is None:
                return self.connect()
            self._client.ping()
            return True
        except Exception as e:
            logger.warning(f"Redis connection lost: {e}")
            return self.connect()

    def write_ticker(self, ticker: TickerData) -> bool:
        """
        Write ticker data to Redis.

        Key format: exchange_price:{exchange}:{coin}

        Args:
            ticker: Normalized ticker data

        Returns:
            True if write successful.
        """
        if not self._check_connection():
            return False

        try:
            key = REDIS_KEY_PRICE.format(exchange=ticker.exchange, coin=ticker.coin)
            data = json.dumps(ticker.to_dict())
            self._client.setex(key, REDIS_TTL_SECONDS, data)
            return True
        except Exception as e:
            logger.error(f"Redis write error for ticker {ticker.exchange}:{ticker.coin}: {e}")
            return False

    def write_orderbook(self, orderbook: OrderbookData) -> bool:
        """
        Write orderbook data to Redis.

        Key format: exchange_orderbook:{exchange}:{coin}

        Args:
            orderbook: Normalized orderbook data

        Returns:
            True if write successful.
        """
        if not self._check_connection():
            return False

        try:
            key = REDIS_KEY_ORDERBOOK.format(exchange=orderbook.exchange, coin=orderbook.coin)
            data = json.dumps(orderbook.to_dict())
            self._client.setex(key, REDIS_TTL_SECONDS, data)
            return True
        except Exception as e:
            logger.error(f"Redis write error for orderbook {orderbook.exchange}:{orderbook.coin}: {e}")
            return False

    def write_current_candle(self, candle: CurrentCandle) -> bool:
        """
        Write current candle data to Redis.

        Key format: current_candle:{exchange}:{coin}_{timeframe}

        This format is compatible with existing paper trader systems.

        Args:
            candle: Current candle data

        Returns:
            True if write successful.
        """
        if not self._check_connection():
            return False

        try:
            key = REDIS_KEY_CURRENT_CANDLE.format(
                exchange=candle.exchange,
                coin=candle.coin,
                timeframe=candle.timeframe
            )
            data = json.dumps(candle.to_dict())
            self._client.setex(key, REDIS_TTL_SECONDS, data)
            return True
        except Exception as e:
            logger.error(
                f"Redis write error for current_candle "
                f"{candle.exchange}:{candle.coin}_{candle.timeframe}: {e}"
            )
            return False

    def _should_write_tick(self, key: str, current_time_ms: int) -> bool:
        """
        Check if enough time has passed since last write for throttling.

        Args:
            key: Redis key to check
            current_time_ms: Current timestamp in milliseconds

        Returns:
            True if write should proceed, False if throttled.
        """
        last_write = self._last_tick_write.get(key, 0)
        if current_time_ms - last_write >= TICK_THROTTLE_MS:
            self._last_tick_write[key] = current_time_ms
            return True
        return False

    def write_tick_price(self, tick_price: TickPrice) -> bool:
        """
        Write tick price data to Redis.

        Key format: tick_price:{exchange}:{coin}
        Throttled to prevent overload during high volatility.

        Args:
            tick_price: Tick price data

        Returns:
            True if write successful or throttled, False on error.
        """
        if not self._check_connection():
            return False

        key = REDIS_KEY_TICK_PRICE.format(
            exchange=tick_price.exchange,
            coin=tick_price.coin
        )

        # Apply throttling
        if not self._should_write_tick(key, tick_price.timestamp):
            return True  # Throttled - not an error

        try:
            data = json.dumps(tick_price.to_dict())
            self._client.setex(key, REDIS_TTL_TICK, data)
            return True
        except Exception as e:
            logger.error(
                f"Redis write error for tick_price {tick_price.exchange}:{tick_price.coin}: {e}"
            )
            return False

    def write_tick_vwap(self, tick_vwap: TickVWAP) -> bool:
        """
        Write tick VWAP data to Redis.

        Key format: tick_vwap:{exchange}:{coin}
        Throttled to prevent overload during high volatility.

        Args:
            tick_vwap: VWAP data

        Returns:
            True if write successful or throttled, False on error.
        """
        if not self._check_connection():
            return False

        key = REDIS_KEY_TICK_VWAP.format(
            exchange=tick_vwap.exchange,
            coin=tick_vwap.coin
        )

        # Apply throttling
        if not self._should_write_tick(key, tick_vwap.timestamp):
            return True  # Throttled - not an error

        try:
            data = json.dumps(tick_vwap.to_dict())
            self._client.setex(key, REDIS_TTL_TICK, data)
            return True
        except Exception as e:
            logger.error(
                f"Redis write error for tick_vwap {tick_vwap.exchange}:{tick_vwap.coin}: {e}"
            )
            return False

    def write_tick_candle(self, tick_candle: TickCandle) -> bool:
        """
        Write tick candle data to Redis.

        Key format: tick_candle:{exchange}:{coin}_{timeframe}
        Throttled to prevent overload during high volatility.

        Args:
            tick_candle: Tick candle data

        Returns:
            True if write successful or throttled, False on error.
        """
        if not self._check_connection():
            return False

        key = REDIS_KEY_TICK_CANDLE.format(
            exchange=tick_candle.exchange,
            coin=tick_candle.coin,
            timeframe=tick_candle.timeframe
        )

        # Use current time for throttling (candle timestamp might be candle start)
        current_time_ms = int(time.time() * 1000)

        # Apply throttling
        if not self._should_write_tick(key, current_time_ms):
            return True  # Throttled - not an error

        try:
            data = json.dumps(tick_candle.to_dict())
            self._client.setex(key, REDIS_TTL_TICK, data)
            return True
        except Exception as e:
            logger.error(
                f"Redis write error for tick_candle "
                f"{tick_candle.exchange}:{tick_candle.coin}_{tick_candle.timeframe}: {e}"
            )
            return False

    def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            try:
                self._client.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.warning(f"Error closing Redis connection: {e}")
            finally:
                self._client = None
