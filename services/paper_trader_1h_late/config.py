"""
Paper Trader 1H Late: 1시간봉 후반(56분~) Crossing 전략

STRATEGY.md 최적 전략:
- 진입: 3360초(56분) ~ 3590초(59분50초) - 첫 x, 이후 2x
- 헤지: 3590초 ~ 3600초 - x로 헤지
- Max K: 5 (5번째는 x로 헤지)
- 속도 필터: 20초 내 3회 → x로 헤지
"""
import os

# === 전략 설정 ===
STRATEGY_NAME = "paper_1h_late"

# 코인/타임프레임 (1시간봉)
COINS = ["btc"]
TIMEFRAMES = ["1h"]

# === Crossing 전략 파라미터 ===
CROSSING_BET_CONTRACT_UNIT = 10      # 기본 단위: 1회차=10, 2~4회차=20, 5회차=10
CROSSING_MAX_COUNT = 5               # 최대 진입 횟수

# 진입 시간 설정 (STRATEGY.md 1시간봉 최적)
CROSSING_MIN_ELAPSED_SECONDS = 3360  # 56분(3360초)부터 시작
CROSSING_ENTRY_CUTOFF_SECONDS = 3590 # 진입 구간 종료 (59분50초)
CROSSING_HEDGE_CUTOFF_SECONDS = 3600 # 헤지 구간 종료 (60분)
CROSSING_CUTOFF_SECONDS = 3600       # 캔들 종료 (60분)

# 속도 필터 설정
SPEED_FILTER_WINDOW_SECONDS = 20     # 20초 내
SPEED_FILTER_MAX_CROSSINGS = 3       # 3회 이상이면 헤지

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
OB_CACHE_TTL = 60
