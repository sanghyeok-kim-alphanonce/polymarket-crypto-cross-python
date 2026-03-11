#!/usr/bin/env python3
"""
Crossing 전략 백테스트 프레임워크

조절 가능한 파라미터:
1. entry_start_sec: 진입 시작 시간 (초)
2. entry_end_sec: 진입 종료 시간 (초)
3. bet_sequence: 각 crossing별 베팅액 리스트 (예: [4, 8, 16])
4. max_entries: 최대 진입 횟수 (금액 폭발 방지)

결과 판단:
- 마지막 진입 시점 이후 crossing이 짝수면 WIN, 홀수면 LOSS
- WIN: +bet, LOSS: -bet (가격 0.5 가정)
"""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional
from collections import defaultdict


@dataclass
class StrategyConfig:
    entry_start_sec: int = 600   # 10분
    entry_end_sec: int = 840     # 14분
    bet_sequence: List[float] = None  # [4, 8, 16, ...] 또는 None이면 자동 생성
    max_entries: int = 10        # 최대 진입 횟수
    first_bet: float = 4.0       # bet_sequence가 None일 때 첫 베팅액
    multiplier: float = 2.0      # bet_sequence가 None일 때 배수

    def __post_init__(self):
        if self.bet_sequence is None:
            # 기본: 마팅게일 시퀀스
            self.bet_sequence = [
                self.first_bet * (self.multiplier ** i)
                for i in range(self.max_entries)
            ]


@dataclass
class BacktestResult:
    total_candles: int
    entered_candles: int
    wins: int
    losses: int
    total_pnl: float
    total_bet: float
    max_single_bet: float
    avg_entries_per_candle: float

    @property
    def win_rate(self) -> float:
        return self.wins / self.entered_candles * 100 if self.entered_candles else 0

    @property
    def roi(self) -> float:
        return self.total_pnl / self.total_bet * 100 if self.total_bet else 0


def load_crossings():
    crossings_path = Path(__file__).parent.parent / "binance-tick-data/btc/crossings.json"
    with open(crossings_path, "r") as f:
        return json.load(f)


def backtest(config: StrategyConfig, data: dict,
             start_date: str = "2026-02-25", end_date: str = "2026-03-10") -> BacktestResult:
    """단일 설정으로 백테스트 실행"""

    total_candles = 0
    entered_candles = 0
    wins = 0
    losses = 0
    total_pnl = 0.0
    total_bet = 0.0
    max_single_bet = 0.0
    total_entries = 0

    for candle_name, candle_data in data.items():
        # 날짜 필터
        candle_date = candle_name[:10]
        if not (start_date <= candle_date < end_date):
            continue

        total_candles += 1
        crossings = candle_data.get("crossings", [])

        # 진입 시간 범위 내 crossing 필터
        valid_crossings = [
            c for c in crossings
            if config.entry_start_sec <= c["sec"] < config.entry_end_sec
        ]

        if not valid_crossings:
            continue

        # max_entries 제한
        entries = valid_crossings[:config.max_entries]
        entered_candles += 1
        total_entries += len(entries)

        # 마지막 진입 정보
        last_entry = entries[-1]
        last_entry_sec = last_entry["sec"]
        last_dir = last_entry["dir"]

        # 마지막 진입 이후 crossing 수
        crossings_after = [c for c in crossings if c["sec"] > last_entry_sec]
        is_win = (len(crossings_after) % 2 == 0)

        # PnL 계산
        candle_pnl = 0.0
        candle_bet = 0.0

        for i, c in enumerate(entries):
            bet = config.bet_sequence[i] if i < len(config.bet_sequence) else config.bet_sequence[-1]
            direction = c["dir"]

            # 이 베팅이 마지막 방향과 같은지?
            if direction == last_dir:
                pnl = bet if is_win else -bet
            else:
                pnl = -bet if is_win else bet

            candle_pnl += pnl
            candle_bet += bet
            max_single_bet = max(max_single_bet, bet)

        total_pnl += candle_pnl
        total_bet += candle_bet

        if is_win:
            wins += 1
        else:
            losses += 1

    avg_entries = total_entries / entered_candles if entered_candles else 0

    return BacktestResult(
        total_candles=total_candles,
        entered_candles=entered_candles,
        wins=wins,
        losses=losses,
        total_pnl=total_pnl,
        total_bet=total_bet,
        max_single_bet=max_single_bet,
        avg_entries_per_candle=avg_entries,
    )


def grid_search():
    """파라미터 그리드 서치"""
    data = load_crossings()

    results = []

    # 파라미터 조합
    entry_ranges = [
        (600, 840),   # 10분~14분
        (600, 870),   # 10분~14분30초
        (660, 840),   # 11분~14분
        (660, 870),   # 11분~14분30초
        (720, 840),   # 12분~14분
        (720, 870),   # 12분~14분30초
        (780, 840),   # 13분~14분
        (780, 870),   # 13분~14분30초
    ]

    max_entries_list = [1, 2, 3, 4, 5, 10]

    bet_sequences = {
        "flat_4": [4] * 10,                    # 고정 4달러
        "flat_8": [8] * 10,                    # 고정 8달러
        "martin_4": [4 * (2**i) for i in range(10)],   # 마팅게일 4,8,16...
        "martin_2": [2 * (2**i) for i in range(10)],   # 마팅게일 2,4,8...
        "linear_4": [4 * (i+1) for i in range(10)],    # 선형 4,8,12,16...
        "reverse_16": [16, 8, 4, 4, 4, 4, 4, 4, 4, 4], # 역마팅게일
    }

    print("=" * 100)
    print("Crossing 전략 파라미터 그리드 서치")
    print("=" * 100)
    print(f"{'시간범위':>12} | {'Max':>3} | {'시퀀스':>12} | {'진입':>4} | {'승률':>6} | {'PnL':>10} | {'총베팅':>10} | {'ROI':>7} | {'MaxBet':>8}")
    print("-" * 100)

    for entry_start, entry_end in entry_ranges:
        for max_ent in max_entries_list:
            for seq_name, seq in bet_sequences.items():
                config = StrategyConfig(
                    entry_start_sec=entry_start,
                    entry_end_sec=entry_end,
                    bet_sequence=seq,
                    max_entries=max_ent,
                )

                result = backtest(config, data)

                time_range = f"{entry_start//60}m~{entry_end//60}m{entry_end%60:02d}s"

                results.append({
                    "time_range": time_range,
                    "entry_start": entry_start,
                    "entry_end": entry_end,
                    "max_entries": max_ent,
                    "bet_seq": seq_name,
                    "result": result,
                })

                # ROI > 0인 것만 출력
                if result.roi > 0:
                    print(f"{time_range:>12} | {max_ent:>3} | {seq_name:>12} | "
                          f"{result.entered_candles:>4} | {result.win_rate:>5.1f}% | "
                          f"${result.total_pnl:>+9.0f} | ${result.total_bet:>9.0f} | "
                          f"{result.roi:>+6.1f}% | ${result.max_single_bet:>7.0f}")

    # Top 10 by ROI
    print("\n" + "=" * 100)
    print("Top 10 by ROI (ROI > 0)")
    print("=" * 100)

    positive_roi = [r for r in results if r["result"].roi > 0]
    sorted_results = sorted(positive_roi, key=lambda x: x["result"].roi, reverse=True)

    for i, r in enumerate(sorted_results[:10], 1):
        res = r["result"]
        print(f"{i:>2}. {r['time_range']:>12} | max={r['max_entries']} | {r['bet_seq']:>12} | "
              f"진입={res.entered_candles} | 승률={res.win_rate:.1f}% | "
              f"PnL=${res.total_pnl:+.0f} | ROI={res.roi:+.1f}%")


def main():
    # 단일 테스트 예시
    data = load_crossings()

    print("=== 현재 전략 (10분~14분, 마팅게일 4,8,16..., max=10) ===")
    config = StrategyConfig(
        entry_start_sec=600,
        entry_end_sec=840,
        bet_sequence=[4 * (2**i) for i in range(10)],
        max_entries=10,
    )
    result = backtest(config, data)

    print(f"진입 캔들: {result.entered_candles}/{result.total_candles}")
    print(f"승률: {result.win_rate:.1f}% ({result.wins}승 {result.losses}패)")
    print(f"총 PnL: ${result.total_pnl:+,.0f}")
    print(f"총 베팅: ${result.total_bet:,.0f}")
    print(f"ROI: {result.roi:+.2f}%")
    print(f"최대 단일 베팅: ${result.max_single_bet:,.0f}")
    print(f"평균 진입 횟수: {result.avg_entries_per_candle:.1f}")

    print("\n")
    grid_search()


if __name__ == "__main__":
    main()
