#!/usr/bin/env python3
"""
각 캔들별 crossing 발생 시점(초) 추출

출력: binance-tick-data/{coin}/crossings.json
{
  "2026-02-25_10-00": {
    "open": 65558.71,
    "crossings": [
      {"sec": 5, "dir": "down"},
      {"sec": 12, "dir": "up"},
      ...
    ]
  },
  ...
}
"""

import csv
import json
from datetime import datetime
from pathlib import Path


def extract_crossings_from_candle(csv_path: Path) -> dict:
    """캔들 CSV에서 crossing 추출"""
    ticks = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dt = datetime.fromisoformat(row["time"])
            price = float(row["price"])
            ticks.append((dt, price))

    if not ticks:
        return {"open": 0, "crossings": []}

    # 첫 tick = open
    candle_start = ticks[0][0]
    open_price = ticks[0][1]

    crossings = []
    zone = None  # "above" | "below"

    for dt, price in ticks:
        elapsed_sec = int((dt - candle_start).total_seconds())

        # 현재 영역 판단
        if price > open_price:
            curr_zone = "above"
        elif price < open_price:
            curr_zone = "below"
        else:
            curr_zone = "at"  # open과 같으면 이전 상태 유지

        # crossing 감지
        if zone == "below" and curr_zone == "above":
            crossings.append({"sec": elapsed_sec, "dir": "up"})
        elif zone == "above" and curr_zone == "below":
            crossings.append({"sec": elapsed_sec, "dir": "down"})

        # 영역 업데이트 (at일 때는 유지)
        if curr_zone != "at":
            zone = curr_zone

    return {
        "open": round(open_price, 2),
        "crossings": crossings,
    }


def main():
    base_dir = Path(__file__).parent.parent / "binance-tick-data"

    for coin_dir in sorted(base_dir.iterdir()):
        if not coin_dir.is_dir():
            continue

        coin = coin_dir.name
        print(f"Processing {coin}...")

        result = {}
        csv_files = sorted(coin_dir.glob("*.csv"))

        for csv_path in csv_files:
            candle_name = csv_path.stem  # e.g., "2026-02-25_10-00"
            data = extract_crossings_from_candle(csv_path)
            result[candle_name] = data

        # 저장
        output_path = coin_dir / "crossings.json"
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)

        # 통계
        total_candles = len(result)
        candles_with_crossings = sum(1 for v in result.values() if v["crossings"])
        total_crossings = sum(len(v["crossings"]) for v in result.values())
        avg_crossings = total_crossings / total_candles if total_candles else 0

        print(f"  {coin}: {total_candles} candles, {candles_with_crossings} with crossings")
        print(f"  Total crossings: {total_crossings}, avg: {avg_crossings:.1f}/candle")
        print(f"  Saved: {output_path}")


if __name__ == "__main__":
    main()
