#!/usr/bin/env python3
"""
캔들 데이터 분석 - Binance 가격 시그널 탐색

각 캔들에서:
1. Binance 가격 변화 패턴
2. 오더북 변화와의 상관관계
3. 결과(UP/DOWN) 예측 가능성

Usage:
    python analyze_candle_signals.py --data ./backtest_data/candles/btc/
"""
import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import statistics


@dataclass
class CandleAnalysis:
    """캔들 분석 결과"""
    slug: str
    market_result: str  # UP, DOWN, FLAT

    # Binance 가격 통계
    binance_start: float
    binance_end: float
    binance_high: float
    binance_low: float
    binance_delta: float  # end - start
    binance_volatility: float  # std dev

    # 시간대별 delta
    delta_0_5m: float  # 0-5분 delta
    delta_5_10m: float  # 5-10분 delta
    delta_10_15m: float  # 10-15분 delta

    # 큰 움직임 (spikes)
    max_1s_up: float  # 최대 1초 상승
    max_1s_down: float  # 최대 1초 하락
    spike_count_30: int  # $30 이상 움직임 횟수

    # 오더북 초기 상태
    up_ask_start: float
    down_ask_start: float

    # 예측 신호
    early_direction: str  # 전반(0-5분) 기준 방향
    mid_direction: str  # 중반(0-10분) 기준 방향


def load_candle_data(filepath: str) -> Dict:
    """캔들 데이터 로드"""
    with open(filepath, 'r') as f:
        return json.load(f)


def parse_time(timestr: str) -> datetime:
    """ISO 시간 문자열 파싱"""
    return datetime.fromisoformat(timestr.replace('Z', '+00:00'))


def analyze_binance_prices(prices: List[Dict], candle_start: datetime) -> Dict:
    """Binance 가격 분석"""
    if not prices:
        return {}

    price_values = [p['price'] for p in prices]
    times = [parse_time(p['time']) for p in prices]

    # 기본 통계
    start_price = price_values[0]
    end_price = price_values[-1]

    # 1초 delta 계산
    deltas_1s = []
    for i in range(1, len(price_values)):
        deltas_1s.append(price_values[i] - price_values[i-1])

    # 시간대별 분류
    prices_0_5m = []
    prices_5_10m = []
    prices_10_15m = []

    for p, t in zip(price_values, times):
        elapsed = (t - candle_start).total_seconds()
        if elapsed <= 300:
            prices_0_5m.append(p)
        elif elapsed <= 600:
            prices_5_10m.append(p)
        else:
            prices_10_15m.append(p)

    # 시간대별 delta
    delta_0_5m = (prices_0_5m[-1] - prices_0_5m[0]) if len(prices_0_5m) >= 2 else 0
    delta_5_10m = (prices_5_10m[-1] - prices_5_10m[0]) if len(prices_5_10m) >= 2 else 0
    delta_10_15m = (prices_10_15m[-1] - prices_10_15m[0]) if len(prices_10_15m) >= 2 else 0

    # spike 분석
    max_up = max(deltas_1s) if deltas_1s else 0
    max_down = min(deltas_1s) if deltas_1s else 0
    spike_count_30 = sum(1 for d in deltas_1s if abs(d) >= 30)

    return {
        'start': start_price,
        'end': end_price,
        'high': max(price_values),
        'low': min(price_values),
        'delta': end_price - start_price,
        'volatility': statistics.stdev(price_values) if len(price_values) > 1 else 0,
        'delta_0_5m': delta_0_5m,
        'delta_5_10m': delta_5_10m,
        'delta_10_15m': delta_10_15m,
        'max_1s_up': max_up,
        'max_1s_down': max_down,
        'spike_count_30': spike_count_30,
        'early_direction': 'UP' if delta_0_5m > 0 else 'DOWN',
        'mid_direction': 'UP' if (delta_0_5m + delta_5_10m) > 0 else 'DOWN',
    }


def get_initial_orderbook(books: List[Dict]) -> Tuple[float, float]:
    """초기 오더북에서 UP/DOWN ask 가격 추출"""
    up_ask = 0.5
    down_ask = 0.5

    for book in books[:4]:  # 처음 몇 개만 확인
        side = book.get('side', '').lower()
        asks = book.get('asks', [])
        if asks:
            best_ask = min(a[0] for a in asks)
            if side == 'up':
                up_ask = best_ask
            elif side == 'down':
                down_ask = best_ask

    return up_ask, down_ask


def analyze_candle(filepath: str) -> Optional[CandleAnalysis]:
    """단일 캔들 분석"""
    data = load_candle_data(filepath)

    meta = data.get('meta', {})
    market_result = meta.get('market_result')

    if not market_result or market_result == 'FLAT':
        return None

    candle_start = parse_time(meta['candle_start'])

    # Binance 분석
    binance = analyze_binance_prices(data.get('binance_prices', []), candle_start)
    if not binance:
        return None

    # 오더북 초기 상태
    up_ask, down_ask = get_initial_orderbook(data.get('orderbook_books', []))

    return CandleAnalysis(
        slug=meta['market_slug'],
        market_result=market_result,
        binance_start=binance['start'],
        binance_end=binance['end'],
        binance_high=binance['high'],
        binance_low=binance['low'],
        binance_delta=binance['delta'],
        binance_volatility=binance['volatility'],
        delta_0_5m=binance['delta_0_5m'],
        delta_5_10m=binance['delta_5_10m'],
        delta_10_15m=binance['delta_10_15m'],
        max_1s_up=binance['max_1s_up'],
        max_1s_down=binance['max_1s_down'],
        spike_count_30=binance['spike_count_30'],
        up_ask_start=up_ask,
        down_ask_start=down_ask,
        early_direction=binance['early_direction'],
        mid_direction=binance['mid_direction'],
    )


def print_analysis_summary(analyses: List[CandleAnalysis]):
    """분석 결과 요약 출력"""
    print(f"\n{'='*80}")
    print(f"총 {len(analyses)}개 캔들 분석 완료")
    print(f"{'='*80}\n")

    # 결과 분포
    up_count = sum(1 for a in analyses if a.market_result == 'UP')
    down_count = sum(1 for a in analyses if a.market_result == 'DOWN')
    print(f"[결과 분포]")
    print(f"  UP: {up_count} ({100*up_count/len(analyses):.1f}%)")
    print(f"  DOWN: {down_count} ({100*down_count/len(analyses):.1f}%)")

    # 전반(0-5분) 방향 기준 예측력
    print(f"\n[전반(0-5분) Binance 방향 → 결과]")
    early_correct = sum(1 for a in analyses if a.early_direction == a.market_result)
    print(f"  정확도: {early_correct}/{len(analyses)} ({100*early_correct/len(analyses):.1f}%)")

    # 중반(0-10분) 방향 기준 예측력
    print(f"\n[중반(0-10분) Binance 방향 → 결과]")
    mid_correct = sum(1 for a in analyses if a.mid_direction == a.market_result)
    print(f"  정확도: {mid_correct}/{len(analyses)} ({100*mid_correct/len(analyses):.1f}%)")

    # 전체 delta vs 결과
    print(f"\n[전체 Binance Delta → 결과]")
    delta_correct = sum(1 for a in analyses
                       if (a.binance_delta > 0 and a.market_result == 'UP') or
                          (a.binance_delta < 0 and a.market_result == 'DOWN'))
    print(f"  정확도: {delta_correct}/{len(analyses)} ({100*delta_correct/len(analyses):.1f}%)")

    # 큰 spike 분석
    print(f"\n[Spike 분석 ($30 이상)]")
    spike_candles = [a for a in analyses if a.spike_count_30 > 0]
    print(f"  Spike 발생 캔들: {len(spike_candles)}/{len(analyses)}")
    if spike_candles:
        avg_spikes = statistics.mean(a.spike_count_30 for a in spike_candles)
        print(f"  평균 Spike 횟수: {avg_spikes:.1f}")

    # 상세 테이블
    print(f"\n{'='*80}")
    print(f"[캔들별 상세]")
    print(f"{'='*80}")
    print(f"{'Slug':<35} {'Result':<6} {'Δ0-5m':>10} {'Δ5-10m':>10} {'Δ10-15m':>10} {'Total':>10} {'Early':>6} {'Correct':>7}")
    print(f"{'-'*80}")

    for a in analyses:
        early_match = '✓' if a.early_direction == a.market_result else '✗'
        print(f"{a.slug:<35} {a.market_result:<6} {a.delta_0_5m:>10.2f} {a.delta_5_10m:>10.2f} {a.delta_10_15m:>10.2f} {a.binance_delta:>10.2f} {a.early_direction:>6} {early_match:>7}")

    # 핵심 인사이트
    print(f"\n{'='*80}")
    print("[핵심 인사이트]")
    print(f"{'='*80}")

    # 전반 방향과 결과가 다른 경우 분석
    reversals = [a for a in analyses if a.early_direction != a.market_result]
    if reversals:
        print(f"\n1. 전반 방향과 결과가 다른 캔들 (역전): {len(reversals)}개")
        for r in reversals:
            print(f"   - {r.slug}: 전반 {r.early_direction} → 결과 {r.market_result}")
            print(f"     Δ0-5m={r.delta_0_5m:+.2f}, Δ5-10m={r.delta_5_10m:+.2f}, Δ10-15m={r.delta_10_15m:+.2f}")


def main():
    parser = argparse.ArgumentParser(description="Analyze candle data for signals")
    parser.add_argument("--data", required=True, help="Directory containing candle JSON files")
    args = parser.parse_args()

    # 모든 JSON 파일 로드
    analyses = []
    for filename in sorted(os.listdir(args.data)):
        if filename.endswith('.json'):
            filepath = os.path.join(args.data, filename)
            print(f"Analyzing {filename}...")
            analysis = analyze_candle(filepath)
            if analysis:
                analyses.append(analysis)

    if not analyses:
        print("No valid candles found")
        return

    print_analysis_summary(analyses)


if __name__ == "__main__":
    main()
