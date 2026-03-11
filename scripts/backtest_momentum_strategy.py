#!/usr/bin/env python3
"""
Momentum Strategy Backtest

조건:
1. |delta_1s| >= threshold
2. delta 방향 == distance 방향 (모멘텀 일치)
   - delta > 0 AND distance > 0 → BUY UP
   - delta < 0 AND distance < 0 → BUY DOWN

Usage:
    python backtest_momentum_strategy.py --data ./backtest_data/candles/btc/
"""
import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional


@dataclass
class Trade:
    """거래 기록"""
    candle_slug: str
    time_sec: int          # 진입 시간 (캔들 시작 후 초)
    side: str              # UP or DOWN
    entry_price: float     # 오더북 entry price
    delta_1s: float
    distance: float
    market_result: str     # 실제 결과
    pnl: float = 0.0

    @property
    def is_win(self) -> bool:
        return self.side == self.market_result


@dataclass
class BacktestConfig:
    """백테스트 설정"""
    delta_threshold: float = 30.0      # |delta| >= 이 값
    require_momentum_match: bool = True  # delta 방향 == distance 방향
    min_entry_price: float = 0.35      # 최소 entry price
    max_entry_price: float = 0.95      # 최대 entry price
    cooldown_seconds: int = 5          # 연속 진입 방지
    bet_amount: float = 1.0            # 베팅 금액


@dataclass
class BacktestResult:
    """백테스트 결과"""
    config: BacktestConfig
    trades: List[Trade] = field(default_factory=list)
    candles_tested: int = 0

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.is_win)

    @property
    def losses(self) -> int:
        return self.total_trades - self.wins

    @property
    def win_rate(self) -> float:
        return 100 * self.wins / self.total_trades if self.total_trades > 0 else 0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.total_trades if self.total_trades > 0 else 0


def load_candle_data(filepath: str) -> Dict:
    """캔들 데이터 로드"""
    with open(filepath, 'r') as f:
        return json.load(f)


def replay_orderbook(books: List[Dict], changes: List[Dict], token_to_side: Dict) -> Dict[int, Dict]:
    """오더북 replay → 각 초별 best_ask 추출"""
    # 초기화
    book_state = {}  # side -> {bids: {price: size}, asks: {price: size}}
    result = {}  # timestamp_sec -> {up_ask, down_ask}

    # 모든 이벤트를 시간순 정렬
    events = []

    for b in books:
        t = datetime.fromisoformat(b['time'].replace('Z', '+00:00'))
        events.append(('book', t, b))

    for c in changes:
        t = datetime.fromisoformat(c['time'].replace('Z', '+00:00'))
        events.append(('change', t, c))

    events.sort(key=lambda x: x[1])

    if not events:
        return result

    candle_start = None

    for event_type, event_time, data in events:
        if candle_start is None:
            candle_start = event_time

        time_sec = int((event_time - candle_start).total_seconds())
        if time_sec < 0:
            time_sec = 0

        if event_type == 'book':
            side = data['side'].lower()
            book_state[side] = {
                'bids': {float(p): s for p, s in data.get('bids', [])},
                'asks': {float(p): s for p, s in data.get('asks', [])},
            }
        else:  # change
            token_id = data['token_id']
            side = token_to_side.get(token_id, '').lower()
            if not side or side not in book_state:
                continue

            price = float(data['price'])
            size = int(data['size'])
            book_side = data['book_side']

            target = 'bids' if book_side == 'BUY' else 'asks'
            if size == 0:
                book_state[side][target].pop(price, None)
            else:
                book_state[side][target][price] = size

        # BBO 추출
        up_ask = None
        down_ask = None

        if 'up' in book_state and book_state['up']['asks']:
            up_ask = min(book_state['up']['asks'].keys())
        if 'down' in book_state and book_state['down']['asks']:
            down_ask = min(book_state['down']['asks'].keys())

        if up_ask is not None or down_ask is not None:
            result[time_sec] = {
                'up_ask': up_ask,
                'down_ask': down_ask,
            }

    return result


def get_entry_price_at_time(orderbook_timeline: Dict[int, Dict], time_sec: int, side: str) -> Optional[float]:
    """특정 시간의 entry price 조회"""
    # 해당 시간 또는 그 직전 시간의 오더북 찾기
    best_time = None
    for t in orderbook_timeline:
        if t <= time_sec:
            if best_time is None or t > best_time:
                best_time = t

    if best_time is None:
        return None

    ob = orderbook_timeline[best_time]
    if side == 'UP':
        return ob.get('up_ask')
    else:
        return ob.get('down_ask')


def backtest_candle(data: Dict, config: BacktestConfig) -> List[Trade]:
    """단일 캔들 백테스트"""
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

    # 오더북 타임라인 생성
    orderbook_timeline = replay_orderbook(
        data.get('orderbook_books', []),
        data.get('orderbook_changes', []),
        data.get('token_to_side', {}),
    )

    trades = []
    last_trade_time = -config.cooldown_seconds  # 첫 거래 가능하도록

    for i in range(1, len(prices)):
        curr = prices[i]
        prev = prices[i-1]

        curr_time = datetime.fromisoformat(curr['time'].replace('Z', '+00:00'))
        time_sec = int((curr_time - candle_start).total_seconds())

        if time_sec < 0 or time_sec > 900:
            continue

        # Cooldown 체크
        if time_sec - last_trade_time < config.cooldown_seconds:
            continue

        delta_1s = curr['price'] - prev['price']
        distance = curr['price'] - start_price

        # 조건 1: |delta| >= threshold
        if abs(delta_1s) < config.delta_threshold:
            continue

        # 방향 결정
        delta_dir = 'UP' if delta_1s > 0 else 'DOWN'
        dist_dir = 'UP' if distance > 0 else 'DOWN'

        # 조건 2: 모멘텀 일치 (옵션)
        if config.require_momentum_match and delta_dir != dist_dir:
            continue

        side = delta_dir  # delta 방향으로 베팅

        # Entry price 조회
        entry_price = get_entry_price_at_time(orderbook_timeline, time_sec, side)
        if entry_price is None:
            continue

        # Entry price 필터
        if entry_price < config.min_entry_price or entry_price > config.max_entry_price:
            continue

        # PnL 계산
        contracts = int(config.bet_amount / entry_price)
        if contracts <= 0:
            continue

        cost = contracts * entry_price
        if side == market_result:
            pnl = contracts * (1 - entry_price)  # WIN: (1 - entry) per contract
        else:
            pnl = -cost  # LOSS: lose entire cost

        trade = Trade(
            candle_slug=slug,
            time_sec=time_sec,
            side=side,
            entry_price=entry_price,
            delta_1s=delta_1s,
            distance=distance,
            market_result=market_result,
            pnl=pnl,
        )
        trades.append(trade)
        last_trade_time = time_sec

    return trades


def run_backtest(data_dir: str, config: BacktestConfig) -> BacktestResult:
    """전체 백테스트 실행"""
    result = BacktestResult(config=config)

    for filename in sorted(os.listdir(data_dir)):
        if not filename.endswith('.json'):
            continue

        filepath = os.path.join(data_dir, filename)
        try:
            data = load_candle_data(filepath)
            trades = backtest_candle(data, config)
            result.trades.extend(trades)
            result.candles_tested += 1
        except Exception as e:
            print(f"Error processing {filename}: {e}")

    return result


def print_result(result: BacktestResult):
    """결과 출력"""
    print("\n" + "="*80)
    print("BACKTEST RESULT")
    print("="*80)

    print(f"\n[Config]")
    print(f"  Delta Threshold: ${result.config.delta_threshold}")
    print(f"  Require Momentum Match: {result.config.require_momentum_match}")
    print(f"  Entry Price Range: {result.config.min_entry_price} ~ {result.config.max_entry_price}")
    print(f"  Cooldown: {result.config.cooldown_seconds}s")
    print(f"  Bet Amount: ${result.config.bet_amount}")

    print(f"\n[Summary]")
    print(f"  Candles Tested: {result.candles_tested}")
    print(f"  Total Trades: {result.total_trades}")
    print(f"  Wins: {result.wins}")
    print(f"  Losses: {result.losses}")
    print(f"  Win Rate: {result.win_rate:.1f}%")
    print(f"  Total PnL: ${result.total_pnl:.2f}")
    print(f"  Avg PnL/Trade: ${result.avg_pnl:.4f}")

    if result.trades:
        print(f"\n[Trade Details]")
        print(f"{'Candle':<35} {'Time':>5} {'Side':<5} {'Entry':>6} {'Delta':>8} {'Dist':>8} {'Result':<6} {'PnL':>8}")
        print("-"*90)

        for t in result.trades[:30]:  # 처음 30개만
            win_mark = '✓' if t.is_win else '✗'
            print(f"{t.candle_slug:<35} {t.time_sec:>5}s {t.side:<5} {t.entry_price:>6.3f} {t.delta_1s:>+8.2f} {t.distance:>+8.2f} {t.market_result:<6} {t.pnl:>+8.2f} {win_mark}")

        if len(result.trades) > 30:
            print(f"  ... and {len(result.trades) - 30} more trades")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="Directory with candle JSONs")
    parser.add_argument("--delta", type=float, default=30.0, help="Delta threshold")
    parser.add_argument("--no-momentum", action="store_true", help="Don't require momentum match")
    parser.add_argument("--min-ep", type=float, default=0.35, help="Min entry price")
    parser.add_argument("--cooldown", type=int, default=5, help="Cooldown seconds")
    args = parser.parse_args()

    config = BacktestConfig(
        delta_threshold=args.delta,
        require_momentum_match=not args.no_momentum,
        min_entry_price=args.min_ep,
        cooldown_seconds=args.cooldown,
    )

    print(f"Running backtest on {args.data}...")
    result = run_backtest(args.data, config)
    print_result(result)

    # 여러 조건 비교
    print("\n" + "="*80)
    print("PARAMETER COMPARISON")
    print("="*80)

    print(f"\n{'Delta':>8} {'Momentum':>10} {'Trades':>8} {'WinRate':>10} {'TotalPnL':>10}")
    print("-"*50)

    for delta in [20, 30, 50]:
        for momentum in [True, False]:
            cfg = BacktestConfig(
                delta_threshold=delta,
                require_momentum_match=momentum,
                min_entry_price=0.35,
                cooldown_seconds=5,
            )
            res = run_backtest(args.data, cfg)
            mom_str = "Yes" if momentum else "No"
            print(f"${delta:>7} {mom_str:>10} {res.total_trades:>8} {res.win_rate:>9.1f}% ${res.total_pnl:>9.2f}")


if __name__ == "__main__":
    main()
