"""
P1: RTDS Service - 설정

Polymarket CLOB WebSocket + RTDS WebSocket + Chainlink REST
"""
import os

# === 코인 설정 ===
# 오더북: BTC만 수집 (Polymarket CLOB WS)
ORDERBOOK_COINS = ['btc']
# 가격: 4개 코인 모두 수집 (RTDS + Chainlink)
PRICE_COINS = ['btc', 'eth', 'sol', 'xrp']
# 하위 호환용 (deprecated)
COINS = ORDERBOOK_COINS
TIMEFRAMES = ['5m', '15m']

# === DB 설정 ===
DB_HOST = os.getenv("DB_HOST", "paper_trade_db")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "paper_trade")
DB_USER = os.getenv("DB_USER", "paper")
DB_PASSWORD = os.getenv("DB_PASSWORD", "papertrade")

# === Redis 설정 ===
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# === Polymarket 설정 ===
GAMMA_API = "https://gamma-api.polymarket.com"
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# === Polymarket RTDS 설정 (실시간 가격 - primary) ===
RTDS_WSS_URL = "wss://ws-live-data.polymarket.com"
RTDS_BINANCE_SYMBOLS = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
RTDS_CHAINLINK_SYMBOLS = ["btc/usd", "eth/usd", "sol/usd", "xrp/usd"]
RTDS_SYMBOL_MAP = {
    "btcusdt": "btc", "ethusdt": "eth", "solusdt": "sol", "xrpusdt": "xrp",
    "btc/usd": "btc", "eth/usd": "eth", "sol/usd": "sol", "xrp/usd": "xrp",
}
RTDS_RECONNECT_DELAY = 5

# === Chainlink Feed IDs ===
CHAINLINK_FEED_IDS = {
    "btc": "0x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8",
    "eth": "0x000359843a543ee2fe414dc14c7e7920ef10f4372990b79d6361cdc0dd1ba782",
    "sol": "0x0003b778d3f6b2ac4991302b89cb313f99a42467d6c9c5f96f57c29c0d2bc24f",
    "xrp": "0x0003c16c6aed42294f5cb4741f6e59ba2d728f0eae2eb9e6d3f555808c59fc45",
}
CHAINLINK_INTERVAL = 10

# === 수집 간격 ===
DB_FLUSH_INTERVAL = 5
STATS_INTERVAL = 60

# === DB 저장 설정 ===
SAVE_ORDERBOOK_TO_DB = False  # orderbook_books, orderbook_changes 저장 비활성화
