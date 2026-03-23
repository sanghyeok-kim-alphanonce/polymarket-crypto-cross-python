"""
Real Trader 5M Improve: 5분봉 Crossing 전략 (Max5 + 속도필터 + 헤지)

- 진입 구간 (0~250초): 첫 진입 x, 이후 2x
- 헤지 구간 (250~290초): x로 헤지
- 금지 구간 (290~300초): 진입 안 함
- Max5: 4번 진입 후 5번째는 x로 헤지
- 속도 필터: 20초 내 3회 crossing → x로 헤지
- GTC 0.80 고정
- 거래 후 1초 cooltime
"""
import os

# === 전략 설정 ===
STRATEGY_NAME = os.getenv("STRATEGY_NAME", "real_trade_5m_improve")

# 코인/타임프레임 (BTC only, 5분봉)
COINS = ["btc"]
TIMEFRAMES = ["5m"]

# === Crossing 전략 파라미터 ===
CROSSING_BET_CONTRACT_UNIT = 3       # 기본 단위: 1회차=3, 2~4회차=6, 5회차=3
CROSSING_MAX_COUNT = 5               # 최대 진입 횟수
COOLTIME_SECONDS = 1.0               # 거래 후 cooltime (1초)

# 진입 시간 설정
CROSSING_MIN_ELAPSED_SECONDS = 0     # 시작부터 진입
CROSSING_ENTRY_CUTOFF_SECONDS = 250  # 진입 구간 종료 (0~250초: 정상 진입)
CROSSING_HEDGE_CUTOFF_SECONDS = 290  # 헤지 구간 종료 (250~290초: 헤지만 가능)
CROSSING_CUTOFF_SECONDS = 300        # 금지 구간 (290초 이후 진입 금지)

# 속도 필터 설정
SPEED_FILTER_WINDOW_SECONDS = 20     # 20초 내
SPEED_FILTER_MAX_CROSSINGS = 3       # 3회 이상이면 헤지

# === GTC 주문 설정 ===
GTC_FIXED_PRICE = 0.80               # 고정 GTC 가격

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
