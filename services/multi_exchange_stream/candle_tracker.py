"""
Candle Tracker

Tracks current candle OHLCV for multiple timeframes from 1-minute candle stream.
Calculates derived metrics (body_abs, direction, etc.) for compatibility with
existing paper trader systems.
"""
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from exchange_base import OHLCVData

logger = logging.getLogger(__name__)

# Timeframes to track: (minutes, label)
TIMEFRAMES = [(5, "5m"), (15, "15m"), (60, "1h"), (240, "4h")]


@dataclass
class CurrentCandle:
    """
    Current candle data compatible with existing paper trader format.

    Matches Redis key format: current_candle:{exchange}:{coin}_{tf}
    """
    exchange: str
    coin: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    candle_start: str  # ISO format
    candle_end: str    # ISO format
    elapsed_min: int
    direction: str     # "up", "down", "flat"
    body_abs: float    # abs(close - open) / open * 100
    price_change_pct: float
    updated_at: str    # ISO format

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "exchange": self.exchange,
            "coin": self.coin,
            "timeframe": self.timeframe,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "candle_start": self.candle_start,
            "candle_end": self.candle_end,
            "elapsed_min": self.elapsed_min,
            "direction": self.direction,
            "body_abs": self.body_abs,
            "price_change_pct": self.price_change_pct,
            "updated_at": self.updated_at,
        }


class CandleTracker:
    """
    Tracks current candle OHLCV for multiple timeframes.

    Receives 1-minute candles and aggregates them into 15m, 1h, 4h candles.
    """

    def __init__(self, exchange_id: str):
        """
        Initialize candle tracker for an exchange.

        Args:
            exchange_id: Exchange identifier (e.g., "binance")
        """
        self.exchange_id = exchange_id

        # 1-minute candle cache: {coin: deque of OHLCVData}
        # Keep last 240 minutes (4 hours) for aggregation
        self._candles_1m: Dict[str, deque] = {}
        self._max_candles = 240

        # Current candle state: {coin_tf: CurrentCandle}
        self._current_candles: Dict[str, CurrentCandle] = {}

    def update(self, candle: OHLCVData) -> List[CurrentCandle]:
        """
        Update tracker with new 1-minute candle.

        Args:
            candle: 1-minute OHLCV data

        Returns:
            List of updated CurrentCandle for all timeframes
        """
        coin = candle.coin

        # Initialize cache if needed
        if coin not in self._candles_1m:
            self._candles_1m[coin] = deque(maxlen=self._max_candles)

        # Add to cache
        self._candles_1m[coin].append(candle)

        # Calculate current candles for all timeframes
        results = []
        now = datetime.fromtimestamp(candle.timestamp / 1000, tz=timezone.utc)

        for duration_min, tf_label in TIMEFRAMES:
            current = self._calculate_current_candle(coin, tf_label, duration_min, now)
            if current:
                key = f"{coin}_{tf_label}"
                self._current_candles[key] = current
                results.append(current)

        return results

    def _calculate_current_candle(
        self, coin: str, timeframe: str, duration_min: int, now: datetime
    ) -> Optional[CurrentCandle]:
        """
        Calculate current candle for a specific timeframe.

        Args:
            coin: Coin identifier
            timeframe: Timeframe label (e.g., "15m")
            duration_min: Duration in minutes
            now: Current timestamp

        Returns:
            CurrentCandle if enough data, None otherwise
        """
        if coin not in self._candles_1m or not self._candles_1m[coin]:
            return None

        # Calculate candle start time (aligned to timeframe boundary)
        candle_start = self._get_candle_start(now, duration_min)
        candle_end = candle_start.replace(
            minute=candle_start.minute + duration_min if candle_start.minute + duration_min < 60
            else (candle_start.minute + duration_min) % 60
        )
        if duration_min >= 60:
            hours_add = duration_min // 60
            candle_end = candle_start.replace(
                hour=(candle_start.hour + hours_add) % 24,
                minute=0
            )

        # Filter candles within current timeframe window
        candles_in_window = [
            c for c in self._candles_1m[coin]
            if datetime.fromtimestamp(c.timestamp / 1000, tz=timezone.utc) >= candle_start
        ]

        if not candles_in_window:
            return None

        # Aggregate OHLCV
        open_price = candles_in_window[0].open
        high_price = max(c.high for c in candles_in_window)
        low_price = min(c.low for c in candles_in_window)
        close_price = candles_in_window[-1].close
        total_volume = sum(c.volume for c in candles_in_window)

        # Calculate elapsed minutes
        elapsed_min = len(candles_in_window)

        # Calculate direction
        if close_price > open_price:
            direction = "up"
        elif close_price < open_price:
            direction = "down"
        else:
            direction = "flat"

        # Calculate body_abs (percentage)
        body_abs = abs(close_price - open_price) / open_price * 100 if open_price > 0 else 0

        # Calculate price change percentage
        price_change_pct = (close_price - open_price) / open_price * 100 if open_price > 0 else 0

        return CurrentCandle(
            exchange=self.exchange_id,
            coin=coin,
            timeframe=timeframe,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=total_volume,
            candle_start=candle_start.isoformat(),
            candle_end=candle_end.isoformat(),
            elapsed_min=elapsed_min,
            direction=direction,
            body_abs=round(body_abs, 4),
            price_change_pct=round(price_change_pct, 4),
            updated_at=now.isoformat(),
        )

    def _get_candle_start(self, now: datetime, duration_min: int) -> datetime:
        """Get the start time of the current candle period."""
        if duration_min < 60:
            # Sub-hourly: align to duration boundary within the hour
            aligned_minute = (now.minute // duration_min) * duration_min
            return now.replace(minute=aligned_minute, second=0, microsecond=0)
        else:
            # Hourly or longer: align to hour boundary
            hours = duration_min // 60
            aligned_hour = (now.hour // hours) * hours
            return now.replace(hour=aligned_hour, minute=0, second=0, microsecond=0)

    def get_current_candle(self, coin: str, timeframe: str) -> Optional[CurrentCandle]:
        """Get current candle for a coin and timeframe."""
        key = f"{coin}_{timeframe}"
        return self._current_candles.get(key)

    def get_all_current_candles(self) -> List[CurrentCandle]:
        """Get all current candles."""
        return list(self._current_candles.values())
