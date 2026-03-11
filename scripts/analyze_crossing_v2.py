#!/usr/bin/env python3
"""
BTC crossing 전략 분석 v2

수정: 마지막 진입 시점 이후의 crossing만 카운트
(기존: 14분(840초) 이후 crossing 카운트)
"""

import json
from pathlib import Path


def analyze(start_date: str = "2026-02-25", end_date: str = "2026-03-10"):
    crossings_path = Path(__file__).parent.parent / "binance-tick-data/btc/crossings.json"

    with open(crossings_path, "r") as f:
        data = json.load(f)

    results = []

    for candle_name, candle_data in data.items():
        # 날짜 필터
        candle_date = candle_name[:10]
        if not (start_date <= candle_date < end_date):
            continue

        crossings = candle_data.get("crossings", [])

        # 10분(600초) ~ 14분(840초) 사이 crossing
        crossings_10_14 = [c for c in crossings if 600 <= c["sec"] < 840]

        if not crossings_10_14:
            continue

        # 마지막 진입 시점
        last_entry = crossings_10_14[-1]
        last_entry_sec = last_entry["sec"]
        position_dir = last_entry["dir"]

        # 마지막 진입 시점 이후의 crossing 횟수 (캔들 끝까지)
        crossings_after_entry = [c for c in crossings if c["sec"] > last_entry_sec]
        count_after_entry = len(crossings_after_entry)

        # 짝수면 승리, 홀수면 패배
        is_win = (count_after_entry % 2 == 0)

        results.append({
            "candle": candle_name,
            "position": position_dir,
            "entry_sec": last_entry_sec,
            "crossings_after": count_after_entry,
            "result": "WIN" if is_win else "LOSS",
        })

    # 통계
    total = len(results)
    if total == 0:
        print(f"No trades for {start_date} ~ {end_date}")
        return

    wins = sum(1 for r in results if r["result"] == "WIN")
    losses = total - wins

    print("=" * 60)
    print(f"기간: {start_date} ~ {end_date}")
    print(f"로직: 마지막 진입 시점 이후 crossing 카운트")
    print("=" * 60)
    print(f"총 진입 캔들: {total}")
    print(f"승리: {wins} ({wins/total*100:.1f}%)")
    print(f"패배: {losses} ({losses/total*100:.1f}%)")
    print()

    # 진입 후 crossing 횟수별
    from collections import Counter
    after_counts = Counter(r["crossings_after"] for r in results)
    print("진입 후 crossing 횟수별:")
    for cnt in sorted(after_counts.keys()):
        n = after_counts[cnt]
        outcome = "WIN" if cnt % 2 == 0 else "LOSS"
        print(f"  {cnt}회: {n}개 ({outcome})")

    return results


if __name__ == "__main__":
    print("=== Real Trading 기간 (3/6~3/8) ===")
    analyze("2026-03-06", "2026-03-09")

    print("\n")
    print("=== 전체 기간 ===")
    analyze()
