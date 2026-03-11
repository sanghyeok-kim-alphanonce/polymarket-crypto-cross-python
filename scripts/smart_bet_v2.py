#!/usr/bin/env python3
"""
스마트 베팅 v2 - 기대값 최적화

핵심: 이기면 +target, 지면 -total_bet
기대값 = win_rate * target - (1 - win_rate) * total_bet

수익 조건: win_rate > total_bet / (total_bet + target)
"""

import subprocess
from collections import defaultdict


def calculate_smart_bets(price: float, target: float, max_n: int) -> list:
    """스마트 베팅 시퀀스"""
    profit_rate = (1 - price) / price

    bets = []
    totals = [0.0, 0.0]

    for n in range(max_n):
        direction = n % 2
        opposite = 1 - direction

        same_total = totals[direction]
        opposite_total = totals[opposite]

        bet = (opposite_total + target - same_total * profit_rate) / profit_rate
        bet = max(bet, target / profit_rate)

        bets.append(bet)
        totals[direction] += bet

    return bets


def calculate_expected_value(bets: list, win_rate: float, target: float) -> dict:
    """기대값 계산"""
    total_bet = sum(bets)
    ev = win_rate * target - (1 - win_rate) * total_bet
    roi = ev / total_bet * 100 if total_bet > 0 else 0

    # 손익분기 승률
    break_even = total_bet / (total_bet + target)

    return {
        "total_bet": total_bet,
        "expected_value": ev,
        "roi": roi,
        "break_even_wr": break_even * 100,
        "bets": bets,
    }


def main():
    print("=" * 80)
    print("스마트 베팅 기대값 분석")
    print("=" * 80)

    # 가격별, max crossing별 분석
    prices = [0.55, 0.60]
    max_crossings = [1, 2, 3, 4, 5]
    targets = [2, 4]

    win_rates = {
        (720, 780): 0.846,  # 12-13분: 84.6%
        (600, 720): 0.500,  # 10-12분: 50%
        (780, 840): 0.588,  # 13-14분: 58.8%
    }

    print(f"\n{'설정':<40} | {'Total':>8} | {'손익분기':>8} | {'필요승률':>8} | {'EV@85%':>10} | {'ROI':>7}")
    print("-" * 100)

    for price in prices:
        for target in targets:
            for max_n in max_crossings:
                bets = calculate_smart_bets(price, target, max_n)
                result = calculate_expected_value(bets, 0.85, target)  # 85% 승률 가정

                name = f"p={price}, target=${target}, max={max_n}"
                bet_str = ",".join([f"${b:.1f}" for b in bets])

                print(f"{name:<40} | ${result['total_bet']:>7.1f} | "
                      f"{result['break_even_wr']:>7.1f}% | "
                      f">{result['break_even_wr']:.0f}% | "
                      f"${result['expected_value']:>+9.2f} | "
                      f"{result['roi']:>+6.1f}%")

    # 실제 데이터 승률별 최적 설정 찾기
    print("\n" + "=" * 80)
    print("실제 승률 기반 최적 설정 (12-13분, 승률 84.6%)")
    print("=" * 80)

    actual_wr = 0.846
    for price in [0.55, 0.58, 0.60]:
        print(f"\n가격 {price}:")
        for max_n in [1, 2, 3]:
            for target in [2, 4, 6]:
                bets = calculate_smart_bets(price, target, max_n)
                result = calculate_expected_value(bets, actual_wr, target)

                if result["expected_value"] > 0:
                    bet_str = " → ".join([f"${b:.1f}" for b in bets])
                    print(f"  max={max_n}, target=${target}: {bet_str}")
                    print(f"    Total=${result['total_bet']:.1f}, "
                          f"손익분기={result['break_even_wr']:.1f}%, "
                          f"EV=${result['expected_value']:+.2f}, "
                          f"ROI={result['roi']:+.1f}%")

    # 단순 마팅게일과 비교
    print("\n" + "=" * 80)
    print("단순 마팅게일 vs 스마트 베팅 비교")
    print("=" * 80)

    # 마팅게일: 첫 베팅 * 2^n
    def martingale_ev(first_bet: float, max_n: int, price: float, win_rate: float):
        """마팅게일 기대값 (마지막 방향 WIN/LOSS 기준)"""
        profit_rate = (1 - price) / price

        # n회 crossing 후 마지막 방향이 WIN
        # 홀수번째: 첫 방향, 짝수번째: 반대 방향
        bets = [first_bet * (2 ** i) for i in range(max_n)]
        total = sum(bets)

        # 마지막 방향의 베팅들 (1,3,5... 또는 2,4,6...)
        last_dir_bets = sum(bets[i] for i in range(max_n) if i % 2 == (max_n - 1) % 2)
        opposite_bets = total - last_dir_bets

        win_profit = last_dir_bets * profit_rate - opposite_bets
        loss = -total

        ev = win_rate * win_profit + (1 - win_rate) * loss
        return {"bets": bets, "total": total, "win_profit": win_profit, "ev": ev}

    print(f"\n가격=0.58, 승률=84.6%:")
    price = 0.58
    wr = 0.846

    for max_n in [1, 2, 3]:
        # 마팅게일
        martin = martingale_ev(4, max_n, price, wr)
        # 스마트 (같은 total에 맞춰서)
        target = martin["win_profit"]  # 마팅게일 win시 수익과 동일
        smart_bets = calculate_smart_bets(price, target, max_n)
        smart = calculate_expected_value(smart_bets, wr, target)

        print(f"\n  max={max_n}:")
        print(f"    마팅게일 [4,8,16...]: Total=${martin['total']:.1f}, "
              f"WIN=${martin['win_profit']:+.1f}, EV=${martin['ev']:+.2f}")
        print(f"    스마트 (target=${target:.1f}): Total=${smart['total_bet']:.1f}, "
              f"WIN=${target:+.1f}, EV=${smart['expected_value']:+.2f}")


if __name__ == "__main__":
    main()
