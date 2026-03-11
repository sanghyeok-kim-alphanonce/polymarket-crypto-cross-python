"""
Multi Exchange Stream Configuration

Streams real-time price and orderbook data from multiple exchanges via ccxt.pro.
"""
import os

# =============================================================================
# Exchange Configuration
# =============================================================================

# Supported exchanges (ccxt exchange IDs)
# Tier A (Fully Compatible - No auth required):
#   - binance, bybit: Already tested, production-ready
#   - gate: Full ccxt.pro support, USDT pairs, no auth for public data
#   - bitget: Full ccxt.pro support, USDT pairs, no auth for public data
#
# Note: binance is excluded here because binance_ws service already collects it
EXCHANGES = ["bybit", "gate", "bitget"]

# Exchanges that require authentication for orderbook
EXCHANGES_ORDERBOOK_AUTH_REQUIRED = ["okx"]

# Coins to track
COINS = ["btc", "eth", "sol", "xrp"]

# Symbol mapping: coin -> exchange symbol (USDT pairs)
SYMBOLS = {
    "btc": "BTC/USDT",
    "eth": "ETH/USDT",
    "sol": "SOL/USDT",
    "xrp": "XRP/USDT",
}


def get_symbol_for_exchange(exchange_id: str, coin: str) -> str:
    """Get the correct trading symbol for a given exchange and coin."""
    return SYMBOLS.get(coin, f"{coin.upper()}/USDT")


# =============================================================================
# Redis Configuration
# =============================================================================

REDIS_CONFIG = {
    "host": os.getenv("REDIS_HOST", "localhost"),
    "port": int(os.getenv("REDIS_PORT", 6379)),
    "db": int(os.getenv("REDIS_DB", 0)),
}

# Redis key patterns
REDIS_KEY_PRICE = "exchange_price:{exchange}:{coin}"
REDIS_KEY_ORDERBOOK = "exchange_orderbook:{exchange}:{coin}"
REDIS_KEY_CURRENT_CANDLE = "current_candle:{exchange}:{coin}_{timeframe}"
REDIS_TTL_SECONDS = 30

# Tick-level data keys
REDIS_KEY_TICK_PRICE = "tick_price:{exchange}:{coin}"
REDIS_KEY_TICK_VWAP = "tick_vwap:{exchange}:{coin}"
REDIS_KEY_TICK_CANDLE = "tick_candle:{exchange}:{coin}_{timeframe}"
REDIS_TTL_TICK = 10

# =============================================================================
# Stream Configuration
# =============================================================================

ORDERBOOK_LIMIT = 50

ORDERBOOK_LIMITS_BY_EXCHANGE = {
    # "kraken": 25,
}


def get_orderbook_limit_for_exchange(exchange_id: str) -> int:
    """Get the correct orderbook limit for a given exchange."""
    return ORDERBOOK_LIMITS_BY_EXCHANGE.get(exchange_id, ORDERBOOK_LIMIT)

# Reconnection settings
RECONNECT_DELAY_SECONDS = 5
MAX_RECONNECT_ATTEMPTS = 100

# Stats logging interval (seconds)
STATS_LOG_INTERVAL = 60

# =============================================================================
# Tick-Level Trades Configuration
# =============================================================================

# Disabled initially - enable later if needed
ENABLE_TICK_TRADES = False

# Throttle interval for Redis writes (milliseconds)
TICK_THROTTLE_MS = 50

# =============================================================================
# Logging Configuration
# =============================================================================

LOG_LEVEL = "INFO"
