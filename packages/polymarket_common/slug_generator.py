"""Slug 생성 유틸리티"""
from datetime import datetime
from typing import Literal

from .time_utils import ET

# 타임프레임 -> 초 매핑
TIMEFRAME_SECONDS = {
    "5m": 300,       # 5분
    "15m": 900,      # 15분
    "1h": 3600,      # 1시간
    "4h": 14400,     # 4시간
}

# 지원하는 코인
SUPPORTED_COINS = ["btc", "eth", "sol", "xrp"]

# 1시간봉 코인 이름 매핑
COIN_1H_NAMES = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
    "xrp": "xrp",
}

Timeframe = Literal["5m", "15m", "1h", "4h"]
Coin = Literal["btc", "eth", "sol", "xrp"]


def get_current_candle_timestamp(timeframe: Timeframe) -> int:
    """현재 시간에 해당하는 캔들의 시작 timestamp 계산 (ET 기준)"""
    now_et = datetime.now(ET)

    if timeframe == "5m":
        minute = (now_et.minute // 5) * 5
        candle_time = now_et.replace(minute=minute, second=0, microsecond=0)
    elif timeframe == "15m":
        minute = (now_et.minute // 15) * 15
        candle_time = now_et.replace(minute=minute, second=0, microsecond=0)
    elif timeframe == "1h":
        candle_time = now_et.replace(minute=0, second=0, microsecond=0)
    elif timeframe == "4h":
        hour = (now_et.hour // 4) * 4
        candle_time = now_et.replace(hour=hour, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"지원하지 않는 타임프레임: {timeframe}")

    return int(candle_time.timestamp())


def get_candle_start_for_timestamp(timeframe: Timeframe, timestamp: int) -> int:
    """주어진 timestamp가 속하는 캔들의 시작 timestamp 계산 (ET 기준)"""
    dt_et = datetime.fromtimestamp(timestamp, tz=ET)

    if timeframe == "5m":
        minute = (dt_et.minute // 5) * 5
        candle_time = dt_et.replace(minute=minute, second=0, microsecond=0)
    elif timeframe == "15m":
        minute = (dt_et.minute // 15) * 15
        candle_time = dt_et.replace(minute=minute, second=0, microsecond=0)
    elif timeframe == "1h":
        candle_time = dt_et.replace(minute=0, second=0, microsecond=0)
    elif timeframe == "4h":
        hour = (dt_et.hour // 4) * 4
        candle_time = dt_et.replace(hour=hour, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"지원하지 않는 타임프레임: {timeframe}")

    return int(candle_time.timestamp())


def generate_slug(coin: Coin, timeframe: Timeframe, timestamp: int | None = None) -> str:
    """
    마켓 slug 생성

    Args:
        coin: 코인 이름 ("btc", "eth", "sol", "xrp")
        timeframe: 타임프레임 ("15m", "1h", "4h")
        timestamp: Unix timestamp (None이면 현재 캔들)

    Returns:
        slug (예: "eth-updown-15m-1763618400" or "xrp-up-or-down-november-20-12am-et")
    """
    if coin not in SUPPORTED_COINS:
        raise ValueError(f"지원하지 않는 코인: {coin}. 지원: {SUPPORTED_COINS}")

    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError(f"지원하지 않는 타임프레임: {timeframe}. 지원: {list(TIMEFRAME_SECONDS.keys())}")

    if timestamp is None:
        timestamp = get_current_candle_timestamp(timeframe)

    # 1시간봉은 다른 패턴 사용: bitcoin-up-or-down-march-26-2026-2am-et
    if timeframe == "1h":
        dt_et = datetime.fromtimestamp(timestamp, tz=ET)
        month = dt_et.strftime("%B").lower()
        day = dt_et.day
        year = dt_et.year
        hour = dt_et.hour
        ampm = "am" if hour < 12 else "pm"
        hour_12 = hour if hour <= 12 else hour - 12
        if hour_12 == 0:
            hour_12 = 12

        coin_name = COIN_1H_NAMES[coin]
        return f"{coin_name}-up-or-down-{month}-{day}-{year}-{hour_12}{ampm}-et"

    # 15분봉, 4시간봉은 기존 패턴
    return f"{coin}-updown-{timeframe}-{timestamp}"


def generate_current_slugs(coin: Coin) -> dict[Timeframe, str]:
    """특정 코인의 현재 시간 기준 모든 타임프레임 slug 생성"""
    return {
        "5m": generate_slug(coin, "5m"),
        "15m": generate_slug(coin, "15m"),
        "1h": generate_slug(coin, "1h"),
        "4h": generate_slug(coin, "4h"),
    }
