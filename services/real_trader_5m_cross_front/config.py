"""
Real Trader 5M Cross Front: 5분봉 전체 Crossing 전략

- 5분봉 전체 crossing에 진입 (0s ~ 300s)
- 10회 제한, 4분 50초 이후 → 바로 10회차
- 1회차: 10, 2~9회차: 20, 10회차: 10
- GTC 0.80
"""
import os

# === 전략 설정 ===
STRATEGY_NAME = os.getenv("STRATEGY_NAME", "real_trade_5m_cross_front")

# 코인/타임프레임 (BTC only, 5분봉)
COINS = ["btc"]
TIMEFRAMES = ["5m"]

# === Crossing 전략 파라미터 ===
CROSSING_BET_CONTRACT_UNIT = 10      # 기본 단위: 1회차=10, 2-9회차=20, 10회차=10
CROSSING_MAX_COUNT = 10              # 최대 진입 횟수

# 진입 시간 설정 (5분 전체)
CROSSING_MIN_ELAPSED_SECONDS = 0     # 시작부터 진입 가능
CROSSING_CUTOFF_SECONDS = 300        # 5분 전체
CROSSING_LATE_ENTRY_SECONDS = 290    # 4분 50초 이후 → 바로 10회차

# === GTC 주문 설정 ===
GTC_FIXED_PRICE = 0.80               # 주문 가격

def get_gtc_price(elapsed_seconds: int) -> float:
    """GTC 가격 반환 (고정)"""
    return GTC_FIXED_PRICE

# === Polymarket CLOB 설정 ===
POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")

# === Telegram 설정 ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID", "")

# === DB 설정 ===
DB_HOST = os.getenv("DB_HOST", "paper_trade_db")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "paper_trade")
DB_USER = os.getenv("DB_USER", "paper")
DB_PASSWORD = os.getenv("DB_PASSWORD", "papertrade")

# === Redis 설정 ===
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

STATS_INTERVAL = 60
