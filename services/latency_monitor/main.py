"""
Latency Monitor Service

두 가지 경로 비교:
1) 직접 Binance WS 연결 → 수신 시간
2) Redis pub/sub (binance_ws 경유) → 수신 시간

같은 tick에 대해 두 경로의 지연 차이를 측정
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Optional, Dict
from collections import defaultdict

import aiohttp
import redis.asyncio as redis

from config import (
    REDIS_HOST, REDIS_PORT, COINS, TIMEFRAMES,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID,
    BINANCE_WS_URL, BINANCE_SYMBOLS,
)


def ts_to_str(ts_ms: int) -> str:
    """밀리초 타임스탬프를 HH:MM:SS.mmm 형식으로 변환"""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime('%H:%M:%S.%f')[:-3]


def now_ms() -> int:
    return int(time.time() * 1000)


async def send_telegram(msg: str):
    """텔레그램 메시지 전송"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
            }
            if TELEGRAM_THREAD_ID:
                payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)
            await session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5))
    except Exception as e:
        print(f"[TELEGRAM] Error: {e}")


class LatencyMonitor:
    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None

        # 직접 WS에서 받은 최신 tick: {coin: (price, binance_event_ms, local_recv_ms)}
        self.direct_ws_ticks: Dict[str, tuple] = {}

        # Redis에서 받은 최신 tick: {coin: (price, binance_event_ms, local_recv_ms)}
        self.redis_ticks: Dict[str, tuple] = {}

        # 통계
        self.stats = {
            "direct_ws_count": 0,
            "redis_count": 0,
            "comparisons": 0,
            # 직접 WS 지연 (Binance → 우리 서버)
            "direct_latency_sum": 0,
            "direct_latency_max": 0,
            "direct_latency_min": float('inf'),
            # Redis 경유 지연 (Binance → binance_ws → Redis → 우리 서버)
            "redis_latency_sum": 0,
            "redis_latency_max": 0,
            "redis_latency_min": float('inf'),
            # 차이 (Redis - Direct)
            "diff_sum": 0,
            "diff_max": 0,
            "diff_min": float('inf'),
        }
        self.start_time = time.time()

        # 최근 비교 결과 (텔레그램 전송용)
        self.recent_comparisons = []

    async def connect_redis(self):
        """Redis 연결"""
        try:
            self.redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                decode_responses=True
            )
            await self.redis_client.ping()
            print(f"[REDIS] Connected to {REDIS_HOST}:{REDIS_PORT}")
            return True
        except Exception as e:
            print(f"[REDIS] Connection failed: {e}")
            return False

    async def start_direct_binance_ws(self):
        """직접 Binance WS 연결 (aggTrade)"""
        streams = [f"{s.lower()}@aggTrade" for s in BINANCE_SYMBOLS.values()]
        ws_url = BINANCE_WS_URL + "/".join(streams)
        symbol_to_coin = {v.lower(): k for k, v in BINANCE_SYMBOLS.items()}

        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, heartbeat=30) as ws:
                        print("[DIRECT-WS] Binance aggTrade connected")

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                local_recv = now_ms()
                                data = json.loads(msg.data)
                                payload = data.get("data", {})

                                symbol = payload.get("s", "").lower()
                                coin = symbol_to_coin.get(symbol)
                                if coin:
                                    price = float(payload.get("p", 0))
                                    binance_event_ms = payload.get("E", local_recv)

                                    self.stats["direct_ws_count"] += 1
                                    await self.handle_direct_tick(coin, price, binance_event_ms, local_recv)

                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                break

            except Exception as e:
                print(f"[DIRECT-WS] Error: {e}")

            print("[DIRECT-WS] Reconnecting in 5s...")
            await asyncio.sleep(5)

    async def subscribe_redis(self):
        """Redis pub/sub 구독 (ch:candle:*)"""
        pubsub = self.redis_client.pubsub()
        await pubsub.psubscribe("ch:candle:*")
        print("[REDIS-SUB] Listening to ch:candle:*")

        async for message in pubsub.listen():
            if message["type"] == "pmessage":
                local_recv = now_ms()
                try:
                    data = json.loads(message["data"])
                    coin = data.get("coin")
                    if not coin:
                        continue

                    close_price = data.get("close", 0)

                    # binance_ws가 추가한 Binance 원본 event time
                    binance_event_ms = data.get("binance_event_ms")
                    server_recv_ms = data.get("server_recv_ms")

                    if binance_event_ms and server_recv_ms:
                        # 3구간 latency 계산
                        # 1) Binance → binance_ws 서버
                        binance_to_server = server_recv_ms - binance_event_ms
                        # 2) binance_ws 서버 → Redis → 여기
                        server_to_here = local_recv - server_recv_ms
                        # 3) 총 latency
                        total = local_recv - binance_event_ms

                        self.redis_ticks[coin] = {
                            "price": close_price,
                            "binance_event_ms": binance_event_ms,
                            "server_recv_ms": server_recv_ms,
                            "local_recv_ms": local_recv,
                            "binance_to_server": binance_to_server,
                            "server_to_here": server_to_here,
                            "total": total,
                        }
                        self.stats["redis_count"] += 1

                        # 통계 업데이트
                        self.stats["binance_to_server_sum"] = self.stats.get("binance_to_server_sum", 0) + binance_to_server
                        self.stats["binance_to_server_max"] = max(self.stats.get("binance_to_server_max", 0), binance_to_server)
                        self.stats["binance_to_server_min"] = min(self.stats.get("binance_to_server_min", float('inf')), binance_to_server)

                        self.stats["server_to_here_sum"] = self.stats.get("server_to_here_sum", 0) + server_to_here
                        self.stats["server_to_here_max"] = max(self.stats.get("server_to_here_max", 0), server_to_here)
                        self.stats["server_to_here_min"] = min(self.stats.get("server_to_here_min", float('inf')), server_to_here)

                        self.stats["total_sum"] = self.stats.get("total_sum", 0) + total
                        self.stats["total_max"] = max(self.stats.get("total_max", 0), total)
                        self.stats["total_min"] = min(self.stats.get("total_min", float('inf')), total)

                        # 100회마다 출력
                        if self.stats["redis_count"] % 100 == 0:
                            print(f"\n[REDIS] {coin.upper()} #{self.stats['redis_count']}")
                            print(f"  Binance Event: {ts_to_str(binance_event_ms)}")
                            print(f"  Server Recv:   {ts_to_str(server_recv_ms)} (+{binance_to_server}ms)")
                            print(f"  Local Recv:    {ts_to_str(local_recv)} (+{server_to_here}ms)")
                            print(f"  TOTAL: {total}ms")

                except Exception as e:
                    print(f"[REDIS-SUB] Error: {e}")

    async def handle_direct_tick(self, coin: str, price: float, binance_event_ms: int, local_recv_ms: int):
        """Direct WS tick 처리"""
        direct_latency = local_recv_ms - binance_event_ms

        # 통계 업데이트
        self.stats["direct_latency_sum"] = self.stats.get("direct_latency_sum", 0) + direct_latency
        self.stats["direct_latency_max"] = max(self.stats.get("direct_latency_max", 0), direct_latency)
        self.stats["direct_latency_min"] = min(self.stats.get("direct_latency_min", float('inf')), direct_latency)

        # 500회마다 출력
        if self.stats["direct_ws_count"] % 500 == 0:
            print(f"\n[DIRECT] {coin.upper()} #{self.stats['direct_ws_count']} latency={direct_latency}ms")

    async def stats_loop(self):
        """통계 출력 및 텔레그램 전송"""
        await asyncio.sleep(30)  # 초기 30초 대기

        while True:
            await asyncio.sleep(60)
            uptime = int(time.time() - self.start_time)
            redis_count = self.stats["redis_count"]

            print(f"\n{'#'*80}")
            print(f"[STATS] uptime={uptime}s | redis_ticks={redis_count}")

            direct_count = self.stats["direct_ws_count"]

            if redis_count > 0:
                avg_b2s = self.stats.get("binance_to_server_sum", 0) / redis_count
                avg_s2h = self.stats.get("server_to_here_sum", 0) / redis_count
                avg_total = self.stats.get("total_sum", 0) / redis_count

                min_b2s = int(self.stats.get("binance_to_server_min", 0))
                max_b2s = int(self.stats.get("binance_to_server_max", 0))
                min_s2h = int(self.stats.get("server_to_here_min", 0))
                max_s2h = int(self.stats.get("server_to_here_max", 0))
                min_total = int(self.stats.get("total_min", 0))
                max_total = int(self.stats.get("total_max", 0))

                # Direct WS 통계
                avg_direct = self.stats.get("direct_latency_sum", 0) / direct_count if direct_count > 0 else 0
                min_direct = int(self.stats.get("direct_latency_min", 0)) if self.stats.get("direct_latency_min", float('inf')) != float('inf') else 0
                max_direct = int(self.stats.get("direct_latency_max", 0))

                print(f"")
                print(f"  ┌─────────────────────────────────────────────────────────────────────┐")
                print(f"  │ LATENCY BREAKDOWN (Redis 경로)                                      │")
                print(f"  ├─────────────────────────────────────────────────────────────────────┤")
                print(f"  │ 1) Binance → Server : avg={avg_b2s:6.1f}ms  min={min_b2s:4}ms  max={max_b2s:4}ms │")
                print(f"  │ 2) Server → Here    : avg={avg_s2h:6.1f}ms  min={min_s2h:4}ms  max={max_s2h:4}ms │")
                print(f"  ├─────────────────────────────────────────────────────────────────────┤")
                print(f"  │ TOTAL (Redis)       : avg={avg_total:6.1f}ms  min={min_total:4}ms  max={max_total:4}ms │")
                print(f"  ├─────────────────────────────────────────────────────────────────────┤")
                print(f"  │ Direct WS (참고)    : avg={avg_direct:6.1f}ms  min={min_direct:4}ms  max={max_direct:4}ms │")
                print(f"  └─────────────────────────────────────────────────────────────────────┘")

                # 텔레그램은 60초마다 보내지 않음 (콘솔 출력만)

            print(f"{'#'*80}\n")

    async def run(self):
        print("=" * 80)
        print("Latency Monitor - Direct WS vs Redis Comparison")
        print("=" * 80)
        print(f"Redis: {REDIS_HOST}:{REDIS_PORT}")
        print(f"Coins: {list(BINANCE_SYMBOLS.keys())}")
        print("")
        print("Comparing two paths:")
        print("  1) Direct Binance WS → This service")
        print("  2) Binance WS → binance_ws → Redis → This service")
        print("=" * 80)

        if not await self.connect_redis():
            print("Failed to connect to Redis")
            return

        # 서비스 시작 알림
        start_msg = (
            f"<b>🚀 Latency Monitor Started</b>\n"
            f"\n"
            f"Coins: {', '.join(BINANCE_SYMBOLS.keys())}\n"
            f"Redis: {REDIS_HOST}:{REDIS_PORT}\n"
            f"\n"
            f"Comparing:\n"
            f"1️⃣ Direct Binance WS\n"
            f"2️⃣ binance_ws → Redis → here"
        )
        await send_telegram(start_msg)

        tasks = [
            self.start_direct_binance_ws(),
            self.subscribe_redis(),
            self.stats_loop(),
        ]

        await asyncio.gather(*tasks)


def main():
    monitor = LatencyMonitor()
    asyncio.run(monitor.run())


if __name__ == "__main__":
    main()
