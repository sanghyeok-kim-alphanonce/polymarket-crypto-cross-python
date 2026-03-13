"""
공유 전략 설정
paper_trader와 backtest_api가 함께 사용
"""
import os

# === Strategy 설정 ===
STRATEGY_NAME = os.getenv("STRATEGY_NAME", "v12_5")

# === 코인 설정 ===
COINS = ['btc']  # Paper trading은 BTC만
TIMEFRAMES = ['15m']

# === V12-5 전략 파라미터 ===
V12_5_MIN_ENTRY_PRICE = float(os.getenv("V12_5_MIN_ENTRY_PRICE", 0.35))
V12_5_MIN_ENTRY_PRICE_LATE = float(os.getenv("V12_5_MIN_ENTRY_PRICE_LATE", 0.55))
V12_5_EARLY_CUTOFF_MINUTES = int(os.getenv("V12_5_EARLY_CUTOFF_MINUTES", 10))
V12_5_COOLDOWN_SECONDS = float(os.getenv("V12_5_COOLDOWN_SECONDS", 5.0))
V12_5_MAX_SPREAD = float(os.getenv("V12_5_MAX_SPREAD", 0.05))

# === V12-5 Threshold ($ 단위, Binance 가격 delta 기준) ===
# 전반 (<=10분): 높은 threshold
V12_5_THRESHOLDS_EARLY = {
    'btc': 30.0,   # >= $30
    'eth': 1.0,    # >= $1.0
    'sol': 0.03,   # >= $0.03
    'xrp': 0.0005, # >= $0.0005
}
# 후반 (>10분): 낮은 threshold
V12_5_THRESHOLDS_LATE = {
    'btc': 1.0,    # >= $1
    'eth': 0.1,    # >= $0.1
    'sol': 0.02,   # >= $0.02
    'xrp': 0.0001, # >= $0.0001
}

# === V12-6 전략 파라미터 (Momentum Match 추가) ===
V12_6_MIN_ENTRY_PRICE = float(os.getenv("V12_6_MIN_ENTRY_PRICE", 0.35))
V12_6_MIN_ENTRY_PRICE_LATE = float(os.getenv("V12_6_MIN_ENTRY_PRICE_LATE", 0.55))
V12_6_EARLY_CUTOFF_MINUTES = int(os.getenv("V12_6_EARLY_CUTOFF_MINUTES", 10))
V12_6_COOLDOWN_SECONDS = float(os.getenv("V12_6_COOLDOWN_SECONDS", 5.0))
V12_6_MAX_SPREAD = float(os.getenv("V12_6_MAX_SPREAD", 0.05))
V12_6_REQUIRE_MOMENTUM_MATCH = os.getenv("V12_6_REQUIRE_MOMENTUM_MATCH", "true").lower() == "true"

# === V12-6 Threshold (momentum match 덕분에 더 낮은 threshold 가능) ===
V12_6_THRESHOLDS_EARLY = {
    'btc': 20.0,   # >= $20 (v12_5는 $30)
    'eth': 0.8,    # >= $0.8
    'sol': 0.025,  # >= $0.025
    'xrp': 0.0004, # >= $0.0004
}
V12_6_THRESHOLDS_LATE = {
    'btc': 1.0,    # >= $1
    'eth': 0.1,    # >= $0.1
    'sol': 0.02,   # >= $0.02
    'xrp': 0.0001, # >= $0.0001
}

# === 공통 배팅 설정 ===
BET_AMOUNT = float(os.getenv("BET_AMOUNT", 1.0))  # $ per trade
MIN_LIQUIDITY = int(os.getenv("MIN_LIQUIDITY", 10))  # 최소 유동성

# CONTRACTS는 deprecated, BET_AMOUNT 사용 권장
CONTRACTS = int(os.getenv("CONTRACTS", 10))

# Export all
__all__ = [
    'STRATEGY_NAME', 'COINS', 'TIMEFRAMES',
    'V12_5_MIN_ENTRY_PRICE', 'V12_5_MIN_ENTRY_PRICE_LATE',
    'V12_5_EARLY_CUTOFF_MINUTES', 'V12_5_COOLDOWN_SECONDS', 'V12_5_MAX_SPREAD',
    'V12_5_THRESHOLDS_EARLY', 'V12_5_THRESHOLDS_LATE',
    'V12_6_MIN_ENTRY_PRICE', 'V12_6_MIN_ENTRY_PRICE_LATE',
    'V12_6_EARLY_CUTOFF_MINUTES', 'V12_6_COOLDOWN_SECONDS', 'V12_6_MAX_SPREAD',
    'V12_6_REQUIRE_MOMENTUM_MATCH',
    'V12_6_THRESHOLDS_EARLY', 'V12_6_THRESHOLDS_LATE',
    'BET_AMOUNT', 'MIN_LIQUIDITY', 'CONTRACTS',
]
