#!/usr/bin/env python3
"""
Delta 1s 시그널 분석

State = (distance, delta_1s, time_left)
각 상태에서 해당 방향으로 베팅했을 때 승률 분석

Usage:
    python analyze_delta_signals.py --data ./backtest_data/candles/btc/
"""
import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Tuple


@dataclass
class DeltaEvent:
    """1초 delta 이벤트"""
    time_sec: int           # 캔들 시작 후 경과 시간 (초)
    time_left: int          # 남은 시간 (초)
    price: float            # 현재 가격
    delta_1s: float         # 1초 변화 ($)
    distance: float         # 시작가 대비 현재 위치 ($)
    market_result: str      # 캔들 최종 결과 (UP/DOWN)

    @property
    def delta_direction(self) -> str:
        """delta 방향"""
        return 'UP' if self.delta_1s > 0 else 'DOWN'

    @property
    def is_correct(self) -> bool:
        """delta 방향 = 결과 방향?"""
        return self.delta_direction == self.market_result


def load_candle_events(filepath: str) -> List[DeltaEvent]:
    """캔들에서 delta 이벤트 추출"""
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

    events = []
    for i in range(1, len(prices)):
        curr = prices[i]
        prev = prices[i-1]

        curr_time = datetime.fromisoformat(curr['time'].replace('Z', '+00:00'))
        time_sec = int((curr_time - candle_start).total_seconds())

        if time_sec < 0 or time_sec > 900:
            continue

        delta_1s = curr['price'] - prev['price']
        distance = curr['price'] - start_price

        # delta가 0이 아닌 경우만
        if abs(delta_1s) > 0.01:
            events.append(DeltaEvent(
                time_sec=time_sec,
                time_left=900 - time_sec,
                price=curr['price'],
                delta_1s=delta_1s,
                distance=distance,
                market_result=market_result,
            ))

    return events


def bucket_value(value: float, thresholds: List[float]) -> str:
    """값을 구간으로 분류"""
    for i, t in enumerate(thresholds):
        if value < t:
            if i == 0:
                return f"<{t}"
            return f"{thresholds[i-1]}~{t}"
    return f">={thresholds[-1]}"


def analyze_by_conditions(events: List[DeltaEvent]):
    """조건별 승률 분석"""

    # 1. |delta_1s| 크기별 분석
    print("\n" + "="*80)
    print("[1] |delta_1s| 크기별 승률 (해당 방향으로 베팅 시)")
    print("="*80)

    delta_thresholds = [10, 20, 30, 50, 100]
    delta_buckets = defaultdict(lambda: {'total': 0, 'correct': 0})

    for e in events:
        bucket = bucket_value(abs(e.delta_1s), delta_thresholds)
        delta_buckets[bucket]['total'] += 1
        if e.is_correct:
            delta_buckets[bucket]['correct'] += 1

    print(f"{'|delta|':<15} {'Total':>10} {'Correct':>10} {'Win Rate':>10}")
    print("-"*50)
    for bucket in ['<10', '10~20', '20~30', '30~50', '50~100', '>=100']:
        if bucket in delta_buckets:
            d = delta_buckets[bucket]
            wr = 100 * d['correct'] / d['total'] if d['total'] > 0 else 0
            print(f"{bucket:<15} {d['total']:>10} {d['correct']:>10} {wr:>9.1f}%")

    # 2. time_left별 분석
    print("\n" + "="*80)
    print("[2] 남은 시간별 승률")
    print("="*80)

    time_thresholds = [180, 360, 540, 720]  # 3분, 6분, 9분, 12분
    time_buckets = defaultdict(lambda: {'total': 0, 'correct': 0})

    for e in events:
        bucket = bucket_value(e.time_left, time_thresholds)
        time_buckets[bucket]['total'] += 1
        if e.is_correct:
            time_buckets[bucket]['correct'] += 1

    print(f"{'Time Left':<15} {'Total':>10} {'Correct':>10} {'Win Rate':>10}")
    print("-"*50)
    for bucket in ['<180', '180~360', '360~540', '540~720', '>=720']:
        if bucket in time_buckets:
            d = time_buckets[bucket]
            wr = 100 * d['correct'] / d['total'] if d['total'] > 0 else 0
            print(f"{bucket:<15} {d['total']:>10} {d['correct']:>10} {wr:>9.1f}%")

    # 3. distance별 분석 (시작가 대비)
    print("\n" + "="*80)
    print("[3] 시작가 대비 위치별 승률")
    print("="*80)

    dist_thresholds = [-100, -50, 0, 50, 100]
    dist_buckets = defaultdict(lambda: {'total': 0, 'correct': 0})

    for e in events:
        bucket = bucket_value(e.distance, dist_thresholds)
        dist_buckets[bucket]['total'] += 1
        if e.is_correct:
            dist_buckets[bucket]['correct'] += 1

    print(f"{'Distance':<15} {'Total':>10} {'Correct':>10} {'Win Rate':>10}")
    print("-"*50)
    for bucket in ['<-100', '-100~-50', '-50~0', '0~50', '50~100', '>=100']:
        if bucket in dist_buckets:
            d = dist_buckets[bucket]
            wr = 100 * d['correct'] / d['total'] if d['total'] > 0 else 0
            print(f"{bucket:<15} {d['total']:>10} {d['correct']:>10} {wr:>9.1f}%")

    # 4. delta 방향 vs distance 방향 조합
    print("\n" + "="*80)
    print("[4] Delta 방향 vs Distance 방향 조합")
    print("="*80)

    combo_buckets = defaultdict(lambda: {'total': 0, 'correct': 0})

    for e in events:
        delta_dir = 'UP' if e.delta_1s > 0 else 'DOWN'
        dist_dir = 'UP' if e.distance > 0 else 'DOWN'
        combo = f"delta={delta_dir}, dist={dist_dir}"
        combo_buckets[combo]['total'] += 1
        if e.is_correct:
            combo_buckets[combo]['correct'] += 1

    print(f"{'Combination':<30} {'Total':>10} {'Correct':>10} {'Win Rate':>10}")
    print("-"*65)
    for combo, d in sorted(combo_buckets.items()):
        wr = 100 * d['correct'] / d['total'] if d['total'] > 0 else 0
        print(f"{combo:<30} {d['total']:>10} {d['correct']:>10} {wr:>9.1f}%")

    # 5. 큰 delta (>=$30) + 조건별 분석
    print("\n" + "="*80)
    print("[5] 큰 Delta (>=$30) 발생 시 조건별 승률")
    print("="*80)

    big_deltas = [e for e in events if abs(e.delta_1s) >= 30]

    # 5a. 남은 시간별
    print("\n[5a] 큰 Delta + 남은 시간")
    time_big = defaultdict(lambda: {'total': 0, 'correct': 0})
    for e in big_deltas:
        bucket = '전반(>9분)' if e.time_left > 540 else '중반(3-9분)' if e.time_left > 180 else '후반(<3분)'
        time_big[bucket]['total'] += 1
        if e.is_correct:
            time_big[bucket]['correct'] += 1

    print(f"{'Phase':<15} {'Total':>10} {'Correct':>10} {'Win Rate':>10}")
    print("-"*50)
    for phase in ['전반(>9분)', '중반(3-9분)', '후반(<3분)']:
        if phase in time_big:
            d = time_big[phase]
            wr = 100 * d['correct'] / d['total'] if d['total'] > 0 else 0
            print(f"{phase:<15} {d['total']:>10} {d['correct']:>10} {wr:>9.1f}%")

    # 5b. 모멘텀 방향 일치
    print("\n[5b] 큰 Delta + 모멘텀 방향")
    momentum_big = defaultdict(lambda: {'total': 0, 'correct': 0})
    for e in big_deltas:
        delta_dir = 'UP' if e.delta_1s > 0 else 'DOWN'
        dist_dir = 'UP' if e.distance > 0 else 'DOWN'
        same = '일치' if delta_dir == dist_dir else '불일치'
        momentum_big[same]['total'] += 1
        if e.is_correct:
            momentum_big[same]['correct'] += 1

    print(f"{'Momentum':<15} {'Total':>10} {'Correct':>10} {'Win Rate':>10}")
    print("-"*50)
    for key in ['일치', '불일치']:
        if key in momentum_big:
            d = momentum_big[key]
            wr = 100 * d['correct'] / d['total'] if d['total'] > 0 else 0
            print(f"{key:<15} {d['total']:>10} {d['correct']:>10} {wr:>9.1f}%")

    # 6. 복합 조건 탐색
    print("\n" + "="*80)
    print("[6] 복합 조건 탐색 (승률 높은 조건)")
    print("="*80)

    conditions = []

    # 조건 조합 테스트
    for min_delta in [20, 30, 50]:
        for min_time_left in [180, 360, 540]:
            for momentum_match in [True, False, None]:
                filtered = []
                for e in events:
                    if abs(e.delta_1s) < min_delta:
                        continue
                    if e.time_left < min_time_left:
                        continue
                    if momentum_match is not None:
                        delta_dir = 'UP' if e.delta_1s > 0 else 'DOWN'
                        dist_dir = 'UP' if e.distance > 0 else 'DOWN'
                        if momentum_match and delta_dir != dist_dir:
                            continue
                        if not momentum_match and delta_dir == dist_dir:
                            continue
                    filtered.append(e)

                if len(filtered) >= 5:  # 최소 5개 샘플
                    correct = sum(1 for e in filtered if e.is_correct)
                    wr = 100 * correct / len(filtered)
                    conditions.append({
                        'min_delta': min_delta,
                        'min_time_left': min_time_left,
                        'momentum_match': momentum_match,
                        'count': len(filtered),
                        'correct': correct,
                        'win_rate': wr,
                    })

    # 승률 순 정렬
    conditions.sort(key=lambda x: -x['win_rate'])

    print(f"{'|Δ|>=':<8} {'TimeLeft>=':<12} {'Momentum':<12} {'Count':>8} {'Correct':>8} {'WinRate':>10}")
    print("-"*65)
    for c in conditions[:15]:
        momentum_str = '일치' if c['momentum_match'] is True else '불일치' if c['momentum_match'] is False else '무관'
        print(f"${c['min_delta']:<7} {c['min_time_left']:<12} {momentum_str:<12} {c['count']:>8} {c['correct']:>8} {c['win_rate']:>9.1f}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="Directory with candle JSONs")
    args = parser.parse_args()

    all_events = []
    candle_count = 0

    for filename in sorted(os.listdir(args.data)):
        if filename.endswith('.json'):
            filepath = os.path.join(args.data, filename)
            events = load_candle_events(filepath)
            if events:
                all_events.extend(events)
                candle_count += 1
                print(f"Loaded {filename}: {len(events)} events")

    print(f"\n총 {candle_count}개 캔들, {len(all_events)}개 delta 이벤트")

    if all_events:
        analyze_by_conditions(all_events)


if __name__ == "__main__":
    main()
