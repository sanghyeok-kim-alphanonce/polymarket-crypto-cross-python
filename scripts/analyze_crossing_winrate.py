#!/usr/bin/env python3
"""
BTC crossing 전략 승률 분석

조건:
- 10분(600초) ~ 14분(840초) 사이에 crossing이 있을 때만 진입
- 14분(840초) 시점에 마지막 crossing 방향으로 포지션
- 14분 이후 crossing 횟수가 짝수면 승리, 홀수면 패배
"""

import json
from pathlib import Path


def analyze():
    crossings_path = Path(__file__).parent.parent / "binance-tick-data/btc/crossings.json"

    with open(crossings_path, "r") as f:
        data = json.load(f)

    # 결과 저장
    results = []

    for candle_name, candle_data in data.items():
        crossings = candle_data.get("crossings", [])

        # 10분(600초) ~ 14분(840초) 사이 crossing 필터
        crossings_10_14 = [c for c in crossings if 600 <= c["sec"] < 840]

        if not crossings_10_14:
            # 10-14분 사이에 crossing 없으면 진입 안함
            continue

        # 14분 시점 마지막 crossing 방향 = 포지션 방향
        last_crossing_before_14 = crossings_10_14[-1]
        position_dir = last_crossing_before_14["dir"]
        entry_sec = last_crossing_before_14["sec"]

        # 14분(840초) 이후 crossing 횟수
        crossings_after_14 = [c for c in crossings if c["sec"] >= 840]
        count_after_14 = len(crossings_after_14)

        # 짝수면 승리, 홀수면 패배
        is_win = (count_after_14 % 2 == 0)

        results.append({
            "candle": candle_name,
            "position": position_dir,
            "entry_sec": entry_sec,
            "crossings_10_14": len(crossings_10_14),
            "crossings_after_14": count_after_14,
            "result": "WIN" if is_win else "LOSS",
        })

    # 통계
    total = len(results)
    wins = sum(1 for r in results if r["result"] == "WIN")
    losses = total - wins

    print("=" * 60)
    print("BTC Crossing 전략 분석 (10분-14분 진입, 14분 기준)")
    print("=" * 60)
    print(f"총 진입 캔들: {total}")
    print(f"승리: {wins} ({wins/total*100:.1f}%)")
    print(f"패배: {losses} ({losses/total*100:.1f}%)")
    print()

    # 14분 이후 crossing 횟수별 분포
    print("14분 이후 crossing 횟수별 분포:")
    from collections import Counter
    after_counts = Counter(r["crossings_after_14"] for r in results)
    for cnt in sorted(after_counts.keys()):
        n = after_counts[cnt]
        outcome = "WIN" if cnt % 2 == 0 else "LOSS"
        print(f"  {cnt}회: {n}개 ({outcome})")

    print()

    # 포지션 방향별 통계
    up_results = [r for r in results if r["position"] == "up"]
    down_results = [r for r in results if r["position"] == "down"]

    up_wins = sum(1 for r in up_results if r["result"] == "WIN")
    down_wins = sum(1 for r in down_results if r["result"] == "WIN")

    print("포지션 방향별 승률:")
    if up_results:
        print(f"  UP:   {up_wins}/{len(up_results)} ({up_wins/len(up_results)*100:.1f}%)")
    if down_results:
        print(f"  DOWN: {down_wins}/{len(down_results)} ({down_wins/len(down_results)*100:.1f}%)")

    print()

    # 샘플 출력
    print("샘플 (처음 10개):")
    for r in results[:10]:
        print(f"  {r['candle']}: {r['position'].upper()} @{r['entry_sec']}s → "
              f"after14={r['crossings_after_14']} → {r['result']}")


if __name__ == "__main__":
    analyze()
