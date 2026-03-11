"""
Backtest Runner

DB에서 오더북 데이터 로드 → BBO 리플레이 → 전략 실행.
paper_trader/strategies를 직접 사용하여 전략 코드 단일화.
"""
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any

import asyncpg

from strategies import get_strategy
from config import (
    STRATEGY_NAME,
    V12_5_MIN_ENTRY_PRICE, V12_5_MIN_ENTRY_PRICE_LATE,
    V12_5_EARLY_CUTOFF_MINUTES, V12_5_COOLDOWN_SECONDS, V12_5_MAX_SPREAD,
    BET_AMOUNT, MIN_LIQUIDITY,
    # Crossing config
    CROSSING_FIRST_BET, CROSSING_SUBSEQUENT_BET,
    CROSSING_MAX_COUNT, CROSSING_COUNT_START_SECONDS, CROSSING_CUTOFF_SECONDS,
    CROSSING_MIN_ENTRY_PRICE, CROSSING_MAX_ENTRY_PRICE,
    CROSSING_V2_CUTOFF_SECONDS,
)


# =========================================================================
# Orderbook Replay
# =========================================================================

def replay_orderbook(
    books: List[Dict],
    changes: List[Dict],
    token_to_side: Dict[str, str],
) -> Tuple[List[Dict], List[Dict]]:
    """
    orderbook_books + orderbook_changes를 시간순 merge하여
    UP/DOWN BBO 시계열(snapshots)을 생성.

    Returns:
        (up_snapshots, down_snapshots)
        각 snapshot: {"time": datetime, "best_bid": float, "best_ask": float, "mid_price": float}
    """
    # Build event list
    events = []
    for row in books:
        events.append({
            "time": row["time"],
            "type": "book",
            "side": row["side"],
            "bids": row.get("bids") or [],
            "asks": row.get("asks") or [],
        })
    for row in changes:
        side = token_to_side.get(row["token_id"])
        if side:
            events.append({
                "time": row["time"],
                "type": "change",
                "side": side,
                "price": float(row["price"]),
                "size": int(row["size"]),
                "book_side": row["book_side"],
            })

    events.sort(key=lambda e: e["time"])

    # Replay
    book_state: Dict[str, Dict[str, Dict[float, int]]] = {}
    last_bbo: Dict[str, Tuple[float, float]] = {}
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


# =========================================================================
# Strategy Execution
# =========================================================================

def run_candle_backtest(
    coin: str,
    timeframe: str,
    candle_start: datetime,
    candle_end: datetime,
    up_snaps: List[Dict],
    down_snaps: List[Dict],
    binance_prices: List[Dict],
    market_result: Optional[str],
) -> Dict[str, Any]:
    """
    BBO snapshots + Binance 가격을 전략에 통과시켜 백테스트 실행.

    Args:
        binance_prices: [{"time": datetime, "price": float}, ...]

    Returns:
        {"trades": [...]}
    """
    strategy = get_strategy(
        STRATEGY_NAME,
        min_entry_price=V12_5_MIN_ENTRY_PRICE,
        min_entry_price_late=V12_5_MIN_ENTRY_PRICE_LATE,
        early_cutoff_minutes=V12_5_EARLY_CUTOFF_MINUTES,
        cooldown_seconds=V12_5_COOLDOWN_SECONDS,  # 실제 cooldown 적용
        max_spread=V12_5_MAX_SPREAD,
        bet_amount=BET_AMOUNT,
        min_liquidity=MIN_LIQUIDITY,
    )

    candle_key = f"{coin}_{timeframe}_{candle_start.isoformat()}"
    trades = []

    # Binance 가격 시계열 (시간순)
    binance_times = sorted([p["time"] for p in binance_prices])
    binance_by_time = {p["time"]: p["price"] for p in binance_prices}

    # Orderbook snapshots 시간순 정렬
    up_snaps_sorted = sorted(up_snaps, key=lambda x: x["time"])
    down_snaps_sorted = sorted(down_snaps, key=lambda x: x["time"])

    def get_orderbook_at(snaps_sorted: List[Dict], t: datetime) -> Optional[Dict]:
        """t 시점 또는 직전의 오더북 조회"""
        result = None
        for s in snaps_sorted:
            if s["time"] <= t:
                result = s
            else:
                break
        return result

    prev_binance_price = None

    # Binance 가격 중심으로 루프 (paper trading과 동일한 방식)
    for t in binance_times:
        curr_binance_price = binance_by_time[t]

        # delta_1s 계산 (현재 - 이전)
        delta_1s = None
        if prev_binance_price is not None:
            delta_1s = curr_binance_price - prev_binance_price
        prev_binance_price = curr_binance_price

        if delta_1s is None or delta_1s == 0:
            continue

        # 해당 시점의 오더북 조회
        last_up = get_orderbook_at(up_snaps_sorted, t)
        last_down = get_orderbook_at(down_snaps_sorted, t)

        if not last_up or not last_down:
            continue

        elapsed_sec = (t - candle_start).total_seconds()
        elapsed_min = int(elapsed_sec // 60)
        sim_time = t.timestamp()  # 시뮬레이션 시간 (unix timestamp)

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
                "time": t.isoformat() if isinstance(t, datetime) else str(t),
                "elapsed_min": elapsed_min,
                "side": signal.side.upper(),
                "entry_price": signal.price,
                "contracts": signal.contracts,
                "cost": signal.contracts * signal.price,
                "reason": signal.reason,
                "outcome": None,
                "pnl": None,
            })

    # Apply market result to trades
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

    return {"trades": trades}


def run_crossing_backtest(
    coin: str,
    timeframe: str,
    candle_start: datetime,
    candle_end: datetime,
    up_snaps: List[Dict],
    down_snaps: List[Dict],
    binance_prices: List[Dict],
    market_result: Optional[str],
    strategy_name: str = "crossing_v2",
) -> Dict[str, Any]:
    """
    Crossing 전략 백테스트.
    가격이 candle open을 교차할 때 진입.

    Args:
        strategy_name: "crossing" (5회 제한) or "crossing_v2" (무제한)
    """
    # 전략 설정
    if strategy_name == "crossing_v2":
        cutoff_seconds = CROSSING_V2_CUTOFF_SECONDS
        strategy = get_strategy(
            "crossing_v2",
            first_bet_amount=CROSSING_FIRST_BET,
            subsequent_bet_amount=CROSSING_SUBSEQUENT_BET,
            cutoff_seconds=cutoff_seconds,
            min_entry_price=CROSSING_MIN_ENTRY_PRICE,
            max_entry_price=CROSSING_MAX_ENTRY_PRICE,
        )
    else:
        cutoff_seconds = CROSSING_CUTOFF_SECONDS
        strategy = get_strategy(
            "crossing",
            first_bet_amount=CROSSING_FIRST_BET,
            subsequent_bet_amount=CROSSING_SUBSEQUENT_BET,
            max_crossings=CROSSING_MAX_COUNT,
            count_start_seconds=CROSSING_COUNT_START_SECONDS,
            cutoff_seconds=cutoff_seconds,
            min_entry_price=CROSSING_MIN_ENTRY_PRICE,
            max_entry_price=CROSSING_MAX_ENTRY_PRICE,
        )

    candle_key = f"{coin}_{timeframe}_{candle_start.isoformat()}"
    trades = []

    # Binance 가격 시계열 (시간순)
    if not binance_prices:
        return {"trades": []}

    binance_times = sorted([p["time"] for p in binance_prices])
    binance_by_time = {p["time"]: p["price"] for p in binance_prices}

    # Candle open price (첫 번째 가격)
    candle_open = binance_by_time[binance_times[0]]

    # Orderbook snapshots 시간순 정렬
    up_snaps_sorted = sorted(up_snaps, key=lambda x: x["time"])
    down_snaps_sorted = sorted(down_snaps, key=lambda x: x["time"])

    def get_orderbook_at(snaps_sorted: List[Dict], t: datetime) -> Optional[Dict]:
        """t 시점 또는 직전의 오더북 조회"""
        result = None
        for s in snaps_sorted:
            if s["time"] <= t:
                result = s
            else:
                break
        return result

    prev_price = candle_open

    # Binance 가격 중심으로 루프
    for t in binance_times:
        curr_price = binance_by_time[t]
        elapsed_sec = int((t - candle_start).total_seconds())

        # Crossing 감지
        crossing_direction = None
        if prev_price <= candle_open < curr_price:
            crossing_direction = "up"
        elif prev_price >= candle_open > curr_price:
            crossing_direction = "down"

        prev_price = curr_price

        if not crossing_direction:
            continue

        # 해당 시점의 오더북 조회
        last_up = get_orderbook_at(up_snaps_sorted, t)
        last_down = get_orderbook_at(down_snaps_sorted, t)

        if not last_up or not last_down:
            continue

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

        signal = strategy.should_enter_on_crossing(
            coin=coin,
            timeframe=timeframe,
            elapsed_seconds=elapsed_sec,
            crossing_direction=crossing_direction,
            up_orderbook=up_ob,
            down_orderbook=down_ob,
            candle_key=candle_key,
        )

        if signal:
            trades.append({
                "time": t.isoformat() if isinstance(t, datetime) else str(t),
                "elapsed_sec": elapsed_sec,
                "side": signal.side.upper(),
                "entry_price": signal.price,
                "contracts": signal.contracts,
                "cost": signal.contracts * signal.price,
                "reason": signal.reason,
                "crossing_direction": crossing_direction,
                "candle_open": candle_open,
                "outcome": None,
                "pnl": None,
            })

    # Apply market result to trades
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

    return {"trades": trades}


# =========================================================================
# DB Queries
# =========================================================================

async def load_binance_prices(
    conn: asyncpg.Connection,
    coin: str,
    candle_start: datetime,
    candle_end: datetime,
) -> List[Dict]:
    """
    DB에서 Binance 가격 시계열 로드.

    Returns:
        [{"time": datetime, "price": float}, ...]
    """
    rows = await conn.fetch("""
        SELECT time, binance_price as price
        FROM coin_prices
        WHERE coin = $1 AND time >= $2 AND time < $3
          AND binance_price IS NOT NULL
        ORDER BY time ASC
    """, coin, candle_start, candle_end)

    return [{"time": r["time"], "price": float(r["price"])} for r in rows]


async def load_orderbook_data(
    conn: asyncpg.Connection,
    market_slug: str,
    candle_start: datetime,
    candle_end: datetime,
) -> Tuple[List[Dict], List[Dict], Dict[str, str]]:
    """
    DB에서 orderbook_books + orderbook_changes 로드.

    Returns:
        (books, changes, token_to_side)
    """
    books = await conn.fetch("""
        SELECT time, side, bids, asks, token_id
        FROM orderbook_books
        WHERE market_slug = $1
        ORDER BY time ASC
    """, market_slug)

    if not books:
        return [], [], {}

    token_to_side = {}
    token_ids = []
    for row in books:
        token_to_side[row["token_id"]] = row["side"]
        if row["token_id"] not in token_ids:
            token_ids.append(row["token_id"])

    changes = await conn.fetch("""
        SELECT time, token_id, price::float, size::int, book_side
        FROM orderbook_changes
        WHERE token_id = ANY($1) AND time >= $2 AND time < $3
        ORDER BY time ASC
    """, token_ids, candle_start, candle_end)

    # asyncpg returns jsonb as str, need to parse
    parsed_books = []
    for b in books:
        d = dict(b)
        if isinstance(d.get("bids"), str):
            d["bids"] = json.loads(d["bids"])
        if isinstance(d.get("asks"), str):
            d["asks"] = json.loads(d["asks"])
        parsed_books.append(d)

    return parsed_books, [dict(c) for c in changes], token_to_side


async def get_market_result(
    conn: asyncpg.Connection,
    coin: str,
    timeframe: str,
    candle_start: datetime,
    candle_end: datetime,
) -> Optional[str]:
    """
    가격 비교로 UP/DOWN/FLAT 판정.
    15m → chainlink_price, 1h → binance_price
    """
    ALLOWED_COLUMNS = {"binance_price", "chainlink_price"}
    price_column = "binance_price" if timeframe == "1h" else "chainlink_price"
    if price_column not in ALLOWED_COLUMNS:
        return None

    start_row = await conn.fetchrow(f"""
        SELECT {price_column} as price FROM coin_prices
        WHERE coin = $1 AND time >= $2 AND {price_column} IS NOT NULL
        ORDER BY time ASC LIMIT 1
    """, coin, candle_start)

    end_row = await conn.fetchrow(f"""
        SELECT {price_column} as price FROM coin_prices
        WHERE coin = $1 AND time >= $2 AND {price_column} IS NOT NULL
        ORDER BY time ASC LIMIT 1
    """, coin, candle_end)

    if not start_row or not end_row:
        return None

    start_price = float(start_row["price"])
    end_price = float(end_row["price"])

    if end_price > start_price:
        return "UP"
    elif end_price < start_price:
        return "DOWN"
    else:
        return "FLAT"


async def get_paper_trades(
    conn: asyncpg.Connection,
    coin: str,
    market_slug: str,
    strategy_name: str = STRATEGY_NAME,
) -> List[Dict]:
    """DB에서 paper trade 기록 조회"""
    rows = await conn.fetch("""
        SELECT
            time::text as time,
            side,
            order_price::float as entry_price,
            contracts::int,
            cost::float,
            outcome,
            COALESCE(pnl, 0)::float as pnl
        FROM test_paper_trades
        WHERE coin = $1 AND strategy_name = $2
          AND market_slug = $3 AND status = 'CLOSED'
        ORDER BY time DESC
    """, coin, strategy_name, market_slug)

    return [dict(r) for r in rows]


async def get_candle_list(
    conn: asyncpg.Connection,
    coin: str,
    limit: int = 20,
    strategy_name: str = STRATEGY_NAME,
) -> List[Dict]:
    """paper trades에서 유니크 캔들 목록"""
    rows = await conn.fetch("""
        SELECT market_slug,
               MIN(candle_start_time) as candle_start,
               MIN(candle_end_time) as candle_end
        FROM test_paper_trades
        WHERE coin = $1 AND strategy_name = $2
          AND market_slug IS NOT NULL
        GROUP BY market_slug
        ORDER BY market_slug DESC
        LIMIT $3
    """, coin, strategy_name, limit)

    return [{
        "market_slug": r["market_slug"],
        "candle_start": r["candle_start"].isoformat(),
        "candle_end": r["candle_end"].isoformat(),
    } for r in rows]
