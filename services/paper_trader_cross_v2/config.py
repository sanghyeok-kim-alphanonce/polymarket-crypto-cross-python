"""
P4-Cross-V2: Crossing Strategy Paper Trader - 설정
V2: 횟수 제한 없음, 14분까지 베팅
"""
import os

# === 전략 설정 ===
STRATEGY_NAME = "crossing_v2"

# 코인/타임프레임 (15분봉만)
COINS = ["btc"]
TIMEFRAMES = ["15m"]

# === Crossing V2 전략 파라미터 ===
CROSSING_FIRST_BET = 10.0            # 첫 crossing 베팅 금액
CROSSING_SUBSEQUENT_BET = 20.0       # 이후 crossing 베팅 금액 (무제한)
CROSSING_CUTOFF_SECONDS = 840        # 진입 마감 (14분)
CROSSING_MIN_ENTRY_PRICE = 0.10      # 최소 진입 가격
CROSSING_MAX_ENTRY_PRICE = 0.90      # 최대 진입 가격

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
STATS_INTERVAL = 60

# === Orderbook cache TTL (seconds) ===
OB_CACHE_TTL = 30

# Re-export for convenience
__all__ = [
    'STRATEGY_NAME', 'COINS', 'TIMEFRAMES',
    'CROSSING_FIRST_BET', 'CROSSING_SUBSEQUENT_BET',
    'CROSSING_CUTOFF_SECONDS',
    'CROSSING_MIN_ENTRY_PRICE', 'CROSSING_MAX_ENTRY_PRICE',
    'DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD',
    'REDIS_HOST', 'REDIS_PORT',
    'STATS_INTERVAL', 'OB_CACHE_TTL',
]
