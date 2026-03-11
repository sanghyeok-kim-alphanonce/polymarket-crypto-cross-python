"""
Real Trader Cross: 13분 이후 첫 Crossing 전략

- 13분 이후 첫 crossing에만 진입
- 고정 금액 GTC 주문
"""
import os

# === 전략 설정 ===
STRATEGY_NAME = "real_crossing_v2"

# 코인/타임프레임 (BTC only, 15분봉만)
COINS = ["btc"]
TIMEFRAMES = ["15m"]

# === Crossing 전략 파라미터 ===
CROSSING_BET_SIZE = 10               # 고정 베팅 금액 ($)
CROSSING_MIN_ELAPSED_SECONDS = 810   # 진입 시작 (13분 30초 이후)
CROSSING_CUTOFF_SECONDS = 870        # 진입 마감 (14분 30초까지)
CROSSING_MAX_ENTRIES = 10            # 캔들당 최대 진입 횟수

# === Hedge GTC 설정 (10회 채우면 양쪽에 저가 주문) ===
HEDGE_ENABLED = True
HEDGE_GTC_PRICE = 0.40               # 저가 GTC 가격
HEDGE_GTC_SIZE = 4                   # 각 side당 수량

# === GTC 주문 설정 ===
# GTC 가격: 시간대별 승률 기반 동적 설정
# 승률 = GTC 가격 (손익분기)
GTC_PRICE_BY_ELAPSED = {
    810: 0.63,  # 13m30s - 승률 62.6%
    820: 0.63,  # 13m40s
    830: 0.63,  # 13m50s
    840: 0.64,  # 14m00s - 승률 64.5%
    850: 0.66,  # 14m10s - 승률 66.4%
    860: 0.67,  # 14m20s - 승률 67.0%
    870: 0.70,  # 14m30s - 승률 69.5%
    880: 0.74,  # 14m40s - 승률 74.0%
    890: 0.80,  # 14m50s - 승률 79.9%
}

def get_gtc_price(elapsed_seconds: int) -> float:
    """경과 시간에 따른 GTC 가격 반환"""
    # 10초 단위로 내림
    sec_key = (elapsed_seconds // 10) * 10
    return GTC_PRICE_BY_ELAPSED.get(sec_key, 0.70)

# === Polymarket CLOB 설정 ===
POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")

# === Telegram 설정 ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

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
