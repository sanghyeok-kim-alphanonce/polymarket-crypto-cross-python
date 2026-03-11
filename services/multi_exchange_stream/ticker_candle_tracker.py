"""
Ticker-based Candle Tracker

Builds OHLC candles from ticker prices (like Chainlink approach).
Updates in real-time as ticker prices arrive.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional


@dataclass
class CurrentCandle:
    """Current candle data for Redis."""
    exchange: str
    coin: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    candle_start: str  # ISO format
    candle_end: str    # ISO format
    direction: str     # "up", "down", "flat"
    price_change_pct: float
    updated_at: str    # ISO format

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "coin": self.coin,
            "timeframe": self.timeframe,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "candle_start": self.candle_start,
            "candle_end": self.candle_end,
            "direction": self.direction,
            "price_change_pct": self.price_change_pct,
            "updated_at": self.updated_at,
        }


def get_candle_start(now: datetime, duration_min: int) -> datetime:
    """Get candle start time aligned to boundary."""
    if duration_min < 60:
        aligned_minute = (now.minute // duration_min) * duration_min
        return now.replace(minute=aligned_minute, second=0, microsecond=0)
    else:
        hours = duration_min // 60
        aligned_hour = (now.hour // hours) * hours
        return now.replace(hour=aligned_hour, minute=0, second=0, microsecond=0)


# Timeframes: (minutes, label)
TIMEFRAMES = [(5, "5m"), (15, "15m"), (60, "1h"), (240, "4h")]


class TickerCandleTracker:
    """
    Builds OHLC candles from ticker prices.

    Similar to ChainlinkCandleTracker in real_time_price_fetcher.
    """

    def __init__(self, exchange_id: str):
        self.exchange_id = exchange_id
        # {coin: {tf: {'open', 'high', 'low', 'close', 'candle_start'}}}
        self._candles: Dict[str, Dict[str, Dict]] = {}

    def update(self, coin: str, price: float, now: datetime) -> List[CurrentCandle]:
        """
        Update candles with new ticker price.

        Returns list of CurrentCandle for all timeframes.
        """
        if coin not in self._candles:
            self._candles[coin] = {}

        results = []

        for duration_min, tf_label in TIMEFRAMES:
            candle_start = get_candle_start(now, duration_min)
            candle_end = candle_start + timedelta(minutes=duration_min)

            tf_key = tf_label
            candle_data = self._candles[coin].get(tf_key)

            # New candle period?
            if candle_data is None or candle_data['candle_start'] < candle_start:
                # Start new candle
                self._candles[coin][tf_key] = {
                    'open': price,
                    'high': price,
                    'low': price,
                    'close': price,
                    'candle_start': candle_start,
                }
            else:
                # Update existing candle
                candle_data['high'] = max(candle_data['high'], price)
                candle_data['low'] = min(candle_data['low'], price)
                candle_data['close'] = price

            # Build result
            cd = self._candles[coin][tf_key]
            open_price = cd['open']
            close_price = cd['close']

            if close_price > open_price:
                direction = "up"
            elif close_price < open_price:
                direction = "down"
            else:
                direction = "flat"

            price_change_pct = (close_price - open_price) / open_price * 100 if open_price > 0 else 0

            results.append(CurrentCandle(
                exchange=self.exchange_id,
                coin=coin,
                timeframe=tf_label,
                open=open_price,
                high=cd['high'],
                low=cd['low'],
                close=close_price,
                candle_start=cd['candle_start'].isoformat(),
                candle_end=candle_end.isoformat(),
                direction=direction,
                price_change_pct=round(price_change_pct, 4),
                updated_at=now.isoformat(),
            ))

        return results
