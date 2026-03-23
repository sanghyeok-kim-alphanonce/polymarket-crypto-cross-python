"""
Latency Monitor - 설정
Binance WS 직접 연결 vs Redis 구독 지연시간 비교
"""
import os

# === Redis 설정 ===
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# === Telegram 설정 ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID", "")

# === Binance 설정 ===
BINANCE_WS_URL = "wss://stream.binance.com:9443/stream?streams="
BINANCE_SYMBOLS = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
}
COINS = ['btc', 'eth', 'sol', 'xrp']
TIMEFRAMES = ['5m', '15m']

# === 코인/타임프레임 ===
COINS = ['btc', 'eth', 'sol', 'xrp']
TIMEFRAMES = ['5m', '15m']
