"""
DB Writer for Multi Exchange Stream

Writes exchange price data to exchange_prices table.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional, List, Tuple

import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

# DB Configuration (poly-test defaults)
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5435)),
    "user": os.getenv("DB_USER", "paper"),
    "password": os.getenv("DB_PASSWORD", "papertrade"),
    "database": os.getenv("DB_NAME", "paper_trade"),
}

# Buffer settings
BUFFER_SIZE = 100  # Flush after this many records
FLUSH_INTERVAL = 5  # Flush every N seconds


class DBWriter:
    """
    Buffered DB writer for exchange prices.

    Batches inserts for efficiency and handles connection management.
    """

    def __init__(self):
        self._conn: Optional[psycopg2.extensions.connection] = None
        self._buffer: List[Tuple] = []
        self._last_flush: float = 0

    def connect(self) -> bool:
        """
        Connect to PostgreSQL.

        Retries up to 10 times with 3 second delays.
        """
        import time

        for i in range(10):
            try:
                self._conn = psycopg2.connect(
                    host=DB_CONFIG["host"],
                    port=DB_CONFIG["port"],
                    user=DB_CONFIG["user"],
                    password=DB_CONFIG["password"],
                    database=DB_CONFIG["database"],
                )
                self._conn.autocommit = False
                logger.info(f"Connected to DB at {DB_CONFIG['host']}:{DB_CONFIG['port']}")
                return True
            except Exception as e:
                logger.warning(f"DB connection attempt {i + 1}/10 failed: {e}")
                if i < 9:
                    time.sleep(3)

        logger.error("Failed to connect to DB after 10 attempts")
        return False

    def _check_connection(self) -> bool:
        """Check DB connection and reconnect if needed."""
        try:
            if self._conn is None or self._conn.closed:
                return self.connect()
            # Test connection
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception as e:
            logger.warning(f"DB connection lost: {e}")
            return self.connect()

    def buffer_price(
        self,
        exchange: str,
        coin: str,
        price: float,
        bid: float,
        ask: float,
        timestamp: datetime,
    ) -> None:
        """Buffer a price record for batch insert."""
        self._buffer.append((timestamp, coin, exchange, price, bid, ask))

    def should_flush(self) -> bool:
        """Check if buffer should be flushed."""
        import time

        if len(self._buffer) >= BUFFER_SIZE:
            return True
        if time.time() - self._last_flush >= FLUSH_INTERVAL:
            return True
        return False

    def flush(self) -> int:
        """Flush buffer to database."""
        import time

        if not self._buffer:
            return 0

        if not self._check_connection():
            logger.error("Cannot flush: DB connection unavailable")
            return 0

        count = 0
        try:
            with self._conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO exchange_prices (time, coin, exchange, price, bid, ask)
                    VALUES %s
                    ON CONFLICT (time, coin, exchange) DO UPDATE SET
                        price = EXCLUDED.price,
                        bid = EXCLUDED.bid,
                        ask = EXCLUDED.ask
                    """,
                    self._buffer,
                    template="(%s, %s, %s, %s, %s, %s)",
                )
                count = len(self._buffer)
                self._conn.commit()
                self._buffer.clear()
                self._last_flush = time.time()
                logger.debug(f"Flushed {count} price records to DB")
        except Exception as e:
            logger.error(f"DB flush error: {e}")
            self._conn.rollback()
            self._buffer.clear()

        return count

    def close(self) -> None:
        """Close DB connection."""
        if self._buffer:
            self.flush()
        if self._conn:
            try:
                self._conn.close()
                logger.info("DB connection closed")
            except Exception as e:
                logger.warning(f"Error closing DB connection: {e}")
            finally:
                self._conn = None
