#!/usr/bin/env python3
"""
Backtest용 캔들 데이터 Export 스크립트

코인/타임프레임/캔들 단위로 DB에서 데이터를 추출해서 JSON 파일로 저장합니다.

Usage:
    # 특정 캔들 export
    python export_candle_data.py --coin btc --slug btc-updown-15m-1740000000

    # 최근 N개 캔들 export
    python export_candle_data.py --coin btc --recent 10

    # 날짜 범위로 export
    python export_candle_data.py --coin btc --start 2026-02-25 --end 2026-02-26

Output:
    ./backtest_data/{coin}/{market_slug}.json
"""
import argparse
import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import asyncpg

# DB 설정
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5435))
DB_NAME = os.getenv("DB_NAME", "paper_trade")
DB_USER = os.getenv("DB_USER", "paper")
DB_PASSWORD = os.getenv("DB_PASSWORD", "papertrade")

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./backtest_data")


async def get_candles_by_slug(conn: asyncpg.Connection, market_slug: str) -> Dict:
    """market_slug로 캔들 정보 조회"""
    # slug 파싱: btc-updown-15m-1740000000
    parts = market_slug.split("-")
    if len(parts) < 4:
        return None

    coin = parts[0]
    timeframe = parts[2]
    timestamp = int(parts[3])

    candle_start = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    minutes = 15 if timeframe == "15m" else 60
    candle_end = candle_start + timedelta(minutes=minutes)

    return {
        "coin": coin,
        "timeframe": timeframe,
        "market_slug": market_slug,
        "candle_start": candle_start,
        "candle_end": candle_end,
    }


async def get_recent_candles(conn: asyncpg.Connection, coin: str, limit: int = 10) -> List[Dict]:
    """최근 캔들 목록 조회 (orderbook_books 기준)"""
    rows = await conn.fetch("""
        SELECT DISTINCT market_slug
        FROM orderbook_books
        WHERE market_slug LIKE $1
        ORDER BY market_slug DESC
        LIMIT $2
    """, f"{coin}-updown-15m-%", limit)

    candles = []
    for row in rows:
        candle = await get_candles_by_slug(conn, row["market_slug"])
        if candle:
            candles.append(candle)
    return candles


async def get_candles_by_date_range(
    conn: asyncpg.Connection,
    coin: str,
    start_date: datetime,
    end_date: datetime
) -> List[Dict]:
    """날짜 범위로 캔들 목록 조회"""
    rows = await conn.fetch("""
        SELECT DISTINCT market_slug
        FROM orderbook_books
        WHERE market_slug LIKE $1
          AND time >= $2 AND time < $3
        ORDER BY market_slug ASC
    """, f"{coin}-updown-15m-%", start_date, end_date)

    candles = []
    for row in rows:
        candle = await get_candles_by_slug(conn, row["market_slug"])
        if candle:
            candles.append(candle)
    return candles


async def export_candle_data(conn: asyncpg.Connection, candle: Dict) -> Dict:
    """캔들 데이터 export"""
    coin = candle["coin"]
    market_slug = candle["market_slug"]
    candle_start = candle["candle_start"]
    candle_end = candle["candle_end"]

    print(f"  Exporting {market_slug}...")

    # 1. orderbook_books
    books = await conn.fetch("""
        SELECT time, side, bids, asks, token_id
        FROM orderbook_books
        WHERE market_slug = $1
        ORDER BY time ASC
    """, market_slug)

    books_data = []
    token_to_side = {}
    for b in books:
        bids = b["bids"]
        asks = b["asks"]
        if isinstance(bids, str):
            bids = json.loads(bids)
        if isinstance(asks, str):
            asks = json.loads(asks)
        books_data.append({
            "time": b["time"].isoformat(),
            "side": b["side"],
            "bids": bids,
            "asks": asks,
            "token_id": b["token_id"],
        })
        token_to_side[b["token_id"]] = b["side"]

    # 2. orderbook_changes
    token_ids = list(token_to_side.keys())
    changes = []
    if token_ids:
        changes_rows = await conn.fetch("""
            SELECT time, token_id, price::float, size::int, book_side
            FROM orderbook_changes
            WHERE token_id = ANY($1) AND time >= $2 AND time < $3
            ORDER BY time ASC
        """, token_ids, candle_start, candle_end)

        for c in changes_rows:
            changes.append({
                "time": c["time"].isoformat(),
                "token_id": c["token_id"],
                "price": float(c["price"]),
                "size": int(c["size"]),
                "book_side": c["book_side"],
            })

    # 3. binance_prices (coin_prices 테이블)
    prices_rows = await conn.fetch("""
        SELECT time, binance_price as price
        FROM coin_prices
        WHERE coin = $1 AND time >= $2 AND time < $3
          AND binance_price IS NOT NULL
        ORDER BY time ASC
    """, coin, candle_start, candle_end)

    binance_prices = []
    for p in prices_rows:
        binance_prices.append({
            "time": p["time"].isoformat(),
            "price": float(p["price"]),
        })

    # 4. chainlink_prices (정산용)
    chainlink_end = candle_end + timedelta(seconds=10)
    chainlink_rows = await conn.fetch("""
        SELECT time, chainlink_price as price
        FROM coin_prices
        WHERE coin = $1 AND time >= $2 AND time <= $3
          AND chainlink_price IS NOT NULL
        ORDER BY time ASC
    """, coin, candle_start, chainlink_end)

    chainlink_prices = []
    for p in chainlink_rows:
        chainlink_prices.append({
            "time": p["time"].isoformat(),
            "price": float(p["price"]),
        })

    # 5. market_result 계산
    market_result = None
    if chainlink_prices:
        start_price = chainlink_prices[0]["price"]
        # candle_end 이후 첫 가격 찾기
        end_prices = [p for p in chainlink_prices if p["time"] >= candle_end.isoformat()]
        if end_prices:
            end_price = end_prices[0]["price"]
            if end_price > start_price:
                market_result = "UP"
            elif end_price < start_price:
                market_result = "DOWN"
            else:
                market_result = "FLAT"

    return {
        "meta": {
            "coin": coin,
            "timeframe": candle["timeframe"],
            "market_slug": market_slug,
            "candle_start": candle_start.isoformat(),
            "candle_end": candle_end.isoformat(),
            "market_result": market_result,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        },
        "token_to_side": token_to_side,
        "orderbook_books": books_data,
        "orderbook_changes": changes,
        "binance_prices": binance_prices,
        "chainlink_prices": chainlink_prices,
        "stats": {
            "books_count": len(books_data),
            "changes_count": len(changes),
            "binance_prices_count": len(binance_prices),
            "chainlink_prices_count": len(chainlink_prices),
        }
    }


async def save_candle_data(candle: Dict, data: Dict):
    """캔들 데이터 파일로 저장"""
    coin = candle["coin"]
    market_slug = candle["market_slug"]

    # 디렉토리 생성
    output_dir = os.path.join(OUTPUT_DIR, coin)
    os.makedirs(output_dir, exist_ok=True)

    # JSON 저장
    output_path = os.path.join(output_dir, f"{market_slug}.json")
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"  Saved: {output_path}")
    print(f"    books={data['stats']['books_count']}, "
          f"changes={data['stats']['changes_count']}, "
          f"binance={data['stats']['binance_prices_count']}, "
          f"chainlink={data['stats']['chainlink_prices_count']}, "
          f"result={data['meta']['market_result']}")

    return output_path


async def main():
    global OUTPUT_DIR

    parser = argparse.ArgumentParser(description="Export candle data for backtest")
    parser.add_argument("--coin", required=True, help="Coin (btc, eth, sol, xrp)")
    parser.add_argument("--slug", help="Specific market_slug")
    parser.add_argument("--recent", type=int, help="Export recent N candles")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", default=OUTPUT_DIR, help="Output directory")
    args = parser.parse_args()

    OUTPUT_DIR = args.output

    # DB 연결
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )

    try:
        candles = []

        if args.slug:
            # 특정 slug
            candle = await get_candles_by_slug(conn, args.slug)
            if candle:
                candles = [candle]
            else:
                print(f"Invalid slug: {args.slug}")
                return
        elif args.recent:
            # 최근 N개
            candles = await get_recent_candles(conn, args.coin, args.recent)
        elif args.start and args.end:
            # 날짜 범위
            start_date = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
            end_date = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
            candles = await get_candles_by_date_range(conn, args.coin, start_date, end_date)
        else:
            print("Please specify --slug, --recent, or --start/--end")
            return

        if not candles:
            print("No candles found")
            return

        print(f"Found {len(candles)} candle(s) to export")

        for candle in candles:
            data = await export_candle_data(conn, candle)
            await save_candle_data(candle, data)

        print(f"\nDone! Exported {len(candles)} candle(s) to {OUTPUT_DIR}/")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
