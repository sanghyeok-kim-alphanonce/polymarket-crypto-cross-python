"""
Binance aggTrade 스트림 테스트
- 데이터 구조 확인
- 수신 빈도 측정
"""
import asyncio
import json
import time
from datetime import datetime, timezone

import aiohttp

SYMBOLS = ["btcusdt", "ethusdt"]
WS_URL = "wss://stream.binance.com:9443/stream?streams=" + "/".join(
    [f"{s}@aggTrade" for s in SYMBOLS]
)

async def main():
    print(f"Connecting to: {WS_URL}\n")

    stats = {s: {"count": 0, "first_time": None, "last_time": None} for s in SYMBOLS}
    start_time = time.time()
    max_messages = 50  # 50개만 받고 종료
    total_count = 0

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL, heartbeat=30) as ws:
            print("Connected! Receiving aggTrade data...\n")
            print("=" * 80)

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    stream = data.get("stream", "")
                    payload = data.get("data", {})

                    symbol = payload.get("s", "").lower()
                    price = payload.get("p")
                    qty = payload.get("q")
                    event_time_ms = payload.get("E")
                    trade_time_ms = payload.get("T")
                    is_buyer_maker = payload.get("m")

                    # 통계 업데이트
                    if symbol in stats:
                        stats[symbol]["count"] += 1
                        if stats[symbol]["first_time"] is None:
                            stats[symbol]["first_time"] = event_time_ms
                        stats[symbol]["last_time"] = event_time_ms

                    total_count += 1

                    # 처음 10개는 상세 출력
                    if total_count <= 10:
                        event_dt = datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc)
                        trade_dt = datetime.fromtimestamp(trade_time_ms / 1000, tz=timezone.utc)
                        side = "SELL" if is_buyer_maker else "BUY"

                        print(f"[{total_count:2d}] {symbol.upper()}")
                        print(f"    Price: {price} | Qty: {qty} | Side: {side}")
                        print(f"    Event Time: {event_dt.strftime('%H:%M:%S.%f')[:-3]}")
                        print(f"    Trade Time: {trade_dt.strftime('%H:%M:%S.%f')[:-3]}")
                        print(f"    Raw: {json.dumps(payload)}")
                        print()

                    if total_count >= max_messages:
                        break

            elapsed = time.time() - start_time

            print("=" * 80)
            print(f"\n📊 STATS (received {total_count} messages in {elapsed:.2f}s)")
            print(f"   Overall rate: {total_count / elapsed:.1f} msgs/sec\n")

            for symbol, s in stats.items():
                if s["count"] > 0:
                    duration_ms = s["last_time"] - s["first_time"] if s["first_time"] and s["last_time"] else 0
                    rate = s["count"] / (duration_ms / 1000) if duration_ms > 0 else 0
                    avg_interval = duration_ms / s["count"] if s["count"] > 0 else 0
                    print(f"   {symbol.upper()}: {s['count']} trades")
                    print(f"      Rate: {rate:.1f} trades/sec")
                    print(f"      Avg interval: {avg_interval:.1f}ms")
                    print()


if __name__ == "__main__":
    asyncio.run(main())
