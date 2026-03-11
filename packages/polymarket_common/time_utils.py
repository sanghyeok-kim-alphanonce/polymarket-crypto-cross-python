"""시간 변환 유틸리티"""
import time
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Union
from zoneinfo import ZoneInfo

# 타임존 정의
UTC = timezone.utc
ET = ZoneInfo("America/New_York")  # Eastern Time (자동으로 EST/EDT 처리)
KST = ZoneInfo("Asia/Seoul")  # 한국 시간


class TimeConverter:
    """시간 변환 클래스"""

    @staticmethod
    def timestamp_to_datetime(ts: Union[int, float], tz=UTC) -> datetime:
        """타임스탬프 -> datetime (밀리초 자동 처리)"""
        if ts > 10000000000:  # 밀리초인 경우
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=tz)

    @staticmethod
    def datetime_to_timestamp(dt: datetime, milliseconds=False) -> int:
        """datetime -> 타임스탬프"""
        ts = int(dt.timestamp())
        return ts * 1000 if milliseconds else ts

    @staticmethod
    def to_utc(dt: datetime) -> datetime:
        return dt.astimezone(UTC)

    @staticmethod
    def to_et(dt: datetime) -> datetime:
        return dt.astimezone(ET)

    @staticmethod
    def to_kst(dt: datetime) -> datetime:
        return dt.astimezone(KST)

    @staticmethod
    def format_multi_tz(dt: datetime) -> dict:
        """모든 타임존으로 변환"""
        return {
            "utc": dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "et": dt.astimezone(ET).strftime("%Y-%m-%d %I:%M %p %Z"),
            "kst": dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "timestamp": int(dt.timestamp()),
            "timestamp_ms": int(dt.timestamp() * 1000),
        }

    @staticmethod
    def now_all_tz() -> dict:
        now = datetime.now(UTC)
        return TimeConverter.format_multi_tz(now)


# 간편 함수들
def timestamp_to_utc(ts: Union[int, float]) -> datetime:
    return TimeConverter.timestamp_to_datetime(ts, tz=UTC)

def timestamp_to_et(ts: Union[int, float]) -> datetime:
    return TimeConverter.timestamp_to_datetime(ts, tz=ET)

def timestamp_to_kst(ts: Union[int, float]) -> datetime:
    return TimeConverter.timestamp_to_datetime(ts, tz=KST)

def format_timestamp(ts: Union[int, float]) -> dict:
    dt = timestamp_to_utc(ts)
    return TimeConverter.format_multi_tz(dt)


# ============================================================
# 캔들 경계 유틸리티 (timestamp 기반)
# ============================================================

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def get_seconds_to_next_candle(timeframe: str) -> float:
    """다음 캔들까지 남은 초 계산"""
    seconds = TIMEFRAME_SECONDS[timeframe]
    now_ts = time.time()
    current_start = (int(now_ts) // seconds) * seconds
    next_start = current_start + seconds
    return next_start - now_ts


def get_current_candle_start(timeframe: str) -> float:
    """현재 캔들의 시작 timestamp 반환"""
    seconds = TIMEFRAME_SECONDS[timeframe]
    now_ts = time.time()
    return (int(now_ts) // seconds) * seconds


def get_next_candle_start(timeframe: str) -> float:
    """다음 캔들의 시작 timestamp 반환"""
    return get_current_candle_start(timeframe) + TIMEFRAME_SECONDS[timeframe]


def sleep_until_next_candle(timeframe: str, margin: float = 0.5) -> float:
    """다음 캔들 경계까지 정확히 sleep (sync)"""
    sleep_seconds = get_seconds_to_next_candle(timeframe) + margin
    time.sleep(sleep_seconds)
    return sleep_seconds


async def async_sleep_until_next_candle(timeframe: str, margin: float = 0.5) -> float:
    """다음 캔들 경계까지 정확히 sleep (async)"""
    sleep_seconds = get_seconds_to_next_candle(timeframe) + margin
    await asyncio.sleep(sleep_seconds)
    return sleep_seconds


# ============================================================
# datetime 기반 캔들 유틸리티 (binance 서비스 호환용)
# ============================================================

DURATION_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


def get_candle_start_datetime(duration_minutes: int, now: datetime) -> datetime:
    """타임프레임별 현재 캔들 시작 시간 계산 (datetime 반환)"""
    start = now.replace(second=0, microsecond=0)
    if duration_minutes == 240:  # 4h (1시 기준 시작)
        h = start.hour
        back = ((h - 1) % 24) % 4
        start = start - timedelta(hours=back, minutes=start.minute)
    elif duration_minutes >= 60:  # 1h 이상
        hours = duration_minutes // 60
        h = start.hour
        back = h % hours
        start = start - timedelta(hours=back, minutes=start.minute)
    else:  # 분 단위
        start = start - timedelta(minutes=start.minute % duration_minutes)
    return start


def get_next_candle_datetime(duration_minutes: int, now: datetime) -> datetime:
    """다음 캔들 시작 시간 계산 (datetime 반환)"""
    current = get_candle_start_datetime(duration_minutes, now)
    return current + timedelta(minutes=duration_minutes)
