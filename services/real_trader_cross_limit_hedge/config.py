"""
Real Trader Cross Limit: Crossing 전략 (횟수 제한)

- 5분 이후 crossing에만 진입 (0-5분 스킵)
- 1회차: UNIT (10)
- 2~9회차: 2*UNIT (20)
- 10회차: UNIT (10)
- 14분 30초 이후 → 바로 10회차
- GTC 고정 0.70
"""
import os

# === 전략 설정 ===
STRATEGY_NAME = "real_trade_cross_limit_hedge"

# 코인/타임프레임 (BTC only, 15분봉만)
COINS = ["btc"]
TIMEFRAMES = ["15m"]

# === Crossing 전략 파라미터 ===
CROSSING_BET_CONTRACT_UNIT = 10      # 기본 단위: 1회차=10, 2-9회차=20, 10회차=10
CROSSING_MAX_COUNT = 10              # 최대 진입 횟수
CROSSING_MIN_ELAPSED_SECONDS = 300   # 진입 시작 (5분부터)
CROSSING_CUTOFF_SECONDS = 900        # 진입 마감 (15분)
CROSSING_LATE_ENTRY_SECONDS = 870    # 14분 30초 이후 → 바로 10회차

# === GTC 주문 설정 ===
GTC_FIXED_PRICE = 0.90               # 1-10회차 주문 가격

def get_gtc_price(elapsed_seconds: int) -> float:
    """GTC 가격 반환 (고정 0.70)"""
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
