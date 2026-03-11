#!/usr/bin/env python3
"""
Simple Backtest - Entry Price 고정 가정

조건:
1. |delta_1s| >= threshold
2. delta 방향 == distance 방향 (모멘텀 일치)
"""
import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict


@dataclass
class Trade:
    candle_slug: str
    time_sec: int
    side: str
    delta_1s: float
    distance: float
    market_result: str
    entry_price: float = 0.5  # 가정
    pnl: float = 0.0

    @property
    def is_win(self) -> bool:
        return self.side == self.market_result


def backtest_candle(filepath: str, delta_threshold: float, require_momentum: bool, cooldown: int) -> List[Trade]:
    """단일 캔들 백테스트"""
    with open(filepath, 'r') as f:
        data = json.load(f)

    meta = data.get('meta', {})
    market_result = meta.get('market_result')
    if not market_result or market_result == 'FLAT':
        return []

    prices = data.get('binance_prices', [])
    if len(prices) < 2:
        return []

    candle_start = datetime.fromisoformat(meta['candle_start'].replace('Z', '+00:00'))
    start_price = prices[0]['price']
    slug = meta['market_slug']

    trades = []
    last_trade_time = -cooldown

    for i in range(1, len(prices)):
        curr = prices[i]
        prev = prices[i-1]

        curr_time = datetime.fromisoformat(curr['time'].replace('Z', '+00:00'))
        time_sec = int((curr_time - candle_start).total_seconds())

        if time_sec < 0 or time_sec > 900:
            continue

        if time_sec - last_trade_time < cooldown:
            continue

        delta_1s = curr['price'] - prev['price']
        distance = curr['price'] - start_price

        if abs(delta_1s) < delta_threshold:
            continue

        delta_dir = 'UP' if delta_1s > 0 else 'DOWN'
        dist_dir = 'UP' if distance > 0 else 'DOWN'

        if require_momentum and delta_dir != dist_dir:
            continue

        side = delta_dir

        # 간단한 PnL: 고정 entry_price=0.5 가정, $1 베팅
        entry_price = 0.5
        contracts = 2  # $1 / $0.5
        if side == market_result:
            pnl = contracts * (1 - entry_price)  # +$1
        else:
            pnl = -contracts * entry_price  # -$1

        trades.append(Trade(
            candle_slug=slug,
            time_sec=time_sec,
            side=side,
            delta_1s=delta_1s,
            distance=distance,
            market_result=market_result,
            entry_price=entry_price,
            pnl=pnl,
        ))
        last_trade_time = time_sec

    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--delta", type=float, default=30.0)
    parser.add_argument("--no-momentum", action="store_true")
    parser.add_argument("--cooldown", type=int, default=5)
    args = parser.parse_args()

    all_trades = []
    candles = 0

    for filename in sorted(os.listdir(args.data)):
        if filename.endswith('.json'):
            filepath = os.path.join(args.data, filename)
            trades = backtest_candle(filepath, args.delta, not args.no_momentum, args.cooldown)
            all_trades.extend(trades)
            candles += 1

    wins = sum(1 for t in all_trades if t.is_win)
    total = len(all_trades)
    total_pnl = sum(t.pnl for t in all_trades)

    print(f"\n{'='*70}")
    print(f"BACKTEST RESULT")
    print(f"{'='*70}")
    print(f"Delta Threshold: ${args.delta}")
    print(f"Require Momentum Match: {not args.no_momentum}")
    print(f"Cooldown: {args.cooldown}s")
    print(f"Candles: {candles}")
    print(f"{'='*70}")
    print(f"Total Trades: {total}")
    print(f"Wins: {wins}, Losses: {total - wins}")
    print(f"Win Rate: {100*wins/total:.1f}%" if total > 0 else "N/A")
    print(f"Total PnL: ${total_pnl:.2f}")
    print(f"Avg PnL/Trade: ${total_pnl/total:.4f}" if total > 0 else "N/A")

    # 파라미터 비교
    print(f"\n{'='*70}")
    print("PARAMETER COMPARISON")
    print(f"{'='*70}")
    print(f"{'Delta':>8} {'Momentum':>10} {'Trades':>8} {'WinRate':>10} {'PnL':>10}")
    print("-"*50)

    for delta in [20, 30, 50]:
        for momentum in [True, False]:
            trades = []
            for filename in sorted(os.listdir(args.data)):
                if filename.endswith('.json'):
                    filepath = os.path.join(args.data, filename)
                    trades.extend(backtest_candle(filepath, delta, momentum, args.cooldown))

            w = sum(1 for t in trades if t.is_win)
            n = len(trades)
            pnl = sum(t.pnl for t in trades)
            wr = 100*w/n if n > 0 else 0
            mom_str = "Yes" if momentum else "No"
            print(f"${delta:>7} {mom_str:>10} {n:>8} {wr:>9.1f}% ${pnl:>9.2f}")

    # 상세 출력
    if all_trades:
        print(f"\n[Recent Trades]")
        for t in all_trades[-20:]:
            mark = '✓' if t.is_win else '✗'
            print(f"  {t.candle_slug[-15:]} {t.time_sec:>4}s {t.side:<5} Δ={t.delta_1s:>+7.2f} dist={t.distance:>+8.2f} → {t.market_result} {mark}")


if __name__ == "__main__":
    main()
