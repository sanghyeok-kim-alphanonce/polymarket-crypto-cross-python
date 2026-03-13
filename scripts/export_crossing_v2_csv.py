#!/usr/bin/env python3
"""
Crossing V2 Paper Trading 데이터를 CSV로 내보내기
- 코인별, 캔들별로 폴더/파일 구조화
"""

import os
import csv
import psycopg2
from datetime import datetime, timezone
from collections import defaultdict

# DB 설정
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5435")
DB_NAME = os.getenv("DB_NAME", "paper_trade")
DB_USER = os.getenv("DB_USER", "paper")
DB_PASS = os.getenv("DB_PASS", "papertrade")

STRATEGY_NAME = "crossing_v2"
EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "exports", "crossing_v2")


def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )


def fetch_all_trades(conn):
    """전체 crossing_v2 trades 가져오기"""
    query = """
        SELECT
            id, time, coin, side,
            order_price, fill_price, exit_price,
            contracts, cost, pnl,
            outcome, market_result, status,
            candle_start_time, candle_end_time,
            orderbook_snapshot,
            reason
        FROM test_paper_trades
        WHERE strategy_name = %s
        ORDER BY candle_start_time DESC, time ASC
    """
    with conn.cursor() as cur:
        cur.execute(query, (STRATEGY_NAME,))
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
    return columns, rows


def extract_crossing_info(orderbook_snapshot):
    """orderbook_snapshot에서 crossing 정보 추출"""
    if not orderbook_snapshot:
        return {}

    crossing = orderbook_snapshot.get("crossing", {})
    return {
        "candle_open": crossing.get("candle_open"),
        "prev_price": crossing.get("prev_price"),
        "current_price": crossing.get("current_price"),
        "direction": crossing.get("direction"),
        "elapsed_seconds": crossing.get("elapsed_seconds"),
    }


def format_datetime(dt):
    """datetime을 문자열로 변환"""
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def export_to_csv():
    conn = get_db_connection()

    try:
        columns, rows = fetch_all_trades(conn)

        if not rows:
            print("No crossing_v2 trades found.")
            return

        print(f"Found {len(rows)} trades")

        # 코인별, 캔들별로 그룹화
        grouped = defaultdict(lambda: defaultdict(list))

        for row in rows:
            data = dict(zip(columns, row))
            coin = data.get("coin", "unknown")
            candle_start = data.get("candle_start_time")

            if candle_start is None:
                candle_key = "no_candle"
            else:
                # ISO 형식으로 안전한 파일명 생성
                if isinstance(candle_start, datetime):
                    candle_key = candle_start.strftime("%Y%m%d_%H%M")
                else:
                    candle_key = str(candle_start).replace(":", "").replace(" ", "_").replace("-", "")[:13]

            grouped[coin][candle_key].append(data)

        # CSV 필드 정의
        csv_fields = [
            "id", "time", "coin", "side",
            "candle_open", "prev_price", "current_price", "direction", "elapsed_seconds",
            "order_price", "fill_price", "exit_price",
            "contracts", "cost", "pnl",
            "outcome", "market_result", "status",
            "candle_start_time", "reason"
        ]

        # 코인별 폴더 생성 및 CSV 저장
        total_files = 0
        for coin, candles in grouped.items():
            coin_dir = os.path.join(EXPORT_DIR, coin)
            os.makedirs(coin_dir, exist_ok=True)

            for candle_key, trades in candles.items():
                csv_path = os.path.join(coin_dir, f"{candle_key}.csv")

                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=csv_fields)
                    writer.writeheader()

                    for trade in trades:
                        # crossing 정보 추출
                        crossing_info = extract_crossing_info(trade.get("orderbook_snapshot"))

                        row = {
                            "id": trade.get("id"),
                            "time": format_datetime(trade.get("time")),
                            "coin": trade.get("coin"),
                            "side": trade.get("side"),
                            "candle_open": crossing_info.get("candle_open"),
                            "prev_price": crossing_info.get("prev_price"),
                            "current_price": crossing_info.get("current_price"),
                            "direction": crossing_info.get("direction"),
                            "elapsed_seconds": crossing_info.get("elapsed_seconds"),
                            "order_price": trade.get("order_price"),
                            "fill_price": trade.get("fill_price"),
                            "exit_price": trade.get("exit_price"),
                            "contracts": trade.get("contracts"),
                            "cost": trade.get("cost"),
                            "pnl": trade.get("pnl"),
                            "outcome": trade.get("outcome"),
                            "market_result": trade.get("market_result"),
                            "status": trade.get("status"),
                            "candle_start_time": format_datetime(trade.get("candle_start_time")),
                            "reason": trade.get("reason"),
                        }
                        writer.writerow(row)

                total_files += 1
                print(f"  {coin}/{candle_key}.csv ({len(trades)} trades)")

        # 전체 요약 CSV도 생성
        summary_path = os.path.join(EXPORT_DIR, "summary.csv")
        summary_fields = ["coin", "candle_start_time", "up_count", "down_count", "total_cost", "total_pnl"]

        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=summary_fields)
            writer.writeheader()

            for coin in sorted(grouped.keys()):
                for candle_key in sorted(grouped[coin].keys(), reverse=True):
                    trades = grouped[coin][candle_key]
                    up_count = sum(1 for t in trades if t.get("side") == "UP")
                    down_count = sum(1 for t in trades if t.get("side") == "DOWN")
                    total_cost = sum(float(t.get("cost") or 0) for t in trades)
                    total_pnl = sum(float(t.get("pnl") or 0) for t in trades)

                    # candle_start_time 복원
                    candle_start = trades[0].get("candle_start_time") if trades else None

                    writer.writerow({
                        "coin": coin,
                        "candle_start_time": format_datetime(candle_start),
                        "up_count": up_count,
                        "down_count": down_count,
                        "total_cost": round(total_cost, 2),
                        "total_pnl": round(total_pnl, 2),
                    })

        print(f"\n✓ Exported {total_files} candle files to {EXPORT_DIR}/")
        print(f"✓ Summary saved to {summary_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    export_to_csv()
