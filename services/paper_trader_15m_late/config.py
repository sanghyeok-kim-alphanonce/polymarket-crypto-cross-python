"""
Paper Trader 15M Late: 15분봉 후반(11분~) Crossing 전략

STRATEGY.md 최적 전략:
- 진입: 660초(11분) ~ 880초(14분40초) - 첫 x, 이후 2x
- 헤지: 880초 ~ 890초 - x로 헤지
- 금지: 890초 이후
- Max K: 5 (5번째는 x로 헤지)
- 속도 필터: 20초 내 3회 → x로 헤지
"""
import os

# === 전략 설정 ===
STRATEGY_NAME = "paper_15m_late"

# 코인/타임프레임 (15분봉)
COINS = ["btc"]
TIMEFRAMES = ["15m"]

# === Crossing 전략 파라미터 ===
CROSSING_BET_CONTRACT_UNIT = 10      # 기본 단위: 1회차=10, 2~4회차=20, 5회차=10
CROSSING_MAX_COUNT = 5               # 최대 진입 횟수

# 진입 시간 설정 (STRATEGY.md 15분봉 최적)
CROSSING_MIN_ELAPSED_SECONDS = 660   # 11분(660초)부터 시작
CROSSING_ENTRY_CUTOFF_SECONDS = 880  # 진입 구간 종료 (14분40초)
CROSSING_HEDGE_CUTOFF_SECONDS = 890  # 헤지 구간 종료 (14분50초)
CROSSING_CUTOFF_SECONDS = 900        # 캔들 종료 (15분)

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
