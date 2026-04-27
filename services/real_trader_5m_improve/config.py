"""
Real Trader 5M Improve: 5분봉 Crossing 전략 (Max3 + 속도필터 + 헤지 + 책 깊이 가드)

- 진입 구간 (0~250초): 첫 진입 x, 이후 2x
- 헤지 구간 (250~280초): x로 헤지
- 금지 구간 (280~300초): 진입 안 함
- Max3: 2번 진입 후 3번째는 x로 헤지 (이후 종료)
- 속도 필터: 20초 내 3회 crossing → x로 헤지
- GTC 0.80 고정
- 거래 후 1초 cooltime
- 책 깊이 가드: best_ask <= GTC AND best_ask_size * SAFETY >= intended.
  실패 시 그 시그널은 pending 상태로 보관, 책 회복 tick에서 재시도.
"""
import os

# === 전략 설정 ===
STRATEGY_NAME = os.getenv("STRATEGY_NAME", "real_trade_5m_improve")

# 코인/타임프레임 (BTC only, 5분봉)
COINS = ["btc"]
TIMEFRAMES = ["5m"]

# === Crossing 전략 파라미터 ===
CROSSING_BET_CONTRACT_UNIT = 15      # 기본 단위: 1회차=15, 2회차=30, 3회차(hedge)=15 (1.5x scale of 10/20/10 design)
CROSSING_MAX_COUNT = 3               # 최대 진입 횟수 (3번째는 hedge)
COOLTIME_SECONDS = 1.0               # 거래 후 cooltime (1초)

# 진입 시간 설정
CROSSING_MIN_ELAPSED_SECONDS = 0     # 시작부터 진입
CROSSING_ENTRY_CUTOFF_SECONDS = 250  # 진입 구간 종료 (0~250초: 정상 진입)
CROSSING_HEDGE_CUTOFF_SECONDS = 280  # 헤지 구간 종료 (250~280초: 헤지만 가능)
CROSSING_CUTOFF_SECONDS = 300        # 금지 구간 (280초 이후 진입 금지)

# 속도 필터 설정
SPEED_FILTER_WINDOW_SECONDS = 20     # 20초 내
SPEED_FILTER_MAX_CROSSINGS = 3       # 3회 이상이면 헤지

# === GTC 주문 설정 ===
GTC_FIXED_PRICE = 0.80               # 고정 GTC 가격

# === 책 깊이 가드 (Polymarket 매칭 보호) ===
# Polymarket: best level에 우리 수량만큼 opposite 유동성이 없으면 GTC가 매칭 안 되거나 취소될 수 있음.
# best_ask <= GTC_FIXED_PRICE AND best_ask_size * BOOK_DEPTH_SAFETY >= intended_contracts 일 때만 발사.
BOOK_DEPTH_REQUIRED = True           # 메인 킬스위치 (False면 가드 OFF, 기존 동작)
BOOK_DEPTH_SAFETY = 0.7              # available × SAFETY ≥ intended 일 때 통과 (0.7 = 30% 마진)
BOOK_DEPTH_PENDING_ENABLED = True    # 책 부족 시 pending 보관 후 책 회복 tick에서 재시도

# === 구조화 로깅 (book_depth_skip / recovered_fire / expired / overridden 등) ===
# JSON-Lines. 분석 스크립트가 후속 분석에 사용.
SIGNALS_LOG_PATH = os.getenv("SIGNALS_LOG_PATH", "/app/logs/book_depth_signals.jsonl")

# === Polymarket CLOB 설정 ===
# 2026-04-28 ~11:00 UTC cutover로 V1→V2 전환 (같은 URL이 V2 backend로 교체).
# 4/28 이전에 V2 미리 검증하려면 POLYMARKET_HOST=https://clob-v2.polymarket.com.
POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
# Builder 프로그램 등록한 경우만. 미설정 시 attribution 없이 거래 (BYTES32_ZERO).
POLYMARKET_BUILDER_CODE = os.getenv("POLYMARKET_BUILDER_CODE", "")

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
