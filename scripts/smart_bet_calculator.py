#!/usr/bin/env python3
"""
스마트 베팅 계산기

목표: 마지막 베팅이 이기면 항상 고정 수익 (target_profit)

수학:
- 가격 p에서 수익률 = (1-p)/p
- n번째 베팅 = (반대방향_총베팅 + 목표수익 - 같은방향_총베팅 * 수익률) / 수익률

예: p=0.6, target=4
- 수익률 = 0.4/0.6 = 0.667
- bet1 = 4 / 0.667 = 6
- bet2 = (6 + 4) / 0.667 = 15
- bet3 = (15 + 4 - 6*0.667) / 0.667 = 22.5
"""


def calculate_smart_bets(price: float, target_profit: float, max_crossings: int) -> list:
    """
    스마트 베팅 시퀀스 계산

    Returns: [(bet_amount, direction_index), ...]
    direction_index: 0=첫방향, 1=반대방향
    """
    profit_rate = (1 - price) / price

    bets = []
    totals = [0.0, 0.0]  # [첫방향 총액, 반대방향 총액]

    for n in range(max_crossings):
        direction = n % 2  # 0, 1, 0, 1, ...
        opposite = 1 - direction

        same_total = totals[direction]
        opposite_total = totals[opposite]

        # 이 베팅이 이기면 target_profit 수익이 나도록 계산
        bet = (opposite_total + target_profit - same_total * profit_rate) / profit_rate

        # 최소 베팅 보장 (음수 방지)
        bet = max(bet, target_profit / profit_rate)

        bets.append(bet)
        totals[direction] += bet

    return bets


def verify_bets(bets: list, price: float, target_profit: float):
    """베팅 시퀀스 검증"""
    profit_rate = (1 - price) / price

    print(f"\n가격: {price}, 수익률: {profit_rate:.3f}, 목표 수익: ${target_profit}")
    print("-" * 60)
    print(f"{'#':>2} | {'방향':>4} | {'베팅':>10} | 이길 시 순수익")
    print("-" * 60)

    totals = [0.0, 0.0]

    for n, bet in enumerate(bets):
        direction = n % 2
        dir_name = "A" if direction == 0 else "B"

        # 이 베팅까지 포함해서 계산
        totals[direction] += bet

        # 이 베팅이 마지막이고 이겼을 때
        win_profit = totals[direction] * profit_rate
        loss = totals[1 - direction]
        net = win_profit - loss

        print(f"{n+1:>2} | {dir_name:>4} | ${bet:>9.2f} | ${net:>+.2f}")


def simulate_smart_strategy():
    """실제 데이터로 스마트 전략 시뮬레이션"""
    import subprocess
    from collections import defaultdict

    # 실제 거래 데이터 가져오기
    cmd = """
    docker exec cointest-paper-trade-db psql -U paper -d paper_trade -t -A -F'|' -c "
    SELECT
        to_char(candle_start_time AT TIME ZONE 'UTC', 'YYYY-MM-DD_HH24-MI') as candle,
        EXTRACT(EPOCH FROM (time - candle_start_time))::int as entry_sec,
        side,
        fill_price::float as price,
        outcome
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
        if len(parts) >= 5:
            trades.append({
                "candle": parts[0],
                "entry_sec": int(parts[1]),
                "side": parts[2],
                "price": float(parts[3]),
                "outcome": parts[4],
            })

    # 캔들별 그룹화
    candles = defaultdict(list)
    for t in trades:
        candles[t["candle"]].append(t)

    # 전략 설정
    configs = [
        # (이름, entry_start, entry_end, target_profit, max_crossings, assumed_price)
        ("현재 12-13m, target=$4, p=0.6", 720, 780, 4, 5, 0.6),
        ("12-13m, target=$4, p=0.55", 720, 780, 4, 5, 0.55),
        ("12-13m, target=$2, p=0.6", 720, 780, 2, 5, 0.6),
        ("12-14m, target=$4, p=0.6", 720, 840, 4, 5, 0.6),
        ("10-14m, target=$4, p=0.6", 600, 840, 4, 5, 0.6),
    ]

    print("\n" + "=" * 100)
    print("스마트 베팅 전략 시뮬레이션 (실제 가격 사용)")
    print("=" * 100)
    print(f"{'전략':<35} | {'캔들':>4} | {'승률':>6} | {'PnL':>10} | {'총베팅':>10} | {'ROI':>7} | {'MaxBet':>8}")
    print("-" * 100)

    for name, start, end, target, max_cross, assumed_p in configs:
        # 가정 가격으로 베팅 시퀀스 계산
        bet_seq = calculate_smart_bets(assumed_p, target, max_cross)

        total_pnl = 0.0
        total_bet = 0.0
        max_bet = 0.0
        entered = 0
        wins = 0

        for candle_name, candle_trades in candles.items():
            # 시간 필터
            valid = [t for t in candle_trades if start <= t["entry_sec"] < end]
            if not valid:
                continue

            entries = valid[:max_cross]
            entered += 1

            # 마지막 거래 결과
            last = entries[-1]
            is_win = last["outcome"] == "WIN"
            if is_win:
                wins += 1

            # PnL 계산 (실제 가격 사용)
            totals = [0.0, 0.0]  # [A방향 총액, B방향 총액]
            candle_pnl = 0.0
            candle_bet = 0.0

            for i, t in enumerate(entries):
                direction = i % 2
                bet = bet_seq[i] if i < len(bet_seq) else bet_seq[-1]
                actual_price = t["price"]
                actual_profit_rate = (1 - actual_price) / actual_price

                totals[direction] += bet
                candle_bet += bet
                max_bet = max(max_bet, bet)

            # 마지막 방향 결정
            last_dir = (len(entries) - 1) % 2

            if is_win:
                # 마지막 방향 이김
                win_profit = totals[last_dir] * ((1 - last["price"]) / last["price"])
                loss = totals[1 - last_dir]
                candle_pnl = win_profit - loss
            else:
                # 마지막 방향 짐
                # 반대 방향이 이김 (하지만 실제로는 마지막 결과 = 캔들 결과)
                # 여기서는 마지막 거래 결과로 전체 캔들 결과를 판단
                candle_pnl = -candle_bet  # 단순화: 마지막이 지면 전체 손실

            total_pnl += candle_pnl
            total_bet += candle_bet

        if entered == 0:
            continue

        win_rate = wins / entered * 100
        roi = total_pnl / total_bet * 100 if total_bet > 0 else 0

        print(f"{name:<35} | {entered:>4} | {win_rate:>5.1f}% | "
              f"${total_pnl:>+9.2f} | ${total_bet:>9.2f} | {roi:>+6.1f}% | ${max_bet:>7.2f}")


def main():
    # 베팅 시퀀스 예시
    print("=" * 60)
    print("스마트 베팅 시퀀스 계산 예시")
    print("=" * 60)

    for price in [0.55, 0.60, 0.65]:
        bets = calculate_smart_bets(price, target_profit=4, max_crossings=5)
        verify_bets(bets, price, target_profit=4)

    # 실제 데이터 시뮬레이션
    simulate_smart_strategy()


if __name__ == "__main__":
    main()
