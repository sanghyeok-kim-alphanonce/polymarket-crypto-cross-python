"""
Exchange Base Class

Abstract base class defining the interface for exchange data streams.
All exchange implementations must inherit from this class.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TickerData:
    """Normalized ticker data from any exchange."""
    exchange: str
    symbol: str
    coin: str
    price: float
    bid: float
    ask: float
    timestamp: int  # Unix timestamp in milliseconds
    datetime: str   # ISO format string

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "coin": self.coin,
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "timestamp": self.timestamp,
            "datetime": self.datetime,
        }


@dataclass
class OrderbookData:
    """Normalized orderbook data from any exchange."""
    exchange: str
    symbol: str
    coin: str
    bids: List[List[float]]  # [[price, size], ...]
    asks: List[List[float]]  # [[price, size], ...]
    timestamp: int  # Unix timestamp in milliseconds

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "coin": self.coin,
            "bids": self.bids,
            "asks": self.asks,
            "timestamp": self.timestamp,
        }


@dataclass
class OHLCVData:
    """Normalized OHLCV candle data from any exchange."""
    exchange: str
    symbol: str
    coin: str
    timeframe: str  # "1m", "15m", "1h", "4h"
    timestamp: int  # Candle open time in milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "coin": self.coin,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class TradeData:
    """Normalized trade data from any exchange."""
    exchange: str
    symbol: str
    coin: str
    trade_id: str
    timestamp: int  # Unix timestamp in milliseconds
    price: float
    amount: float
    side: str  # "buy" or "sell"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "coin": self.coin,
            "trade_id": self.trade_id,
            "timestamp": self.timestamp,
            "price": self.price,
            "amount": self.amount,
            "side": self.side,
        }


class ExchangeBase(ABC):
    """
    Abstract base class for exchange data streams.

    Defines the common interface for connecting to exchanges
    and streaming real-time data.
    """

    def __init__(self, exchange_id: str):
        """
        Initialize the exchange.

        Args:
            exchange_id: The ccxt exchange identifier (e.g., "binance", "bybit")
        """
        self.exchange_id = exchange_id
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if exchange is connected."""
        return self._connected

    @abstractmethod
    async def connect(self) -> bool:
        """
        Connect to the exchange WebSocket.

        Returns:
            True if connection successful, False otherwise.
        """
        pass

    @abstractmethod
    async def watch_ticker(self, symbol: str, coin: str) -> Optional[TickerData]:
        """
        Watch real-time ticker updates for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., "BTC/USDT")
            coin: Coin identifier (e.g., "btc")

        Returns:
            TickerData if update received, None on error.
        """
        pass

    @abstractmethod
    async def watch_order_book(
        self, symbol: str, coin: str, limit: int = 10
    ) -> Optional[OrderbookData]:
        """
        Watch real-time orderbook updates for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., "BTC/USDT")
            coin: Coin identifier (e.g., "btc")
            limit: Number of orderbook levels to return

        Returns:
            OrderbookData if update received, None on error.
        """
        pass

    @abstractmethod
    async def watch_ohlcv(
        self, symbol: str, coin: str, timeframe: str = "1m"
    ) -> Optional[List[OHLCVData]]:
        """
        Watch real-time OHLCV candle updates for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., "BTC/USDT")
            coin: Coin identifier (e.g., "btc")
            timeframe: Candle timeframe (e.g., "1m", "15m", "1h")

        Returns:
            List of OHLCVData if update received, None on error.
        """
        pass

    @abstractmethod
    async def watch_trades(
        self, symbol: str, coin: str
    ) -> Optional[List["TradeData"]]:
        """
        Watch real-time trade stream for a symbol.

        Provides tick-level trade data with ~16ms granularity.

        Args:
            symbol: Trading pair symbol (e.g., "BTC/USDT")
            coin: Coin identifier (e.g., "btc")

        Returns:
            List of TradeData if trades received, None on error.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the exchange connection gracefully."""
        pass
