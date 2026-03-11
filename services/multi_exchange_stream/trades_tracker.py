"""
Trades Tracker

Tracks tick-level trade data for real-time price updates, VWAP calculation,
and candle building from raw trade streams.
"""
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Timeframe configurations: (duration_seconds, label)
TICK_TIMEFRAMES = [
    (60, "1m"),
    (300, "5m"),
    (900, "15m"),
    (3600, "1h"),
]

# VWAP window configurations (seconds)
VWAP_WINDOWS = [60, 300, 900]  # 1m, 5m, 15m


@dataclass
class Trade:
    """Individual trade data."""
    timestamp: int      # Unix ms
    price: float
    amount: float       # Trade volume
    side: str           # "buy" or "sell"
    trade_id: str


@dataclass
class TickPrice:
    """Latest tick price data."""
    exchange: str
    coin: str
    price: float
    timestamp: int
    datetime: str
    trade_count: int    # Trade count in current tracking window

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "coin": self.coin,
            "price": self.price,
            "timestamp": self.timestamp,
            "datetime": self.datetime,
            "trade_count": self.trade_count,
        }


@dataclass
class TickVWAP:
    """VWAP (Volume Weighted Average Price) data."""
    exchange: str
    coin: str
    vwap_1m: float      # 1-minute VWAP
    vwap_5m: float      # 5-minute VWAP
    vwap_15m: float     # 15-minute VWAP
    total_volume_1m: float
    trade_count_1m: int
    timestamp: int
    datetime: str

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "coin": self.coin,
            "vwap_1m": self.vwap_1m,
            "vwap_5m": self.vwap_5m,
            "vwap_15m": self.vwap_15m,
            "total_volume_1m": self.total_volume_1m,
            "trade_count_1m": self.trade_count_1m,
            "timestamp": self.timestamp,
            "datetime": self.datetime,
        }


@dataclass
class TickCandle:
    """Tick-level real-time candle data."""
    exchange: str
    coin: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int
    vwap: float
    candle_start: str
    candle_end: str
    elapsed_seconds: int
    direction: str
    body_abs: float
    price_change_pct: float
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "coin": self.coin,
            "timeframe": self.timeframe,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
            "vwap": self.vwap,
            "candle_start": self.candle_start,
            "candle_end": self.candle_end,
            "elapsed_seconds": self.elapsed_seconds,
            "direction": self.direction,
            "body_abs": self.body_abs,
            "price_change_pct": self.price_change_pct,
            "updated_at": self.updated_at,
        }


class CandleBuilder:
    """
    Builds a single timeframe candle from trade data.

    Maintains OHLCV state and produces TickCandle objects.
    """

    def __init__(self, exchange_id: str, coin: str, timeframe: str, duration_seconds: int):
        self.exchange_id = exchange_id
        self.coin = coin
        self.timeframe = timeframe
        self.duration_seconds = duration_seconds

        # Current candle state
        self._open: Optional[float] = None
        self._high: float = 0.0
        self._low: float = float('inf')
        self._close: float = 0.0
        self._volume: float = 0.0
        self._vwap_numerator: float = 0.0  # sum(price * volume)
        self._trade_count: int = 0
        self._candle_start: Optional[datetime] = None

    def update(self, trade: Trade) -> Optional[TickCandle]:
        """
        Update candle with trade data.

        Returns completed candle if crossing boundary, None otherwise.
        """
        trade_time = datetime.fromtimestamp(trade.timestamp / 1000, tz=timezone.utc)
        candle_start = self._get_candle_start(trade_time)

        # Detect new candle start
        if self._candle_start is None or candle_start > self._candle_start:
            completed_candle = self._finalize() if self._candle_start else None
            self._reset(candle_start, trade)
            return completed_candle

        # Update existing candle
        self._high = max(self._high, trade.price)
        self._low = min(self._low, trade.price)
        self._close = trade.price
        self._volume += trade.amount
        self._vwap_numerator += trade.price * trade.amount
        self._trade_count += 1

        return None

    def get_current(self) -> Optional[TickCandle]:
        """Get current in-progress candle."""
        if self._open is None or self._candle_start is None:
            return None

        now = datetime.now(timezone.utc)
        elapsed = int((now - self._candle_start).total_seconds())
        candle_end = self._candle_start + timedelta(seconds=self.duration_seconds)

        vwap = self._vwap_numerator / self._volume if self._volume > 0 else self._close

        # Direction calculation
        if self._close > self._open:
            direction = "up"
        elif self._close < self._open:
            direction = "down"
        else:
            direction = "flat"

        # Body absolute (percentage)
        body_abs = abs(self._close - self._open) / self._open * 100 if self._open > 0 else 0
        price_change_pct = (self._close - self._open) / self._open * 100 if self._open > 0 else 0

        return TickCandle(
            exchange=self.exchange_id,
            coin=self.coin,
            timeframe=self.timeframe,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
            trade_count=self._trade_count,
            vwap=round(vwap, 8),
            candle_start=self._candle_start.isoformat(),
            candle_end=candle_end.isoformat(),
            elapsed_seconds=elapsed,
            direction=direction,
            body_abs=round(body_abs, 4),
            price_change_pct=round(price_change_pct, 4),
            updated_at=now.isoformat(),
        )

    def _get_candle_start(self, now: datetime) -> datetime:
        """Calculate candle start time aligned to timeframe boundary."""
        if self.duration_seconds < 3600:
            # Sub-hourly
            total_seconds = now.minute * 60 + now.second
            aligned = (total_seconds // self.duration_seconds) * self.duration_seconds
            return now.replace(
                minute=aligned // 60,
                second=aligned % 60,
                microsecond=0
            )
        else:
            # Hourly or longer
            hours = self.duration_seconds // 3600
            aligned_hour = (now.hour // hours) * hours
            return now.replace(hour=aligned_hour, minute=0, second=0, microsecond=0)

    def _reset(self, candle_start: datetime, first_trade: Trade) -> None:
        """Start a new candle."""
        self._candle_start = candle_start
        self._open = first_trade.price
        self._high = first_trade.price
        self._low = first_trade.price
        self._close = first_trade.price
        self._volume = first_trade.amount
        self._vwap_numerator = first_trade.price * first_trade.amount
        self._trade_count = 1

    def _finalize(self) -> Optional[TickCandle]:
        """Finalize current candle and return it."""
        return self.get_current()


class VWAPCalculator:
    """
    Time-window based VWAP calculator.

    Maintains a sliding window of trades for VWAP calculation.
    Uses deque with maxlen to limit memory usage.
    """

    def __init__(self, max_window_seconds: int = 900):
        """
        Initialize VWAP calculator.

        Args:
            max_window_seconds: Maximum window to keep trades (default 15 min)
        """
        self.max_window_seconds = max_window_seconds
        # Limit to ~10000 trades max to prevent memory issues
        self._trades: deque = deque(maxlen=10000)

    def add_trade(self, timestamp_ms: int, price: float, amount: float) -> None:
        """Add a trade to the buffer."""
        self._trades.append((timestamp_ms, price, amount))
        self._cleanup(timestamp_ms)

    def get_vwap(self, window_seconds: int, current_time_ms: int) -> Tuple[float, float, int]:
        """
        Calculate VWAP for a specific time window.

        Args:
            window_seconds: Window duration in seconds
            current_time_ms: Current timestamp in milliseconds

        Returns:
            (vwap, total_volume, trade_count)
        """
        cutoff = current_time_ms - (window_seconds * 1000)

        total_value = 0.0
        total_volume = 0.0
        count = 0

        for ts, price, amount in self._trades:
            if ts >= cutoff:
                total_value += price * amount
                total_volume += amount
                count += 1

        vwap = total_value / total_volume if total_volume > 0 else 0.0
        return vwap, total_volume, count

    def _cleanup(self, current_time_ms: int) -> None:
        """Remove trades older than max window."""
        cutoff = current_time_ms - (self.max_window_seconds * 1000)
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()


class TradesTracker:
    """
    Tick-level trade data tracker and aggregator.

    Features:
    - Real-time price updates (TickPrice)
    - VWAP calculation (TickVWAP)
    - Real-time candle building (TickCandle)
    """

    def __init__(self, exchange_id: str):
        self.exchange_id = exchange_id

        # Per-coin state
        self._candle_builders: Dict[str, Dict[str, CandleBuilder]] = {}  # {coin: {tf: builder}}
        self._vwap_calculators: Dict[str, VWAPCalculator] = {}  # {coin: calculator}
        self._last_prices: Dict[str, Trade] = {}  # {coin: last_trade}
        self._trade_counts: Dict[str, int] = {}  # {coin: cumulative count}

    def update(self, trades: List[Trade], coin: str) -> Tuple[
        Optional[TickPrice],
        Optional[TickVWAP],
        List[TickCandle]
    ]:
        """
        Update tracker with new trades.

        Args:
            trades: Newly received trade list
            coin: Coin identifier

        Returns:
            (tick_price, tick_vwap, tick_candles)
        """
        if not trades:
            return None, None, []

        # Initialize structures if needed
        self._ensure_initialized(coin)

        completed_candles = []

        for trade in trades:
            # Update VWAP calculator
            self._vwap_calculators[coin].add_trade(
                trade.timestamp, trade.price, trade.amount
            )

            # Update candle builders
            for tf_label, builder in self._candle_builders[coin].items():
                completed = builder.update(trade)
                if completed:
                    completed_candles.append(completed)

            # Track last trade
            self._last_prices[coin] = trade
            self._trade_counts[coin] = self._trade_counts.get(coin, 0) + 1

        # Generate outputs
        last_trade = trades[-1]
        now = datetime.fromtimestamp(last_trade.timestamp / 1000, tz=timezone.utc)

        # TickPrice
        tick_price = TickPrice(
            exchange=self.exchange_id,
            coin=coin,
            price=last_trade.price,
            timestamp=last_trade.timestamp,
            datetime=now.isoformat(),
            trade_count=self._trade_counts[coin],
        )

        # TickVWAP
        vwap_calc = self._vwap_calculators[coin]
        vwap_1m, vol_1m, cnt_1m = vwap_calc.get_vwap(60, last_trade.timestamp)
        vwap_5m, _, _ = vwap_calc.get_vwap(300, last_trade.timestamp)
        vwap_15m, _, _ = vwap_calc.get_vwap(900, last_trade.timestamp)

        tick_vwap = TickVWAP(
            exchange=self.exchange_id,
            coin=coin,
            vwap_1m=round(vwap_1m, 8),
            vwap_5m=round(vwap_5m, 8),
            vwap_15m=round(vwap_15m, 8),
            total_volume_1m=vol_1m,
            trade_count_1m=cnt_1m,
            timestamp=last_trade.timestamp,
            datetime=now.isoformat(),
        )

        # Get current candles for all timeframes
        current_candles = [
            builder.get_current()
            for builder in self._candle_builders[coin].values()
            if builder.get_current()
        ]

        return tick_price, tick_vwap, current_candles + completed_candles

    def _ensure_initialized(self, coin: str) -> None:
        """Initialize per-coin data structures."""
        if coin not in self._candle_builders:
            self._candle_builders[coin] = {
                tf_label: CandleBuilder(self.exchange_id, coin, tf_label, duration)
                for duration, tf_label in TICK_TIMEFRAMES
            }

        if coin not in self._vwap_calculators:
            self._vwap_calculators[coin] = VWAPCalculator(max_window_seconds=900)

    def reset_trade_count(self, coin: str) -> None:
        """Reset trade count for statistics logging."""
        self._trade_counts[coin] = 0

    def get_trade_count(self, coin: str) -> int:
        """Get current trade count for a coin."""
        return self._trade_counts.get(coin, 0)
