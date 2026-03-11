#!/usr/bin/env python3
"""
0회 crossing 포함 분석 - 5분 시점 위치 기준
"""

import zipfile
import csv
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
import io


def load_1s_data_from_zip(zip_path: Path) -> list[tuple[int, float]]:
    ticks = []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for name in zf.namelist():
            if name.endswith('.csv'):
                with zf.open(name) as f:
                    reader = csv.reader(io.TextIOWrapper(f, encoding='utf-8'))
                    for row in reader:
                        ts_us = int(row[0])
                        ts_ms = ts_us // 1000
                        close = float(row[4])
                        ticks.append((ts_ms, close))
    return sorted(ticks, key=lambda x: x[0])


def get_15m_candle_key(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    minute_slot = (dt.minute // 15) * 15
    return dt.strftime(f"%Y-%m-%d_{dt.hour:02d}-{minute_slot:02d}")


def analyze_candle(ticks: list[tuple[int, float]]) -> dict:
    if len(ticks) < 2:
        return None

    candle_start_ms = ticks[0][0]
    open_price = ticks[0][1]
    close_price = ticks[-1][1]
    is_bullish = close_price > open_price

    crossings_5min = []
    zone = None

    direction_at_5min = None

    for ts_ms, price in ticks:
        elapsed_sec = (ts_ms - candle_start_ms) / 1000

        if price > open_price:
            curr_zone = "above"
        elif price < open_price:
            curr_zone = "below"
        else:
            curr_zone = "at"

        if elapsed_sec <= 300:
            if zone == "below" and curr_zone == "above":
                crossings_5min.append({"sec": elapsed_sec, "dir": "up"})
            elif zone == "above" and curr_zone == "below":
                crossings_5min.append({"sec": elapsed_sec, "dir": "down"})

        if 299 <= elapsed_sec <= 301:
            direction_at_5min = curr_zone

        if curr_zone != "at":
            zone = curr_zone

    return {
        "open": open_price,
        "close": close_price,
        "is_bullish": is_bullish,
        "crossing_count": len(crossings_5min),
        "direction_at_5min": direction_at_5min,
    }


def main():
    data_dir = Path(__file__).parent.parent / "backtest_1y/data/BTCUSDT/1s"
    zip_files = sorted(data_dir.glob("*.zip"))
    print(f"총 {len(zip_files)}개 일별 데이터 파일")

    candles_data = defaultdict(list)
    for i, zip_path in enumerate(zip_files):
        if (i + 1) % 50 == 0:
            print(f"  처리 중: {i+1}/{len(zip_files)}")
        ticks = load_1s_data_from_zip(zip_path)
        for ts_ms, close in ticks:
            candle_key = get_15m_candle_key(ts_ms)
            candles_data[candle_key].append((ts_ms, close))

    results = []
    for candle_key, ticks in sorted(candles_data.items()):
        if len(ticks) < 800:
            continue
        analysis = analyze_candle(ticks)
        if analysis:
            results.append(analysis)

    print(f"분석 대상: {len(results)}개 15분봉\n")

    # crossing 횟수별 + 5분 시점 위치 기준 적중률
    print("=" * 70)
    print("Crossing 횟수별 적중률 (5분 시점 위치 → 15분봉 방향)")
    print("=" * 70)
    print(f"{'횟수':>4} | {'총개수':>6} | {'above→양봉':>12} | {'below→음봉':>12} | {'전체적중률':>10}")
    print("-" * 70)

    from collections import Counter
    crossing_counts = Counter(r["crossing_count"] for r in results)

    for count in sorted(crossing_counts.keys())[:15]:
        subset = [r for r in results if r["crossing_count"] == count]

        above = [r for r in subset if r["direction_at_5min"] == "above"]
        below = [r for r in subset if r["direction_at_5min"] == "below"]

        above_correct = sum(1 for r in above if r["is_bullish"])
        below_correct = sum(1 for r in below if not r["is_bullish"])

        total_with_dir = len(above) + len(below)
        total_correct = above_correct + below_correct

        above_rate = f"{above_correct}/{len(above)} ({above_correct/len(above)*100:.1f}%)" if above else "N/A"
        below_rate = f"{below_correct}/{len(below)} ({below_correct/len(below)*100:.1f}%)" if below else "N/A"
        total_rate = f"{total_correct/total_with_dir*100:.1f}%" if total_with_dir else "N/A"

        print(f"{count:>4} | {len(subset):>6} | {above_rate:>12} | {below_rate:>12} | {total_rate:>10}")

    # 0회 vs 1회+ 비교
    print("\n" + "=" * 70)
    print("0회 vs 1회+ 비교")
    print("=" * 70)

    zero_crossing = [r for r in results if r["crossing_count"] == 0]
    one_plus = [r for r in results if r["crossing_count"] >= 1]

    for label, subset in [("0회 (no crossing)", zero_crossing), ("1회+", one_plus)]:
        above = [r for r in subset if r["direction_at_5min"] == "above"]
        below = [r for r in subset if r["direction_at_5min"] == "below"]

        above_correct = sum(1 for r in above if r["is_bullish"])
        below_correct = sum(1 for r in below if not r["is_bullish"])

        total = len(above) + len(below)
        correct = above_correct + below_correct

        print(f"\n{label}: {len(subset)}개")
        if above:
            print(f"  above → 양봉: {above_correct}/{len(above)} ({above_correct/len(above)*100:.1f}%)")
        if below:
            print(f"  below → 음봉: {below_correct}/{len(below)} ({below_correct/len(below)*100:.1f}%)")
        if total:
            print(f"  ★ 전체: {correct}/{total} ({correct/total*100:.1f}%)")


if __name__ == "__main__":
    main()
