#!/usr/bin/env python3
"""
Binance tick 데이터를 15분봉 캔들 단위로 정리

구조: binance-tick-data/{coin}/{YYYY-MM-DD_HH-MM}.csv
- 15분봉 시작 시간 기준 파일명 (예: 2026-02-25_09-45.csv)
- 시작가가 없는 캔들은 저장하지 않음
"""

import os
import csv
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path


def get_candle_start(dt: datetime) -> datetime:
    """15분봉 시작 시간 계산"""
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def format_candle_filename(dt: datetime) -> str:
    """캔들 시작 시간을 파일명 형식으로"""
    return dt.strftime("%Y-%m-%d_%H-%M.csv")


def main():
    base_dir = Path(__file__).parent.parent / "binance-tick-data"
    raw_csv = base_dir / "binance_ticks_raw.csv"

    if not raw_csv.exists():
        print(f"Error: {raw_csv} not found")
        return

    # {coin: {candle_start: [(time, price), ...]}}
    data: dict[str, dict[datetime, list]] = defaultdict(lambda: defaultdict(list))

    print("Reading raw CSV...")
    with open(raw_csv, "r") as f:
        reader = csv.DictReader(f)
        row_count = 0
        for row in reader:
            # time format: 2026-02-25 09:46:14+00
            time_str = row["time"]
            coin = row["coin"]
            price = float(row["price"])

            # Parse timestamp
            dt = datetime.fromisoformat(time_str.replace("+00", "+00:00"))
            candle_start = get_candle_start(dt)

            data[coin][candle_start].append((dt, price))
            row_count += 1

            if row_count % 500000 == 0:
                print(f"  Processed {row_count:,} rows...")

    print(f"Total rows: {row_count:,}")

    # 코인별, 캔들별 파일 생성
    total_files = 0
    skipped_candles = 0

    for coin in sorted(data.keys()):
        coin_dir = base_dir / coin
        coin_dir.mkdir(parents=True, exist_ok=True)

        candles = data[coin]
        print(f"\n{coin.upper()}: {len(candles)} candles")

        for candle_start in sorted(candles.keys()):
            ticks = candles[candle_start]

            # 시작가 확인: 캔들 시작 시간에 가까운 tick이 있어야 함
            # 캔들 시작 후 1분 이내에 tick이 없으면 시작가 없음으로 간주
            first_tick_time = min(t[0] for t in ticks)
            seconds_from_start = (first_tick_time - candle_start).total_seconds()

            if seconds_from_start > 60:
                skipped_candles += 1
                continue

            # 파일 저장
            filename = format_candle_filename(candle_start)
            filepath = coin_dir / filename

            with open(filepath, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["time", "price"])
                for dt, price in sorted(ticks):
                    # ISO format으로 저장
                    writer.writerow([dt.isoformat(), round(price, 2)])

            total_files += 1

    print(f"\n=== Summary ===")
    print(f"Total files created: {total_files}")
    print(f"Skipped candles (no open price): {skipped_candles}")

    # 디렉토리 구조 출력
    print(f"\nOutput structure:")
    for coin in sorted(data.keys()):
        coin_dir = base_dir / coin
        files = sorted(coin_dir.glob("*.csv"))
        if files:
            print(f"  {coin}/: {len(files)} files")
            print(f"    First: {files[0].name}")
            print(f"    Last:  {files[-1].name}")


if __name__ == "__main__":
    main()
