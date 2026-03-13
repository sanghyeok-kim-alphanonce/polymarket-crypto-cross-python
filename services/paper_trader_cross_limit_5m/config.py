"""
Paper Trader Cross Limit 5M: Crossing 전략 (횟수 제한) - 5분봉용

- 5분봉 전체 crossing에 진입
- 1회차: UNIT (10)
- 2~9회차: 2*UNIT (20)
- 10회차: UNIT (10)
- 4분 50초 이후 → 바로 10회차
- Taker 체결 (best_ask)
"""
import os

# === 전략 설정 ===
STRATEGY_NAME = "paper_cross_limit_5m"

# 코인/타임프레임 (5분봉)
COINS = ["btc"]
TIMEFRAMES = ["5m"]

# === Crossing 전략 파라미터 ===
CROSSING_BET_CONTRACT_UNIT = 10      # 기본 단위: 1회차=10, 2-9회차=20, 10회차=10
CROSSING_MAX_COUNT = 10              # 최대 진입 횟수
CROSSING_MIN_ELAPSED_SECONDS = 0     # 진입 시작 (0초부터)
CROSSING_CUTOFF_SECONDS = 300        # 진입 마감 (5분)
CROSSING_LATE_ENTRY_SECONDS = 290    # 4분 50초 이후 → 바로 10회차

# === 진입 가격 제한 ===
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

STATS_INTERVAL = 60
OB_CACHE_TTL = 30
