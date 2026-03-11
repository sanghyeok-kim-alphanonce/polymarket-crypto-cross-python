#!/usr/bin/env python3
"""
5분 내 crossing 존재 시 15분봉 방향 예측 vs 단순 5분 방향 예측 비교

분석 목표:
1. 5분 내에 crossing이 존재할 때, 마지막 crossing 방향대로 15분봉이 끝날 확률
2. 단순히 5분 시점 방향이 15분봉 양봉/음봉으로 끝날 확률
"""

import zipfile
import csv
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
import io


def load_1s_data_from_zip(zip_path: Path) -> list[tuple[int, float]]:
    """zip에서 1초 kline 로드 - (timestamp_ms, close) 리스트"""
    ticks = []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for name in zf.namelist():
            if name.endswith('.csv'):
                with zf.open(name) as f:
                    reader = csv.reader(io.TextIOWrapper(f, encoding='utf-8'))
                    for row in reader:
                        # open_time (microseconds), open, high, low, close, ...
                        ts_us = int(row[0])
                        ts_ms = ts_us // 1000
                        close = float(row[4])
                        ticks.append((ts_ms, close))
    return sorted(ticks, key=lambda x: x[0])


def get_15m_candle_key(ts_ms: int) -> str:
    """timestamp를 15분봉 키로 변환"""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    minute_slot = (dt.minute // 15) * 15
    return dt.strftime(f"%Y-%m-%d_{dt.hour:02d}-{minute_slot:02d}")


def analyze_candle(ticks: list[tuple[int, float]]) -> dict:
    """
    하나의 15분봉 분석

    Returns:
        {
            "open": float,
            "close": float,
            "is_bullish": bool,  # 양봉 여부
            "crossings_5min": list,  # 5분 내 crossing 리스트 [{sec, dir}]
            "last_crossing_dir_5min": str | None,  # 5분 내 마지막 crossing 방향
            "price_at_5min": float | None,  # 5분 시점 가격
            "direction_at_5min": str | None,  # 5분 시점 위치 (above/below)
        }
    """
    if len(ticks) < 2:
        return None

    candle_start_ms = ticks[0][0]
    open_price = ticks[0][1]
    close_price = ticks[-1][1]
    is_bullish = close_price > open_price

    crossings_5min = []
    zone = "at"  # 시작 시점은 open price 위치 (at)

    price_at_5min = None
    direction_at_5min = None

    for ts_ms, price in ticks:
        elapsed_sec = (ts_ms - candle_start_ms) / 1000

        # 현재 영역 판단
        if price > open_price:
            curr_zone = "above"
        elif price < open_price:
            curr_zone = "below"
        else:
            curr_zone = "at"

        # 5분(300초) 이내 crossing 감지
        # at→above = up, at→below = down (시작가 crossing 포함)
        if elapsed_sec <= 300:
            if zone in ("below", "at") and curr_zone == "above":
                crossings_5min.append({"sec": elapsed_sec, "dir": "up"})
            elif zone in ("above", "at") and curr_zone == "below":
                crossings_5min.append({"sec": elapsed_sec, "dir": "down"})

        # 5분 시점 기록 (가장 가까운 시점)
        if 299 <= elapsed_sec <= 301:
            price_at_5min = price
            if price > open_price:
                direction_at_5min = "above"
            elif price < open_price:
                direction_at_5min = "below"
            else:
                direction_at_5min = "at"

        # 영역 업데이트
        if curr_zone != "at":
            zone = curr_zone

    last_crossing_dir = crossings_5min[-1]["dir"] if crossings_5min else None

    return {
        "open": open_price,
        "close": close_price,
        "is_bullish": is_bullish,
        "crossings_5min": crossings_5min,
        "last_crossing_dir_5min": last_crossing_dir,
        "price_at_5min": price_at_5min,
        "direction_at_5min": direction_at_5min,
    }


def main():
    data_dir = Path(__file__).parent.parent / "backtest_1y/data/BTCUSDT/1s"

    # 모든 zip 파일 로드
    zip_files = sorted(data_dir.glob("*.zip"))
    print(f"총 {len(zip_files)}개 일별 데이터 파일")

    # 15분봉별로 tick 모으기
    candles_data = defaultdict(list)

    for i, zip_path in enumerate(zip_files):
        if (i + 1) % 30 == 0:
            print(f"  처리 중: {i+1}/{len(zip_files)} - {zip_path.name}")

        ticks = load_1s_data_from_zip(zip_path)
        for ts_ms, close in ticks:
            candle_key = get_15m_candle_key(ts_ms)
            candles_data[candle_key].append((ts_ms, close))

    print(f"총 {len(candles_data)}개 15분봉")

    # 각 캔들 분석
    results = []
    for candle_key, ticks in sorted(candles_data.items()):
        if len(ticks) < 800:  # 최소 800초 이상 데이터 (불완전한 캔들 제외)
            continue

        analysis = analyze_candle(ticks)
        if analysis:
            analysis["candle_key"] = candle_key
            results.append(analysis)

    print(f"분석 대상: {len(results)}개 완전한 15분봉")
    print()

    # ========== 분석 1: 5분 내 crossing 존재 시 예측력 ==========
    print("=" * 70)
    print("분석 1: 5분 내 crossing이 존재할 때, 마지막 crossing 방향 → 15분봉 방향")
    print("=" * 70)

    # crossing 있는 캔들만 필터
    with_crossing = [r for r in results if r["last_crossing_dir_5min"]]

    # 마지막 crossing 방향 = 15분봉 방향 일치율
    crossing_up_bullish = sum(1 for r in with_crossing if r["last_crossing_dir_5min"] == "up" and r["is_bullish"])
    crossing_up_total = sum(1 for r in with_crossing if r["last_crossing_dir_5min"] == "up")
    crossing_down_bearish = sum(1 for r in with_crossing if r["last_crossing_dir_5min"] == "down" and not r["is_bullish"])
    crossing_down_total = sum(1 for r in with_crossing if r["last_crossing_dir_5min"] == "down")

    correct_predictions = crossing_up_bullish + crossing_down_bearish
    total_predictions = len(with_crossing)

    print(f"5분 내 crossing 있는 캔들: {total_predictions}개 ({total_predictions/len(results)*100:.1f}%)")
    print()
    print(f"마지막 crossing UP → 양봉: {crossing_up_bullish}/{crossing_up_total} ({crossing_up_bullish/crossing_up_total*100:.1f}%)" if crossing_up_total else "")
    print(f"마지막 crossing DOWN → 음봉: {crossing_down_bearish}/{crossing_down_total} ({crossing_down_bearish/crossing_down_total*100:.1f}%)" if crossing_down_total else "")
    print()
    print(f"★ 전체 적중률: {correct_predictions}/{total_predictions} ({correct_predictions/total_predictions*100:.1f}%)")
    print()

    # ========== 분석 2: 5분 시점 방향 → 15분봉 방향 ==========
    print("=" * 70)
    print("분석 2: 5분 시점 가격 위치 → 15분봉 방향 (crossing 무관)")
    print("=" * 70)

    # 5분 시점 데이터 있는 캔들
    with_5min_data = [r for r in results if r["direction_at_5min"] and r["direction_at_5min"] != "at"]

    at_5min_above_bullish = sum(1 for r in with_5min_data if r["direction_at_5min"] == "above" and r["is_bullish"])
    at_5min_above_total = sum(1 for r in with_5min_data if r["direction_at_5min"] == "above")
    at_5min_below_bearish = sum(1 for r in with_5min_data if r["direction_at_5min"] == "below" and not r["is_bullish"])
    at_5min_below_total = sum(1 for r in with_5min_data if r["direction_at_5min"] == "below")

    correct_5min = at_5min_above_bullish + at_5min_below_bearish
    total_5min = len(with_5min_data)

    print(f"5분 데이터 있는 캔들: {total_5min}개")
    print()
    print(f"5분 시점 above → 양봉: {at_5min_above_bullish}/{at_5min_above_total} ({at_5min_above_bullish/at_5min_above_total*100:.1f}%)" if at_5min_above_total else "")
    print(f"5분 시점 below → 음봉: {at_5min_below_bearish}/{at_5min_below_total} ({at_5min_below_bearish/at_5min_below_total*100:.1f}%)" if at_5min_below_total else "")
    print()
    print(f"★ 전체 적중률: {correct_5min}/{total_5min} ({correct_5min/total_5min*100:.1f}%)")
    print()

    # ========== 분석 3: 비교 ==========
    print("=" * 70)
    print("비교 분석")
    print("=" * 70)

    print(f"5분 내 crossing 방향 예측력: {correct_predictions/total_predictions*100:.2f}%")
    print(f"5분 시점 위치 예측력:        {correct_5min/total_5min*100:.2f}%")
    diff = correct_predictions/total_predictions*100 - correct_5min/total_5min*100
    print(f"차이: {'+' if diff > 0 else ''}{diff:.2f}%p")
    print()

    # ========== 추가 분석: crossing 횟수별 ==========
    print("=" * 70)
    print("추가: 5분 내 crossing 횟수별 적중률")
    print("=" * 70)

    from collections import Counter
    crossing_counts = Counter(len(r["crossings_5min"]) for r in results)

    for count in sorted(crossing_counts.keys()):
        subset = [r for r in results if len(r["crossings_5min"]) == count and r["last_crossing_dir_5min"]]
        if not subset:
            n_candles = sum(1 for r in results if len(r["crossings_5min"]) == count)
            print(f"  {count}회: {n_candles}개 캔들 (crossing 없음)")
            continue

        correct = sum(1 for r in subset
                     if (r["last_crossing_dir_5min"] == "up" and r["is_bullish"]) or
                        (r["last_crossing_dir_5min"] == "down" and not r["is_bullish"]))
        print(f"  {count}회: {len(subset)}개 캔들, 적중 {correct}/{len(subset)} ({correct/len(subset)*100:.1f}%)")


if __name__ == "__main__":
    main()
