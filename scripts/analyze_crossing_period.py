#!/usr/bin/env python3
"""
특정 기간만 백테스트 (Real trading 기간과 비교)
"""

import json
from pathlib import Path
from datetime import datetime


def analyze(start_date: str = "2026-03-06", end_date: str = "2026-03-09"):
    crossings_path = Path(__file__).parent.parent / "binance-tick-data/btc/crossings.json"

    with open(crossings_path, "r") as f:
        data = json.load(f)

    results = []

    for candle_name, candle_data in data.items():
        # 날짜 필터
        candle_date = candle_name[:10]  # "2026-03-06"
        if not (start_date <= candle_date < end_date):
            continue

        crossings = candle_data.get("crossings", [])

        # 10분(600초) ~ 14분(840초) 사이 crossing 필터
        crossings_10_14 = [c for c in crossings if 600 <= c["sec"] < 840]

        if not crossings_10_14:
            continue

        # 14분 시점 마지막 crossing 방향 = 포지션 방향
        last_crossing = crossings_10_14[-1]
        position_dir = last_crossing["dir"]
        entry_sec = last_crossing["sec"]

        # 14분(840초) 이후 crossing 횟수
        crossings_after_14 = [c for c in crossings if c["sec"] >= 840]
        count_after_14 = len(crossings_after_14)

        # 짝수면 승리, 홀수면 패배
        is_win = (count_after_14 % 2 == 0)

        results.append({
            "candle": candle_name,
            "position": position_dir,
            "entry_sec": entry_sec,
            "crossings_after_14": count_after_14,
            "result": "WIN" if is_win else "LOSS",
        })

    # 통계
    total = len(results)
    if total == 0:
        print(f"No trades found for period {start_date} ~ {end_date}")
        return

    wins = sum(1 for r in results if r["result"] == "WIN")
    losses = total - wins

    print("=" * 60)
    print(f"기간: {start_date} ~ {end_date}")
    print("=" * 60)
    print(f"총 진입 캔들: {total}")
    print(f"승리: {wins} ({wins/total*100:.1f}%)")
    print(f"패배: {losses} ({losses/total*100:.1f}%)")
    print()

    # 포지션 방향별
    up_results = [r for r in results if r["position"] == "up"]
    down_results = [r for r in results if r["position"] == "down"]

    up_wins = sum(1 for r in up_results if r["result"] == "WIN")
    down_wins = sum(1 for r in down_results if r["result"] == "WIN")

    print("포지션 방향별:")
    if up_results:
        print(f"  UP:   {up_wins}/{len(up_results)} ({up_wins/len(up_results)*100:.1f}%)")
    if down_results:
        print(f"  DOWN: {down_wins}/{len(down_results)} ({down_wins/len(down_results)*100:.1f}%)")

    print()

    # 14분 이후 crossing 횟수별
    from collections import Counter
    after_counts = Counter(r["crossings_after_14"] for r in results)
    print("14분 이후 crossing 횟수별:")
    for cnt in sorted(after_counts.keys()):
        n = after_counts[cnt]
        outcome = "WIN" if cnt % 2 == 0 else "LOSS"
        print(f"  {cnt}회: {n}개 ({outcome})")


if __name__ == "__main__":
    print("=== Real Trading 기간 (3/6~3/8) ===")
    analyze("2026-03-06", "2026-03-09")

    print("\n" + "=" * 60)
    print("\n=== 전체 기간 (2/25~3/9) ===")
    analyze("2026-02-25", "2026-03-10")
