#!/usr/bin/env python3
"""
Crossing 마팅게일 전략 시뮬레이션

- 10-14분 사이 crossing마다 진입
- 첫 베팅 $4, 이후 2배씩 ($4 → $8 → $16 → ...)
- 가격 0.5 가정 (contracts = bet / 0.5)
- WIN: contracts * 1 - bet = +bet
- LOSS: 0 - bet = -bet
"""

import json
from pathlib import Path


def simulate():
    crossings_path = Path(__file__).parent.parent / "binance-tick-data/btc/crossings.json"

    with open(crossings_path, "r") as f:
        data = json.load(f)

    FIRST_BET = 4.0
    PRICE = 0.5
    MAX_CROSSINGS = 4  # crossing 횟수 제한

    results = []
    total_pnl = 0
    total_bet = 0

    for candle_name, candle_data in data.items():
        crossings = candle_data.get("crossings", [])

        # 10분(600초) ~ 14분(840초) 사이 crossing
        crossings_10_14 = [c for c in crossings if 600 <= c["sec"] < 840]

        if not crossings_10_14:
            continue

        # MAX_CROSSINGS까지만 진입
        crossings_limited = crossings_10_14[:MAX_CROSSINGS]

        if not crossings_limited:
            continue

        # 마지막 진입 시점 이후의 모든 crossing 수로 결과 판단
        last_entry_sec = crossings_limited[-1]["sec"]
        crossings_after_entry = [c for c in crossings if c["sec"] > last_entry_sec]
        is_last_win = (len(crossings_after_entry) % 2 == 0)

        # 각 crossing별 베팅 시뮬레이션 (MAX_CROSSINGS 제한)
        candle_pnl = 0
        candle_bet = 0
        bets = []

        # MAX_CROSSINGS까지만 진입
        crossings_limited = crossings_10_14[:MAX_CROSSINGS]

        if not crossings_limited:
            continue

        for i, c in enumerate(crossings_limited):
            bet_amount = FIRST_BET * (2 ** i)
            direction = c["dir"]
            candle_bet += bet_amount

            # 제한된 범위의 마지막 crossing 방향 기준으로 WIN/LOSS 판단
            last_dir = crossings_limited[-1]["dir"]

            # 이 베팅이 마지막 방향과 같은지?
            if direction == last_dir:
                # 마지막 방향과 같으면, 캔들 결과가 마지막 방향이면 WIN
                pnl = bet_amount if is_last_win else -bet_amount
            else:
                # 마지막 방향과 다르면, 캔들 결과가 마지막 방향이면 LOSS
                pnl = -bet_amount if is_last_win else bet_amount

            candle_pnl += pnl
            bets.append({"dir": direction, "bet": bet_amount, "pnl": pnl})

        total_pnl += candle_pnl
        total_bet += candle_bet

        results.append({
            "candle": candle_name,
            "crossings": len(crossings_10_14),
            "after_14": len(crossings_after_14),
            "last_dir": crossings_10_14[-1]["dir"],
            "result": "WIN" if is_last_win else "LOSS",
            "bets": bets,
            "candle_pnl": candle_pnl,
            "candle_bet": candle_bet,
        })

    # 통계
    total_candles = len(results)
    wins = sum(1 for r in results if r["result"] == "WIN")
    losses = total_candles - wins

    win_pnl = sum(r["candle_pnl"] for r in results if r["result"] == "WIN")
    loss_pnl = sum(r["candle_pnl"] for r in results if r["result"] == "LOSS")

    print("=" * 60)
    print("Crossing 마팅게일 전략 시뮬레이션")
    print(f"첫 베팅: ${FIRST_BET}, 이후 2배씩, 가격 {PRICE}")
    print("=" * 60)
    print(f"총 진입 캔들: {total_candles}")
    print(f"승리: {wins} ({wins/total_candles*100:.1f}%)")
    print(f"패배: {losses} ({losses/total_candles*100:.1f}%)")
    print()
    print(f"총 베팅금액: ${total_bet:.2f}")
    print(f"승리 시 수익: ${win_pnl:.2f}")
    print(f"패배 시 손실: ${loss_pnl:.2f}")
    print(f"순 PnL: ${total_pnl:.2f}")
    print(f"ROI: {total_pnl/total_bet*100:.2f}%")
    print()

    # crossing 횟수별 통계
    from collections import defaultdict
    by_count = defaultdict(lambda: {"candles": 0, "wins": 0, "pnl": 0, "bet": 0})

    for r in results:
        cnt = r["crossings"]
        by_count[cnt]["candles"] += 1
        by_count[cnt]["wins"] += 1 if r["result"] == "WIN" else 0
        by_count[cnt]["pnl"] += r["candle_pnl"]
        by_count[cnt]["bet"] += r["candle_bet"]

    print("10-14분 crossing 횟수별 통계:")
    print(f"{'횟수':>4} | {'캔들':>4} | {'승률':>6} | {'총베팅':>10} | {'PnL':>10}")
    print("-" * 50)
    for cnt in sorted(by_count.keys()):
        s = by_count[cnt]
        wr = s["wins"] / s["candles"] * 100 if s["candles"] else 0
        print(f"{cnt:>4} | {s['candles']:>4} | {wr:>5.1f}% | ${s['bet']:>8.0f} | ${s['pnl']:>+8.0f}")

    print()

    # 샘플 출력
    print("샘플 (처음 5개):")
    for r in results[:5]:
        print(f"\n  {r['candle']}: {r['crossings']}회 crossing, 14분후 {r['after_14']}회 → {r['result']}")
        for b in r["bets"]:
            print(f"    {b['dir'].upper()} ${b['bet']:.0f} → ${b['pnl']:+.0f}")
        print(f"    캔들 PnL: ${r['candle_pnl']:+.0f}")


if __name__ == "__main__":
    simulate()
