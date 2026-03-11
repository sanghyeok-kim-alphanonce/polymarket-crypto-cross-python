#!/usr/bin/env python3
"""
백테스트와 Real Trading 캔들별 비교
"""

import json
import subprocess
from pathlib import Path


def get_backtest_candles():
    """백테스트에서 진입한 캔들 (10-14분 crossing 있는 것)"""
    crossings_path = Path(__file__).parent.parent / "binance-tick-data/btc/crossings.json"

    with open(crossings_path, "r") as f:
        data = json.load(f)

    result = {}
    for candle_name, candle_data in data.items():
        candle_date = candle_name[:10]
        if not ("2026-03-06" <= candle_date < "2026-03-09"):
            continue

        crossings = candle_data.get("crossings", [])
        crossings_10_14 = [c for c in crossings if 600 <= c["sec"] < 840]

        if not crossings_10_14:
            continue

        crossings_after_14 = [c for c in crossings if c["sec"] >= 840]
        is_win = (len(crossings_after_14) % 2 == 0)

        result[candle_name] = {
            "position": crossings_10_14[-1]["dir"],
            "crossings_10_14": len(crossings_10_14),
            "crossings_after_14": len(crossings_after_14),
            "backtest_result": "WIN" if is_win else "LOSS",
        }

    return result


def get_real_candles():
    """Real trading에서 마지막 거래 기준 결과"""
    cmd = """
    docker exec cointest-paper-trade-db psql -U paper -d paper_trade -t -A -F'|' -c "
    WITH last_trades AS (
        SELECT DISTINCT ON (candle_start_time)
            to_char(candle_start_time AT TIME ZONE 'UTC', 'YYYY-MM-DD_HH24-MI') as candle,
            side, outcome
        FROM test_paper_trades
        WHERE strategy_name = 'real_crossing' AND status = 'CLOSED'
        ORDER BY candle_start_time, time DESC
    )
    SELECT candle, side, outcome FROM last_trades;
    "
    """
    output = subprocess.check_output(cmd, shell=True).decode().strip()

    result = {}
    for line in output.split('\n'):
        if not line:
            continue
        parts = line.split('|')
        if len(parts) >= 3:
            candle, side, outcome = parts[0], parts[1], parts[2]
            result[candle] = {
                "position": side.lower(),
                "real_result": outcome,
            }

    return result


def main():
    backtest = get_backtest_candles()
    real = get_real_candles()

    all_candles = sorted(set(backtest.keys()) | set(real.keys()))

    print("=" * 80)
    print("백테스트 vs Real Trading 캔들별 비교")
    print("=" * 80)

    # 통계
    only_backtest = [c for c in all_candles if c in backtest and c not in real]
    only_real = [c for c in all_candles if c not in backtest and c in real]
    both = [c for c in all_candles if c in backtest and c in real]

    match = 0
    mismatch = 0
    mismatch_details = []

    for c in both:
        bt = backtest[c]
        rl = real[c]
        bt_result = bt["backtest_result"]
        rl_result = rl["real_result"]

        if bt_result == rl_result:
            match += 1
        else:
            mismatch += 1
            mismatch_details.append({
                "candle": c,
                "bt_pos": bt["position"],
                "rl_pos": rl["position"],
                "bt_result": bt_result,
                "rl_result": rl_result,
                "after_14": bt["crossings_after_14"],
            })

    print(f"\n백테스트에만 있는 캔들: {len(only_backtest)}개")
    print(f"Real에만 있는 캔들: {len(only_real)}개")
    print(f"둘 다 있는 캔들: {len(both)}개")
    print(f"  - 결과 일치: {match}개")
    print(f"  - 결과 불일치: {mismatch}개")

    if only_backtest:
        print(f"\n백테스트에만 있는 캔들 (Real에서 미진입):")
        for c in only_backtest[:10]:
            bt = backtest[c]
            print(f"  {c}: {bt['position'].upper()} → {bt['backtest_result']} (after14={bt['crossings_after_14']})")
        if len(only_backtest) > 10:
            print(f"  ... 외 {len(only_backtest)-10}개")

    if mismatch_details:
        print(f"\n결과 불일치 캔들:")
        for m in mismatch_details:
            print(f"  {m['candle']}: BT={m['bt_pos'].upper()}→{m['bt_result']} vs RL={m['rl_pos'].upper()}→{m['rl_result']} (after14={m['after_14']})")

    # 동일 캔들에서 포지션 방향 비교
    pos_match = sum(1 for c in both if backtest[c]["position"] == real[c]["position"])
    print(f"\n동일 캔들 포지션 방향 일치: {pos_match}/{len(both)}")


if __name__ == "__main__":
    main()
