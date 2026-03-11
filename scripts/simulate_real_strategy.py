#!/usr/bin/env python3
"""
실제 거래 데이터 기반 수량 조절 시뮬레이션

핵심 수학:
- 가격 p에서 contracts = bet / p
- WIN: contracts * 1 - bet = bet * (1-p)/p
- LOSS: -bet
- 손익분기 승률 = p (가격이 곧 손익분기 승률)
"""

import subprocess
import json
from dataclasses import dataclass
from typing import List, Dict, Tuple
from collections import defaultdict


def get_real_trades() -> List[Dict]:
    """실제 거래 데이터 가져오기"""
    cmd = """
    docker exec cointest-paper-trade-db psql -U paper -d paper_trade -t -A -F'|' -c "
    SELECT
        to_char(candle_start_time AT TIME ZONE 'UTC', 'YYYY-MM-DD_HH24-MI') as candle,
        EXTRACT(EPOCH FROM (time - candle_start_time))::int as entry_sec,
        side,
        fill_price::float as price,
        outcome,
        row_number() OVER (PARTITION BY candle_start_time ORDER BY time) as trade_num
    FROM test_paper_trades
    WHERE strategy_name = 'real_crossing'
      AND status = 'CLOSED'
      AND fill_price IS NOT NULL
    ORDER BY candle_start_time, time;
    "
    """
    output = subprocess.check_output(cmd, shell=True).decode().strip()

    trades = []
    for line in output.split('\n'):
        if not line:
            continue
        parts = line.split('|')
        if len(parts) >= 6:
            trades.append({
                "candle": parts[0],
                "entry_sec": int(parts[1]),
                "side": parts[2],
                "price": float(parts[3]),
                "outcome": parts[4],
                "trade_num": int(parts[5]),
            })
    return trades


def simulate_strategy(
    trades: List[Dict],
    entry_start_sec: int,
    entry_end_sec: int,
    bet_sequence: List[float],
    max_entries: int,
) -> Dict:
    """
    전략 시뮬레이션

    실제 가격 사용:
    - WIN: bet * (1-price)/price
    - LOSS: -bet
    """
    # 캔들별로 그룹화
    candles = defaultdict(list)
    for t in trades:
        candles[t["candle"]].append(t)

    results = {
        "entered_candles": 0,
        "wins": 0,
        "losses": 0,
        "total_pnl": 0.0,
        "total_bet": 0.0,
        "max_bet": 0.0,
        "details": [],
    }

    for candle_name, candle_trades in candles.items():
        # 시간 범위 필터
        valid_trades = [
            t for t in candle_trades
            if entry_start_sec <= t["entry_sec"] < entry_end_sec
        ]

        if not valid_trades:
            continue

        # max_entries 제한
        entries = valid_trades[:max_entries]
        results["entered_candles"] += 1

        # 마지막 진입 정보
        last_entry = entries[-1]
        last_dir = last_entry["side"]
        is_candle_win = last_entry["outcome"] == "WIN"

        # PnL 계산 (실제 가격 사용)
        candle_pnl = 0.0
        candle_bet = 0.0

        for i, t in enumerate(entries):
            bet = bet_sequence[i] if i < len(bet_sequence) else bet_sequence[-1]
            price = t["price"]
            direction = t["side"]

            # 이 베팅이 마지막 방향과 같은지?
            same_as_last = (direction == last_dir)

            if same_as_last:
                # 마지막 방향과 같으면 캔들 결과 따름
                if is_candle_win:
                    pnl = bet * (1 - price) / price
                else:
                    pnl = -bet
            else:
                # 마지막 방향과 다르면 반대
                if is_candle_win:
                    pnl = -bet
                else:
                    pnl = bet * (1 - price) / price

            candle_pnl += pnl
            candle_bet += bet
            results["max_bet"] = max(results["max_bet"], bet)

        results["total_pnl"] += candle_pnl
        results["total_bet"] += candle_bet

        if is_candle_win:
            results["wins"] += 1
        else:
            results["losses"] += 1

        results["details"].append({
            "candle": candle_name,
            "entries": len(entries),
            "last_dir": last_dir,
            "win": is_candle_win,
            "pnl": candle_pnl,
            "bet": candle_bet,
        })

    return results


def main():
    trades = get_real_trades()
    print(f"Total trades loaded: {len(trades)}")

    # 전략 조합
    strategies = [
        # (이름, entry_start, entry_end, bet_sequence, max_entries)
        ("현재전략 (10-14m, martin)", 600, 840, [4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048], 10),

        # Top 1: 13-14분, max=1
        ("Top1: 13-14m, max=1, flat_8", 780, 840, [8]*10, 1),
        ("Top1: 13-14m, max=1, flat_16", 780, 840, [16]*10, 1),

        # Top 2: 13-14m30s, max=3, martin
        ("Top2: 13-14m30s, max=3, martin", 780, 870, [4, 8, 16], 3),
        ("Top2: 13-14m30s, max=3, flat_8", 780, 870, [8]*3, 3),

        # Top 3: 12-14m, max=10, martin
        ("Top3: 12-14m, max=10, martin", 720, 840, [4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048], 10),

        # 실제 데이터에서 좋았던 시간대
        ("12-13m, max=1, flat_8", 720, 780, [8]*10, 1),
        ("12-13m, max=2, martin", 720, 780, [4, 8], 2),

        # 10-12분 시간대
        ("10-12m, max=1, flat_8", 600, 720, [8]*10, 1),
        ("10-12m, max=2, flat_8", 600, 720, [8]*10, 2),

        # Reverse martin (역마팅게일)
        ("13-14m, max=3, reverse", 780, 840, [16, 8, 4], 3),
        ("12-14m, max=3, reverse", 720, 840, [16, 8, 4], 3),
    ]

    print("\n" + "=" * 100)
    print("실제 가격 기반 전략 시뮬레이션")
    print("=" * 100)
    print(f"{'전략명':<35} | {'캔들':>4} | {'승률':>6} | {'PnL':>10} | {'베팅':>10} | {'ROI':>7} | {'MaxBet':>7}")
    print("-" * 100)

    for name, start, end, seq, max_ent in strategies:
        result = simulate_strategy(trades, start, end, seq, max_ent)

        if result["entered_candles"] == 0:
            continue

        win_rate = result["wins"] / result["entered_candles"] * 100
        roi = result["total_pnl"] / result["total_bet"] * 100 if result["total_bet"] > 0 else 0

        print(f"{name:<35} | {result['entered_candles']:>4} | {win_rate:>5.1f}% | "
              f"${result['total_pnl']:>+9.2f} | ${result['total_bet']:>9.2f} | "
              f"{roi:>+6.1f}% | ${result['max_bet']:>6.0f}")

    # 손익분기 분석
    print("\n" + "=" * 100)
    print("손익분기 분석 (가격 = 손익분기 승률)")
    print("=" * 100)

    # 시간대별 평균 가격과 필요 승률
    time_ranges = [
        ("10-12분", 600, 720),
        ("12-13분", 720, 780),
        ("13-14분", 780, 840),
    ]

    for name, start, end in time_ranges:
        filtered = [t for t in trades if start <= t["entry_sec"] < end]
        if not filtered:
            continue

        avg_price = sum(t["price"] for t in filtered) / len(filtered)
        wins = sum(1 for t in filtered if t["outcome"] == "WIN")
        actual_wr = wins / len(filtered) * 100

        break_even = avg_price * 100
        margin = actual_wr - break_even

        status = "✓ 수익" if margin > 0 else "✗ 손실"
        print(f"{name}: 평균가격={avg_price:.3f} → 손익분기={break_even:.1f}% | "
              f"실제승률={actual_wr:.1f}% | 마진={margin:+.1f}% {status}")


if __name__ == "__main__":
    main()
