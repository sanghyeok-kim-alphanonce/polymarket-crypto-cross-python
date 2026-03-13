"""
P2: Binance WebSocket Service - 설정
"""
import os

# === 코인 설정 ===
COINS = ['btc', 'eth', 'sol', 'xrp']
TIMEFRAMES = ['5m', '15m']

# === Binance 설정 ===
BINANCE_SYMBOLS = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
}
BINANCE_WS_URL = "wss://stream.binance.com:9443/stream?streams="

# === DB 설정 ===
DB_HOST = os.getenv("DB_HOST", "paper_trade_db")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "paper_trade")
DB_USER = os.getenv("DB_USER", "paper")
DB_PASSWORD = os.getenv("DB_PASSWORD", "papertrade")

# === Redis 설정 ===
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# === 수집 간격 ===
DB_FLUSH_INTERVAL = 5
STATS_INTERVAL = 60

# === Crossing 설정 ===
# "agg" = aggTrade 사용 (빠름, 권장)
# "mini" = miniTicker 사용 (느림, will be deprecated)
# "both" = 둘 다 발행 (ch:crossing:agg:*, ch:crossing:mini:*)
CROSSING_SOURCE = "agg"
