#!/usr/bin/env python3
"""
Real Trading에서 진입한 캔들만 백테스트
"""

import json
import subprocess
from pathlib import Path


def get_real_candles():
    cmd = """
    docker exec cointest-paper-trade-db psql -U paper -d paper_trade -t -A -c "
    SELECT DISTINCT to_char(candle_start_time AT TIME ZONE 'UTC', 'YYYY-MM-DD_HH24-MI')
    FROM test_paper_trades
    WHERE strategy_name = 'real_crossing' AND status = 'CLOSED';
    "
    """
    output = subprocess.check_output(cmd, shell=True).decode().strip()
    return set(output.split('\n'))


def main():
    crossings_path = Path(__file__).parent.parent / "binance-tick-data/btc/crossings.json"

    with open(crossings_path, "r") as f:
        data = json.load(f)

    real_candles = get_real_candles()
    print(f"Real Trading 캔들 수: {len(real_candles)}")

    results = []

    for candle_name in real_candles:
        if candle_name not in data:
            print(f"  WARNING: {candle_name} not in backtest data")
            continue

        candle_data = data[candle_name]
        crossings = candle_data.get("crossings", [])

        # 10분(600초) ~ 14분(840초) 사이 crossing
        crossings_10_14 = [c for c in crossings if 600 <= c["sec"] < 840]

        if not crossings_10_14:
            print(f"  WARNING: {candle_name} has no 10-14 crossings")
            continue

        last_entry = crossings_10_14[-1]
        last_entry_sec = last_entry["sec"]
        position_dir = last_entry["dir"]

        crossings_after_entry = [c for c in crossings if c["sec"] > last_entry_sec]
        count_after_entry = len(crossings_after_entry)

        is_win = (count_after_entry % 2 == 0)

        results.append({
            "candle": candle_name,
            "position": position_dir,
            "entry_sec": last_entry_sec,
            "crossings_after": count_after_entry,
            "backtest_result": "WIN" if is_win else "LOSS",
        })

    # 통계
    total = len(results)
    wins = sum(1 for r in results if r["backtest_result"] == "WIN")

    print(f"\n백테스트 결과 (Real 캔들 {total}개 기준):")
    print(f"  승리: {wins} ({wins/total*100:.1f}%)")
    print(f"  패배: {total - wins} ({(total-wins)/total*100:.1f}%)")


if __name__ == "__main__":
    main()
