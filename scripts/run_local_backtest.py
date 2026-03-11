#!/usr/bin/env python3
"""
로컬 Backtest 실행 스크립트

export_candle_data.py로 export한 JSON 파일을 사용해서 backtest를 실행합니다.
DB 연결 없이 로컬에서 실행 가능.

Usage:
    # 단일 파일
    python run_local_backtest.py ./backtest_data/btc/btc-updown-15m-1740000000.json

    # 폴더 내 모든 파일
    python run_local_backtest.py ./backtest_data/btc/

    # 결과를 CSV로 저장
    python run_local_backtest.py ./backtest_data/btc/ --output results.csv
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

# paper_trader/strategies를 import하기 위해 path 추가
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.insert(0, os.path.join(project_root, "services", "paper_trader"))
sys.path.insert(0, os.path.join(project_root, "packages", "strategy_config"))
sys.path.insert(0, os.path.join(project_root, "packages"))

from strategies import get_strategy
from strategy_config import (
    V12_5_MIN_ENTRY_PRICE, V12_5_MIN_ENTRY_PRICE_LATE,
    V12_5_EARLY_CUTOFF_MINUTES, V12_5_COOLDOWN_SECONDS, V12_5_MAX_SPREAD,
    BET_AMOUNT, MIN_LIQUIDITY,
)


def replay_orderbook(
    books: List[Dict],
    changes: List[Dict],
    token_to_side: Dict[str, str],
) -> tuple:
    """
    orderbook_books + orderbook_changes를 시간순 merge하여
    UP/DOWN BBO 시계열(snapshots)을 생성.
    """
    events = []
    for row in books:
        events.append({
            "time": datetime.fromisoformat(row["time"]),
            "type": "book",
            "side": row["side"],
            "bids": row.get("bids") or [],
            "asks": row.get("asks") or [],
        })
    for row in changes:
        side = token_to_side.get(row["token_id"])
        if side:
            events.append({
                "time": datetime.fromisoformat(row["time"]),
                "type": "change",
                "side": side,
                "price": float(row["price"]),
                "size": int(row["size"]),
                "book_side": row["book_side"],
            })

    events.sort(key=lambda e: e["time"])

    book_state: Dict[str, Dict[str, Dict[float, int]]] = {}
    last_bbo: Dict[str, tuple] = {}
    up_snaps: List[Dict] = []
    down_snaps: List[Dict] = []

    def emit_if_changed(side: str, time):
        state = book_state.get(side)
        if not state:
            return
        best_bid = max(state["bids"].keys()) if state["bids"] else 0.0
        best_ask = min(state["asks"].keys()) if state["asks"] else 1.0
        best_ask_size = state["asks"].get(best_ask, 0) if state["asks"] else 0
        prev = last_bbo.get(side)
        if prev and best_bid == prev[0] and best_ask == prev[1]:
            return
        last_bbo[side] = (best_bid, best_ask)
        snap = {
            "time": time,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "best_ask_size": best_ask_size,
            "mid_price": (best_bid + best_ask) / 2,
        }
        if side == "up":
            up_snaps.append(snap)
        else:
            down_snaps.append(snap)

    for ev in events:
        side = ev["side"]
        if ev["type"] == "book":
            bids = {float(p): int(s) for p, s in ev["bids"]}
            asks = {float(p): int(s) for p, s in ev["asks"]}
            book_state[side] = {"bids": bids, "asks": asks}
        else:
            state = book_state.get(side)
            if not state:
                continue
            book_map = state["bids"] if ev["book_side"] == "BUY" else state["asks"]
            if ev["size"] == 0:
                book_map.pop(ev["price"], None)
            else:
                book_map[ev["price"]] = ev["size"]
        emit_if_changed(side, ev["time"])

    return up_snaps, down_snaps


def run_candle_backtest(data: Dict) -> Dict[str, Any]:
    """JSON 데이터로 백테스트 실행"""
    meta = data["meta"]
    coin = meta["coin"]
    timeframe = meta["timeframe"]
    candle_start = datetime.fromisoformat(meta["candle_start"])
    candle_end = datetime.fromisoformat(meta["candle_end"])
    market_result = meta.get("market_result")

    # Orderbook replay
    up_snaps, down_snaps = replay_orderbook(
        data["orderbook_books"],
        data["orderbook_changes"],
        data["token_to_side"],
    )

    # Binance prices
    binance_prices = [
        {"time": datetime.fromisoformat(p["time"]), "price": p["price"]}
        for p in data["binance_prices"]
    ]

    # Strategy 생성
    strategy = get_strategy(
        "v12_5",
        min_entry_price=V12_5_MIN_ENTRY_PRICE,
        min_entry_price_late=V12_5_MIN_ENTRY_PRICE_LATE,
        early_cutoff_minutes=V12_5_EARLY_CUTOFF_MINUTES,
        cooldown_seconds=V12_5_COOLDOWN_SECONDS,
        max_spread=V12_5_MAX_SPREAD,
        bet_amount=BET_AMOUNT,
        min_liquidity=MIN_LIQUIDITY,
    )

    candle_key = f"{coin}_{timeframe}_{candle_start.isoformat()}"
    trades = []

    # Binance 가격 인덱싱
    binance_by_time = {p["time"]: p["price"] for p in binance_prices}
    binance_times = sorted(binance_by_time.keys())

    # Merge timestamps
    up_by_time = {s["time"]: s for s in up_snaps}
    down_by_time = {s["time"]: s for s in down_snaps}
    all_times = sorted(set(list(up_by_time.keys()) + list(down_by_time.keys())))

    last_up = None
    last_down = None
    prev_binance_price = None

    def get_binance_price_at(t: datetime) -> Optional[float]:
        if not binance_times:
            return None
        for bt in reversed(binance_times):
            if bt <= t:
                return binance_by_time[bt]
        return None

    for t in all_times:
        if t in up_by_time:
            last_up = up_by_time[t]
        if t in down_by_time:
            last_down = down_by_time[t]

        if not last_up or not last_down:
            continue

        curr_binance_price = get_binance_price_at(t)
        if curr_binance_price is None:
            continue

        # delta_1s 계산
        delta_1s = None
        if prev_binance_price is not None:
            delta_1s = curr_binance_price - prev_binance_price
        prev_binance_price = curr_binance_price

        if delta_1s is None or delta_1s == 0:
            continue

        elapsed_sec = (t - candle_start).total_seconds()
        elapsed_min = int(elapsed_sec // 60)
        sim_time = t.timestamp()

        up_ob = {
            "best_bid": last_up["best_bid"],
            "best_ask": last_up["best_ask"],
            "best_ask_size": last_up.get("best_ask_size", 0),
            "mid_price": last_up["mid_price"],
        }
        down_ob = {
            "best_bid": last_down["best_bid"],
            "best_ask": last_down["best_ask"],
            "best_ask_size": last_down.get("best_ask_size", 0),
            "mid_price": last_down["mid_price"],
        }

        signal = strategy.should_enter(
            coin=coin,
            timeframe=timeframe,
            elapsed_min=elapsed_min,
            up_orderbook=up_ob,
            down_orderbook=down_ob,
            candle_key=candle_key,
            delta_1s=delta_1s,
            sim_time=sim_time,
        )

        if signal:
            trades.append({
                "time": t.isoformat(),
                "elapsed_min": elapsed_min,
                "side": signal.side.upper(),
                "entry_price": signal.price,
                "contracts": signal.contracts,
                "cost": signal.contracts * signal.price,
                "reason": signal.reason,
                "delta_1s": delta_1s,
                "outcome": None,
                "pnl": None,
            })

    # Apply market result
    if market_result:
        for trade in trades:
            if market_result == "FLAT":
                trade["outcome"] = "FLAT"
                trade["pnl"] = 0.0
            elif trade["side"] == market_result:
                trade["outcome"] = "WIN"
                trade["pnl"] = round(trade["contracts"] * 1.0 - trade["cost"], 4)
            else:
                trade["outcome"] = "LOSS"
                trade["pnl"] = round(-trade["cost"], 4)

    return {
        "meta": meta,
        "trades": trades,
        "summary": {
            "total_trades": len(trades),
            "wins": sum(1 for t in trades if t["outcome"] == "WIN"),
            "losses": sum(1 for t in trades if t["outcome"] == "LOSS"),
            "total_pnl": round(sum(t["pnl"] or 0 for t in trades), 4),
        }
    }


def main():
    parser = argparse.ArgumentParser(description="Run local backtest")
    parser.add_argument("input", help="JSON file or directory")
    parser.add_argument("--output", help="Output CSV file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # 입력 파일 목록 생성
    if os.path.isdir(args.input):
        files = [
            os.path.join(args.input, f)
            for f in sorted(os.listdir(args.input))
            if f.endswith(".json")
        ]
    else:
        files = [args.input]

    if not files:
        print("No JSON files found")
        return

    print(f"Running backtest on {len(files)} file(s)...")
    print()

    all_results = []
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0

    for filepath in files:
        with open(filepath, "r") as f:
            data = json.load(f)

        result = run_candle_backtest(data)
        all_results.append(result)

        meta = result["meta"]
        summary = result["summary"]

        total_trades += summary["total_trades"]
        total_wins += summary["wins"]
        total_losses += summary["losses"]
        total_pnl += summary["total_pnl"]

        if args.verbose or summary["total_trades"] > 0:
            print(f"{meta['market_slug']}: "
                  f"trades={summary['total_trades']}, "
                  f"W/L={summary['wins']}/{summary['losses']}, "
                  f"PnL=${summary['total_pnl']:.2f}, "
                  f"result={meta.get('market_result', 'N/A')}")

            if args.verbose:
                for trade in result["trades"]:
                    print(f"  {trade['time']} | {trade['side']} @ {trade['entry_price']:.3f} | "
                          f"delta=${trade['delta_1s']:.4f} | {trade['outcome']} | ${trade['pnl']:.2f}")

    print()
    print("=" * 60)
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
    print(f"TOTAL: {len(files)} candles, {total_trades} trades")
    print(f"  Wins: {total_wins}, Losses: {total_losses}, Win Rate: {win_rate:.1f}%")
    print(f"  Total PnL: ${total_pnl:.2f}")
    print("=" * 60)

    # CSV 출력
    if args.output:
        import csv
        with open(args.output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "market_slug", "time", "elapsed_min", "side",
                "entry_price", "contracts", "cost", "delta_1s",
                "outcome", "pnl", "market_result"
            ])
            for result in all_results:
                for trade in result["trades"]:
                    writer.writerow([
                        result["meta"]["market_slug"],
                        trade["time"],
                        trade["elapsed_min"],
                        trade["side"],
                        trade["entry_price"],
                        trade["contracts"],
                        trade["cost"],
                        trade["delta_1s"],
                        trade["outcome"],
                        trade["pnl"],
                        result["meta"].get("market_result"),
                    ])
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
