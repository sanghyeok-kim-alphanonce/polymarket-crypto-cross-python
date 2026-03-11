"""
CCXT Exchange Implementation

Implements ExchangeBase using ccxt.pro for real-time WebSocket streaming.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List

import ccxt.pro as ccxtpro

from exchange_base import ExchangeBase, TickerData, OrderbookData, OHLCVData, TradeData

logger = logging.getLogger(__name__)


class CCXTExchange(ExchangeBase):
    """
    Exchange implementation using ccxt.pro.

    Provides real-time ticker and orderbook streaming via WebSocket.
    """

    def __init__(self, exchange_id: str):
        """
        Initialize CCXT exchange.

        Args:
            exchange_id: The ccxt exchange identifier (e.g., "binance", "bybit")
        """
        super().__init__(exchange_id)
        self._exchange: Optional[ccxtpro.Exchange] = None

    async def connect(self) -> bool:
        """
        Connect to the exchange.

        Creates the ccxt.pro exchange instance and loads markets.

        Returns:
            True if exchange instance created and markets loaded successfully.
        """
        try:
            # Get exchange class from ccxt.pro
            exchange_class = getattr(ccxtpro, self.exchange_id, None)
            if exchange_class is None:
                logger.error(f"[{self.exchange_id.upper()}] Exchange not supported by ccxt.pro")
                return False

            # Create exchange instance with optimized options
            self._exchange = exchange_class({
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                },
            })

            # Load markets (required before watch_* calls)
            await self._exchange.load_markets()

            self._connected = True
            logger.info(f"[{self.exchange_id.upper()}] Connected and loaded {len(self._exchange.markets)} markets")
            return True

        except Exception as e:
            logger.error(f"[{self.exchange_id.upper()}] Failed to connect: {e}")
            return False

    async def watch_ticker(self, symbol: str, coin: str) -> Optional[TickerData]:
        """
        Watch real-time ticker updates.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            coin: Coin identifier (e.g., "btc")

        Returns:
            TickerData with normalized price information.
        """
        if not self._exchange:
            logger.warning(f"[{self.exchange_id.upper()}] Exchange not initialized")
            return None

        try:
            # Watch ticker via WebSocket
            ticker = await self._exchange.watch_ticker(symbol)

            # Extract and normalize data
            timestamp = ticker.get("timestamp") or int(datetime.now(timezone.utc).timestamp() * 1000)
            dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)

            return TickerData(
                exchange=self.exchange_id,
                symbol=symbol,
                coin=coin,
                price=float(ticker.get("last") or ticker.get("close") or 0),
                bid=float(ticker.get("bid") or 0),
                ask=float(ticker.get("ask") or 0),
                timestamp=timestamp,
                datetime=dt.isoformat(),
            )

        except ccxtpro.NetworkError as e:
            logger.warning(f"[{self.exchange_id.upper()}] Network error watching ticker {symbol}: {e}")
            return None
        except ccxtpro.ExchangeError as e:
            logger.warning(f"[{self.exchange_id.upper()}] Exchange error watching ticker {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"[{self.exchange_id.upper()}] Unexpected error watching ticker {symbol}: {e}")
            return None

    async def watch_order_book(
        self, symbol: str, coin: str, limit: int = 10
    ) -> Optional[OrderbookData]:
        """
        Watch real-time orderbook updates.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            coin: Coin identifier (e.g., "btc")
            limit: Number of orderbook levels

        Returns:
            OrderbookData with normalized bid/ask levels.
        """
        if not self._exchange:
            logger.warning(f"[{self.exchange_id.upper()}] Exchange not initialized")
            return None

        try:
            # Watch orderbook via WebSocket
            orderbook = await self._exchange.watch_order_book(symbol, limit)

            # Extract timestamp
            timestamp = orderbook.get("timestamp") or int(datetime.now(timezone.utc).timestamp() * 1000)

            # Normalize bid/ask format to [[price, size], ...]
            bids = [[float(b[0]), float(b[1])] for b in orderbook.get("bids", [])[:limit]]
            asks = [[float(a[0]), float(a[1])] for a in orderbook.get("asks", [])[:limit]]

            return OrderbookData(
                exchange=self.exchange_id,
                symbol=symbol,
                coin=coin,
                bids=bids,
                asks=asks,
                timestamp=timestamp,
            )

        except ccxtpro.NetworkError as e:
            logger.warning(f"[{self.exchange_id.upper()}] Network error watching orderbook {symbol}: {e}")
            return None
        except ccxtpro.ExchangeError as e:
            logger.warning(f"[{self.exchange_id.upper()}] Exchange error watching orderbook {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"[{self.exchange_id.upper()}] Unexpected error watching orderbook {symbol}: {e}")
            return None

    async def watch_ohlcv(
        self, symbol: str, coin: str, timeframe: str = "1m"
    ) -> Optional[List[OHLCVData]]:
        """
        Watch real-time OHLCV candle updates.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            coin: Coin identifier (e.g., "btc")
            timeframe: Candle timeframe (e.g., "1m", "15m", "1h")

        Returns:
            List of OHLCVData with normalized candle information.
        """
        if not self._exchange:
            logger.warning(f"[{self.exchange_id.upper()}] Exchange not initialized")
            return None

        try:
            # Watch OHLCV via WebSocket
            # ccxt returns [[timestamp, open, high, low, close, volume], ...]
            ohlcv_list = await self._exchange.watch_ohlcv(symbol, timeframe)

            if not ohlcv_list:
                return None

            result = []
            for candle in ohlcv_list:
                if len(candle) >= 6:
                    result.append(OHLCVData(
                        exchange=self.exchange_id,
                        symbol=symbol,
                        coin=coin,
                        timeframe=timeframe,
                        timestamp=int(candle[0]),
                        open=float(candle[1]),
                        high=float(candle[2]),
                        low=float(candle[3]),
                        close=float(candle[4]),
                        volume=float(candle[5]),
                    ))

            return result if result else None

        except ccxtpro.NetworkError as e:
            logger.warning(f"[{self.exchange_id.upper()}] Network error watching OHLCV {symbol}: {e}")
            return None
        except ccxtpro.ExchangeError as e:
            logger.warning(f"[{self.exchange_id.upper()}] Exchange error watching OHLCV {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"[{self.exchange_id.upper()}] Unexpected error watching OHLCV {symbol}: {e}")
            return None

    async def watch_trades(
        self, symbol: str, coin: str
    ) -> Optional[List[TradeData]]:
        """
        Watch real-time trade stream.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            coin: Coin identifier (e.g., "btc")

        Returns:
            List of TradeData with individual trades since last call.
        """
        if not self._exchange:
            logger.warning(f"[{self.exchange_id.upper()}] Exchange not initialized")
            return None

        try:
            # Watch trades via WebSocket
            # ccxt returns: [{"id", "timestamp", "price", "amount", "side", ...}, ...]
            trades = await self._exchange.watch_trades(symbol)

            if not trades:
                return None

            result = []
            for trade in trades:
                result.append(TradeData(
                    exchange=self.exchange_id,
                    symbol=symbol,
                    coin=coin,
                    trade_id=str(trade.get("id", "")),
                    timestamp=int(trade.get("timestamp") or 0),
                    price=float(trade.get("price") or 0),
                    amount=float(trade.get("amount") or 0),
                    side=trade.get("side", "unknown"),
                ))

            return result

        except ccxtpro.NetworkError as e:
            logger.warning(f"[{self.exchange_id.upper()}] Network error watching trades {symbol}: {e}")
            return None
        except ccxtpro.ExchangeError as e:
            logger.warning(f"[{self.exchange_id.upper()}] Exchange error watching trades {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"[{self.exchange_id.upper()}] Unexpected error watching trades {symbol}: {e}")
            return None

    async def close(self) -> None:
        """Close the exchange connection gracefully."""
        if self._exchange:
            try:
                await self._exchange.close()
                logger.info(f"[{self.exchange_id.upper()}] Connection closed")
            except Exception as e:
                logger.warning(f"[{self.exchange_id.upper()}] Error closing connection: {e}")
            finally:
                self._exchange = None
                self._connected = False
