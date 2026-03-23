"""
P2: Binance WebSocket Service

Binance miniTicker + aggTrade + kline_1m 수집 전용 서비스.
- miniTicker → coin_prices (binance_price), binance_ticks, Redis 캔들
- aggTrade → 실시간 체결 데이터 (crossing 감지용)
- kline_1m → binance_ohlcv_1m
- Redis pub/sub: ch:candle:{coin}_{tf}, ch:crossing:{key} publish

CROSSING_SOURCE 설정:
- "mini": miniTicker로만 crossing 감지 (legacy)
- "agg": aggTrade로만 crossing 감지 (빠름)
- "both": 둘 다 발행 (ch:crossing:agg:*, ch:crossing:mini:*)

단일 쓰레드 asyncio 구조: Lock/Thread 없이 모든 I/O를 await로 처리
"""
import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp

import os
from config import (
    COINS, TIMEFRAMES,
    BINANCE_SYMBOLS, BINANCE_WS_URL,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    REDIS_HOST, REDIS_PORT,
    DB_FLUSH_INTERVAL, STATS_INTERVAL,
    CROSSING_SOURCE,
)

# Telegram (디버깅용)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID", "")
from service_common import AsyncServiceBase, setup_logging

logger = setup_logging(__name__)


class BinanceWsService(AsyncServiceBase):
    """P2: Binance WebSocket 데이터 수집 서비스"""

    def __init__(self):
        super().__init__(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, REDIS_HOST, REDIS_PORT)

        # Binance 상태
        self.binance_prices: Dict[str, float] = {}
        self.binance_ohlcv: Dict[str, Dict] = {}

        # 버퍼 (단일 쓰레드이므로 Lock 불필요)
        self.price_buffer: List[Dict] = []
        self.ohlcv_buffer: List[Dict] = []
        self.binance_ticks: List[Dict] = []

        # 대시보드용 실시간 캔들 추적
        self.binance_candles: Dict[str, Dict] = {}

        # 1초 delta 계산용: {coin: (price, timestamp_ms)}
        self.prev_tick: Dict[str, tuple] = {}  # miniTicker용: {coin: (price, time_ms)}
        self.prev_agg_tick: Dict[str, Tuple[float, int]] = {}  # aggTrade용: {coin: (price, time_ms)}

        # Crossing 감지용: open 기준 가격 영역 추적
        # {coin_tf: "above" | "below"} - "at"(터치)일 때는 이전 상태 유지
        # miniTicker용 (legacy)
        self.price_zone_mini: Dict[str, str] = {}
        # aggTrade용
        self.price_zone_agg: Dict[str, str] = {}

        # Crossing 카운트 추적: {coin_tf: {"candle_start": str, "up": int, "down": int}}
        # source별로 분리
        self.crossing_counts_mini: Dict[str, Dict] = {}
        self.crossing_counts_agg: Dict[str, Dict] = {}

        # Crossing 이벤트 DB 버퍼
        self.crossing_buffer: List[Dict] = []

        # Binance kline에서 가져온 정확한 캔들 open 가격
        # {coin_tf: {"open": float, "candle_start": str}}
        self.kline_opens: Dict[str, Dict] = {}

        # 통계
        self.stats = defaultdict(int)
        self.start_time = time.time()

    def is_healthy(self) -> bool:
        return self.db_pool is not None

    async def on_shutdown(self):
        await self.flush_buffers()

    # =========================================================================
    # 대시보드 Redis (캔들/가격)
    # =========================================================================
    def _get_candle_window(self, timeframe: str = '15m'):
        now = datetime.now(timezone.utc)
        if timeframe == '5m':
            minutes = 5
        elif timeframe == '15m':
            minutes = 15
        else:
            minutes = 60
        total_min = now.hour * 60 + now.minute
        start_min = (total_min // minutes) * minutes
        start = now.replace(hour=start_min // 60, minute=start_min % 60, second=0, microsecond=0)
        end = start + timedelta(minutes=minutes)
        return start, end

    def _get_or_create_candle(self, coin: str, tf: str, price: float) -> Dict:
        """캔들 가져오거나 새로 생성 (kline에서 정확한 open 사용)"""
        key = f"{coin}_{tf}"
        candle_start, candle_end = self._get_candle_window(tf)
        candle_start_str = candle_start.isoformat()

        candle = self.binance_candles.get(key)
        if not candle or candle["candle_start"] != candle_start_str:
            # 새 캔들 시작 → price_zone 초기화
            if key in self.price_zone_mini:
                del self.price_zone_mini[key]
            if key in self.price_zone_agg:
                del self.price_zone_agg[key]

            # kline에서 정확한 open 가격 가져오기
            kline_data = self.kline_opens.get(key)
            if kline_data and kline_data.get("candle_start") == candle_start_str:
                open_price = kline_data["open"]
            else:
                # kline 데이터 없으면 첫 tick 가격 사용 (fallback)
                open_price = price
                logger.warning(f"[{coin}/{tf}] No kline open, using tick price {price:.2f}")

            candle = {
                "open": open_price, "high": max(open_price, price),
                "low": min(open_price, price), "close": price,
                "volume": 0, "candle_start": candle_start_str,
                "candle_end": candle_end.isoformat(),
            }
            self.binance_candles[key] = candle
        return candle

    async def _check_crossing(
        self,
        source: str,  # "mini" or "agg"
        coin: str,
        tf: str,
        price: float,
        prev_price: Optional[float],
        candle: Dict,
        event_time_ms: Optional[int] = None,  # current price time (ms)
        prev_time_ms: Optional[int] = None,   # prev price time (ms)
    ):
        """
        Crossing 감지 및 발행 (공통 로직)

        source: "mini" (miniTicker) or "agg" (aggTrade)
        """
        if not self.redis_client:
            return

        # CROSSING_SOURCE 체크
        if CROSSING_SOURCE == "mini" and source != "mini":
            return
        if CROSSING_SOURCE == "agg" and source != "agg":
            return

        key = f"{coin}_{tf}"
        candle_open = candle["open"]

        # source별 상태 선택
        if source == "mini":
            price_zone = self.price_zone_mini
            crossing_counts = self.crossing_counts_mini
        else:
            price_zone = self.price_zone_agg
            crossing_counts = self.crossing_counts_agg

        # 현재 가격의 영역 판단
        if price > candle_open:
            curr_zone = "above"
        elif price < candle_open:
            curr_zone = "below"
        else:
            curr_zone = "at"  # 정확히 open과 같음

        # 이전 영역 가져오기
        prev_zone = price_zone.get(key)

        # Crossing 판단: 영역이 변경될 때만
        crossing_direction: Optional[str] = None
        if prev_zone == "below" and curr_zone == "above":
            crossing_direction = "up"
        elif prev_zone == "above" and curr_zone == "below":
            crossing_direction = "down"

        # 영역 업데이트 (at일 때는 이전 상태 유지)
        if curr_zone != "at":
            price_zone[key] = curr_zone

        if not crossing_direction:
            return

        # Crossing 발생!
        candle_start_str = candle["candle_start"]

        # 카운트 업데이트
        if key not in crossing_counts or crossing_counts[key]["candle_start"] != candle_start_str:
            crossing_counts[key] = {"candle_start": candle_start_str, "up": 0, "down": 0}

        crossing_counts[key][crossing_direction] += 1
        up_count = crossing_counts[key]["up"]
        down_count = crossing_counts[key]["down"]

        # 캔들 시작 후 경과 시간 계산 (ms 단위)
        candle_start_dt = datetime.fromisoformat(candle_start_str.replace('Z', '+00:00'))
        candle_start_ms = int(candle_start_dt.timestamp() * 1000)
        now = datetime.now(timezone.utc)
        elapsed_ms = int((now - candle_start_dt).total_seconds() * 1000)
        elapsed_sec = elapsed_ms // 1000

        # prev_price와 current_price의 캔들 시작 기준 경과 시간
        prev_elapsed_ms = (prev_time_ms - candle_start_ms) if prev_time_ms else None
        curr_elapsed_ms = (event_time_ms - candle_start_ms) if event_time_ms else elapsed_ms

        crossing_data = {
            "coin": coin,
            "timeframe": tf,
            "direction": crossing_direction,
            "source": source,  # "mini" or "agg"
            "prev_price": prev_price,
            "current_price": price,
            "prev_elapsed_ms": prev_elapsed_ms,  # prev_price 시점 (캔들 시작 기준)
            "curr_elapsed_ms": curr_elapsed_ms,  # current_price 시점 (캔들 시작 기준)
            "candle_open": candle_open,
            "candle_start": candle["candle_start"],
            "candle_end": candle["candle_end"],
            "timestamp": now.isoformat(),
            "up_count": up_count,
            "down_count": down_count,
            "elapsed_ms": elapsed_ms,
            # Latency 측정용: Binance 원본 event time
            "binance_event_ms": event_time_ms,
            "server_recv_ms": int(now.timestamp() * 1000),
        }

        # 채널 결정
        if CROSSING_SOURCE == "both":
            # 둘 다 발행 시 source별 채널
            channel = f"ch:crossing:{source}:{key}"
        else:
            # 단일 source일 때 기존 채널
            channel = f"ch:crossing:{key}"

        await self.redis_client.publish(channel, json.dumps(crossing_data))

        # Redis에 crossing 카운트 저장
        count_key = f"crossing_count:{key}" if CROSSING_SOURCE != "both" else f"crossing_count:{source}:{key}"
        count_data = {
            "coin": coin,
            "timeframe": tf,
            "source": source,
            "candle_start": candle_start_str,
            "up": up_count,
            "down": down_count,
            "total": up_count + down_count,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis_client.setex(count_key, 900, json.dumps(count_data))

        # DB 버퍼에 추가
        self.crossing_buffer.append({
            "time": datetime.now(timezone.utc),
            "coin": coin,
            "timeframe": tf,
            "source": source,
            "direction": crossing_direction,
            "candle_start": candle_start_dt,
            "candle_open": candle_open,
            "prev_price": prev_price,
            "current_price": price,
            "elapsed_sec": elapsed_sec,
        })

        self.stats[f"crossings_{source}"] += 1
        logger.info(f"[CROSSING-{source.upper()}] {coin}/{tf} {crossing_direction.upper()} | "
                    f"zone: {prev_zone}→{curr_zone} | "
                    f"prev={(prev_price or 0):.2f} open={candle_open:.2f} curr={price:.2f} | "
                    f"count: UP={up_count} DOWN={down_count} | elapsed={elapsed_sec}s")

    async def _update_binance_candle(self, coin: str, price: float, event_time_ms: int):
        """miniTicker 틱으로 실시간 캔들 업데이트 → Redis + pub/sub + crossing (mini)"""
        if not self.redis_client:
            return

        # 1초 delta 계산
        delta_1s = None
        prev_price = None
        delta_time_ms = None

        prev = self.prev_tick.get(coin)
        if prev:
            prev_price, prev_time_ms = prev
            delta_time_ms = event_time_ms - prev_time_ms
            # 500ms ~ 2000ms 범위면 유효한 1초 delta로 간주
            if 500 <= delta_time_ms <= 2000:
                delta_1s = price - prev_price

        # 현재 가격/시간 저장
        self.prev_tick[coin] = (price, event_time_ms)

        for tf in TIMEFRAMES:
            key = f"{coin}_{tf}"
            candle = self._get_or_create_candle(coin, tf, price)

            # 캔들 업데이트
            candle["high"] = max(candle["high"], price)
            candle["low"] = min(candle["low"], price)
            candle["close"] = price

            direction = "up" if price > candle["open"] else ("down" if price < candle["open"] else "flat")
            pct = ((price - candle["open"]) / candle["open"] * 100) if candle["open"] else 0

            now = datetime.now(timezone.utc)
            redis_data = {
                "coin": coin, "timeframe": tf, "direction": direction,
                "open": candle["open"], "high": candle["high"],
                "low": candle["low"], "close": candle["close"],
                "volume": candle["volume"], "price_change_pct": round(pct, 4),
                "candle_start": candle["candle_start"],
                "candle_end": candle["candle_end"],
                "updated_at": now.isoformat(),
                # V12-5 전략용 1초 delta
                "delta_1s": delta_1s,
                "prev_price": prev_price,
                "delta_time_ms": delta_time_ms,
                # Latency 측정용: Binance 원본 event time
                "binance_event_ms": event_time_ms,
                "server_recv_ms": int(now.timestamp() * 1000),
            }

            try:
                # 대시보드용 Redis key
                await self.redis_client.setex(f"current_candle:{key}", 60, json.dumps(redis_data))
                # P4 paper_trader용 delta 전용 key (rtds와 충돌 방지)
                delta_data = {
                    "delta_1s": delta_1s,
                    "prev_price": prev_price,
                    "delta_time_ms": delta_time_ms,
                    "updated_at": redis_data["updated_at"],
                }
                await self.redis_client.setex(f"binance_delta:{key}", 60, json.dumps(delta_data))
                # P4 paper_trader용 pub/sub
                await self.redis_client.publish(f"ch:candle:{key}", json.dumps(redis_data))

                # miniTicker 기반 crossing 감지
                await self._check_crossing("mini", coin, tf, price, prev_price, candle)

            except Exception as e:
                logger.error(f"[REDIS] binance_candle write error: {e}")

    async def _handle_aggtrade(self, coin: str, price: float, event_time_ms: int):
        """aggTrade 틱으로 crossing 감지 (agg)"""
        if not self.redis_client:
            return

        self.stats["agg_ticks"] += 1

        # aggTrade용 prev_price, prev_time_ms 추적
        prev_data = self.prev_agg_tick.get(coin)
        prev_price = prev_data[0] if prev_data else None
        prev_time_ms = prev_data[1] if prev_data else None
        self.prev_agg_tick[coin] = (price, event_time_ms)

        for tf in TIMEFRAMES:
            # 캔들이 없으면 miniTicker에서 만들어질 때까지 대기
            key = f"{coin}_{tf}"
            candle = self.binance_candles.get(key)
            if not candle:
                # 캔들이 아직 없으면 생성
                candle = self._get_or_create_candle(coin, tf, price)

            # aggTrade 기반 crossing 감지 (event_time_ms, prev_time_ms 포함)
            await self._check_crossing("agg", coin, tf, price, prev_price, candle, event_time_ms, prev_time_ms)

    async def _publish_exchange_price(self, coin: str, price: float):
        if not self.redis_client:
            return
        now = datetime.now(timezone.utc)
        data = {
            "exchange": "binance", "symbol": f"{coin.upper()}USDT",
            "coin": coin, "price": price, "bid": price, "ask": price,
            "timestamp": now.timestamp(), "datetime": now.isoformat(),
        }
        try:
            await self.redis_client.setex(f"exchange_price:binance:{coin}", 30, json.dumps(data))
        except Exception as e:
            logger.error(f"[REDIS] exchange_price write error: {e}")

    # =========================================================================
    # Binance WebSocket Streams
    # =========================================================================
    async def start_binance_stream(self):
        """miniTicker + kline_1m 스트림 (1분봉 closed로 5m/15m open 대체)"""
        streams = []
        for symbol in BINANCE_SYMBOLS.values():
            streams.append(f"{symbol.lower()}@miniTicker")
            streams.append(f"{symbol.lower()}@kline_1m")

        ws_url = BINANCE_WS_URL + "/".join(streams)
        symbol_to_coin = {v.lower(): k for k, v in BINANCE_SYMBOLS.items()}

        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, heartbeat=30) as ws:
                        logger.info("Binance miniTicker+kline WS connected")

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                stream = data.get("stream", "")
                                payload = data.get("data", {})

                                if "@miniTicker" in stream:
                                    symbol = payload.get("s", "").lower()
                                    coin = symbol_to_coin.get(symbol)
                                    if coin:
                                        price = float(payload.get("c", 0))
                                        event_time_ms = payload.get("E", int(time.time() * 1000))
                                        event_time = datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc)
                                        self.binance_prices[coin] = price
                                        self.stats["binance_ticks"] += 1

                                        if self.redis_client:
                                            await self.redis_client.setex(
                                                f"binance_price:{coin}", 30,
                                                json.dumps({"coin": coin, "price": price})
                                            )

                                        await self._publish_exchange_price(coin, price)
                                        await self._update_binance_candle(coin, price, event_time_ms)

                                        self.binance_ticks.append({
                                            "time": event_time,
                                            "coin": coin,
                                            "price": price,
                                        })
                                        self.price_buffer.append({
                                            "time": event_time.replace(microsecond=0),
                                            "coin": coin,
                                            "binance_price": price,
                                        })

                                elif "@kline" in stream:
                                    kline = payload.get("k", {})
                                    symbol = kline.get("s", "").lower()
                                    coin = symbol_to_coin.get(symbol)
                                    interval = kline.get("i", "")  # "1m"

                                    # 1분봉 종료 시 처리
                                    if coin and kline.get("x") and interval == "1m":
                                        kline_close_time_ms = kline.get("T", 0)  # 캔들 종료 시간
                                        close_price = float(kline["c"])
                                        now_ms = int(time.time() * 1000)

                                        # 다음 캔들 시작 시간 계산 (1분봉 종료 + 1ms = 다음 캔들 시작)
                                        next_candle_start_ms = kline_close_time_ms + 1
                                        next_candle_start = datetime.fromtimestamp(next_candle_start_ms / 1000, tz=timezone.utc)
                                        next_minute = next_candle_start.minute

                                        # 5분봉 경계 체크 (00, 05, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)
                                        if next_minute % 5 == 0:
                                            key_5m = f"{coin}_5m"
                                            candle_start_5m = next_candle_start.isoformat()
                                            self.kline_opens[key_5m] = {
                                                "open": close_price,
                                                "candle_start": candle_start_5m,
                                            }
                                            delay_ms = now_ms - kline_close_time_ms
                                            now_str = datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3]
                                            logger.info(f"[KLINE-1M] {coin}/5m OPEN={close_price:.2f} | kline_close={kline_close_time_ms} now={now_ms} delay={delay_ms}ms | {now_str}")

                                            # 기존 캔들 교정
                                            candle = self.binance_candles.get(key_5m)
                                            if candle and candle.get("candle_start") == candle_start_5m:
                                                old_open = candle["open"]
                                                if abs(old_open - close_price) > 0.01:
                                                    candle["open"] = close_price
                                                    logger.info(f"[KLINE-1M] {coin}/5m CORRECTED {old_open:.2f} -> {close_price:.2f}")

                                            # BTC만 텔레그램 전송
                                            if coin == "btc":
                                                asyncio.create_task(self._send_open_telegram(
                                                    coin, "5m", close_price, candle_start_5m, delay_ms, now_str
                                                ))

                                        # 15분봉 경계 체크 (00, 15, 30, 45)
                                        if next_minute % 15 == 0:
                                            key_15m = f"{coin}_15m"
                                            candle_start_15m = next_candle_start.isoformat()
                                            self.kline_opens[key_15m] = {
                                                "open": close_price,
                                                "candle_start": candle_start_15m,
                                            }
                                            delay_ms = now_ms - kline_close_time_ms
                                            now_str = datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3]
                                            logger.info(f"[KLINE-1M] {coin}/15m OPEN={close_price:.2f} | kline_close={kline_close_time_ms} now={now_ms} delay={delay_ms}ms | {now_str}")

                                            # 기존 캔들 교정
                                            candle = self.binance_candles.get(key_15m)
                                            if candle and candle.get("candle_start") == candle_start_15m:
                                                old_open = candle["open"]
                                                if abs(old_open - close_price) > 0.01:
                                                    candle["open"] = close_price
                                                    logger.info(f"[KLINE-1M] {coin}/15m CORRECTED {old_open:.2f} -> {close_price:.2f}")

                                            # BTC만 텔레그램 전송
                                            if coin == "btc":
                                                asyncio.create_task(self._send_open_telegram(
                                                    coin, "15m", close_price, candle_start_15m, delay_ms, now_str
                                                ))

                                        # DB 저장 (기존 로직)
                                        ohlcv = {
                                            "time": datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc),
                                            "coin": coin,
                                            "open": float(kline["o"]),
                                            "high": float(kline["h"]),
                                            "low": float(kline["l"]),
                                            "close": close_price,
                                            "volume": float(kline["v"]),
                                        }
                                        self.binance_ohlcv[coin] = ohlcv
                                        self.stats["binance_ohlcv"] += 1
                                        self.ohlcv_buffer.append(ohlcv)

                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                break

            except Exception as e:
                logger.error(f"Binance miniTicker WS error: {e}")

            logger.info("Binance miniTicker WS reconnecting in 5s...")
            await asyncio.sleep(5)

    async def start_aggtrade_stream(self):
        """aggTrade 스트림 (빠른 crossing 감지용)"""
        # agg 또는 both일 때만 실행
        if CROSSING_SOURCE not in ("agg", "both"):
            logger.info(f"[AGGTRADE] Disabled (CROSSING_SOURCE={CROSSING_SOURCE})")
            return

        streams = [f"{s.lower()}@aggTrade" for s in BINANCE_SYMBOLS.values()]
        ws_url = BINANCE_WS_URL + "/".join(streams)
        symbol_to_coin = {v.lower(): k for k, v in BINANCE_SYMBOLS.items()}

        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, heartbeat=30) as ws:
                        logger.info("Binance aggTrade WS connected")

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                payload = data.get("data", {})

                                symbol = payload.get("s", "").lower()
                                coin = symbol_to_coin.get(symbol)
                                if coin:
                                    price = float(payload.get("p", 0))
                                    event_time_ms = payload.get("E", int(time.time() * 1000))
                                    await self._handle_aggtrade(coin, price, event_time_ms)

                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                break

            except Exception as e:
                logger.error(f"Binance aggTrade WS error: {e}")

            logger.info("Binance aggTrade WS reconnecting in 5s...")
            await asyncio.sleep(5)

    # =========================================================================
    # DB Flush
    # =========================================================================
    async def db_flush_loop(self):
        while True:
            await asyncio.sleep(DB_FLUSH_INTERVAL)
            try:
                await self.flush_buffers()
            except Exception as e:
                logger.error(f"DB flush error: {e}")

    async def _send_open_telegram(self, coin: str, tf: str, open_price: float,
                                    candle_start: str, delay_ms: int, now_str: str):
        """캔들 open 가격 텔레그램 전송 (디버깅용)"""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return
        try:
            msg = (
                f"<b>[{tf.upper()} OPEN]</b> {coin.upper()}\n"
                f"open: <b>{open_price:,.2f}</b>\n"
                f"candle: {candle_start}\n"
                f"delay: {delay_ms}ms | {now_str}"
            )
            async with aiohttp.ClientSession() as session:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": msg,
                    "parse_mode": "HTML"
                }
                if TELEGRAM_THREAD_ID:
                    payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)
                await session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5))
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

    async def flush_buffers(self):
        prices = list(self.price_buffer)
        self.price_buffer.clear()

        ohlcv = list(self.ohlcv_buffer)
        self.ohlcv_buffer.clear()

        ticks = list(self.binance_ticks)
        self.binance_ticks.clear()

        crossings = list(self.crossing_buffer)
        self.crossing_buffer.clear()

        async with self.db_pool.acquire() as conn:
            # --- coin_prices (binance_price only) ---
            price_agg = {}
            for p in prices:
                key = (p["time"], p["coin"])
                if key not in price_agg:
                    price_agg[key] = {"binance_price": None}
                if p.get("binance_price") is not None:
                    price_agg[key]["binance_price"] = p["binance_price"]

            if price_agg:
                price_rows = [
                    (t, coin, vals["binance_price"])
                    for (t, coin), vals in price_agg.items()
                ]
                try:
                    await conn.executemany("""
                        INSERT INTO coin_prices (time, coin, binance_price)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (coin, time) DO UPDATE SET
                            binance_price = COALESCE(EXCLUDED.binance_price, coin_prices.binance_price)
                    """, price_rows)
                except Exception as e:
                    logger.error(f"Flush coin_prices error: {e}")

            # --- binance_ohlcv_1m ---
            if ohlcv:
                ohlcv_rows = [
                    (o["time"], o["coin"], o["open"], o["high"], o["low"], o["close"], o["volume"])
                    for o in ohlcv
                ]
                try:
                    await conn.executemany("""
                        INSERT INTO binance_ohlcv_1m (time, coin, open, high, low, close, volume)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (coin, time) DO UPDATE
                        SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                            close=EXCLUDED.close, volume=EXCLUDED.volume
                    """, ohlcv_rows)
                except Exception as e:
                    logger.error(f"Flush ohlcv error: {e}")

            # --- binance_ticks (dedup) ---
            seen_ticks = set()
            tick_rows = []
            for t in ticks:
                tick_key = (t["coin"], t["time"].replace(microsecond=0))
                if tick_key in seen_ticks:
                    continue
                seen_ticks.add(tick_key)
                tick_rows.append((t["time"].replace(microsecond=0), t["coin"], t["price"]))

            if tick_rows:
                try:
                    await conn.executemany("""
                        INSERT INTO binance_ticks (time, coin, price)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (coin, time) DO UPDATE SET price = EXCLUDED.price
                    """, tick_rows)
                except Exception as e:
                    logger.error(f"Flush ticks error: {e}")

            # --- crossing_events ---
            if crossings:
                crossing_rows = [
                    (c["time"], c["coin"], c["timeframe"], c["direction"],
                     c["candle_start"], c["candle_open"], c["prev_price"],
                     c["current_price"], c["elapsed_sec"], c.get("source", "mini"))
                    for c in crossings
                ]
                try:
                    await conn.executemany("""
                        INSERT INTO crossing_events
                        (time, coin, timeframe, direction, candle_start, candle_open,
                         prev_price, current_price, candle_elapsed_sec, source)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """, crossing_rows)
                except Exception as e:
                    logger.error(f"Flush crossing_events error: {e}")

    # =========================================================================
    # Stats
    # =========================================================================
    async def stats_loop(self):
        while True:
            await asyncio.sleep(STATS_INTERVAL)
            uptime = int(time.time() - self.start_time)
            logger.info(
                f"[STATS] uptime={uptime}s | "
                f"ticks={self.stats.get('binance_ticks', 0)} | "
                f"agg_ticks={self.stats.get('agg_ticks', 0)} | "
                f"ohlcv={self.stats.get('binance_ohlcv', 0)} | "
                f"cross_mini={self.stats.get('crossings_mini', 0)} | "
                f"cross_agg={self.stats.get('crossings_agg', 0)} | "
                f"source={CROSSING_SOURCE}"
            )

    # =========================================================================
    # 메인 실행
    # =========================================================================
    async def run(self):
        logger.info("=" * 60)
        logger.info("P2: Binance WebSocket Service Starting")
        logger.info("=" * 60)
        logger.info(f"Coins: {COINS}")
        logger.info(f"Timeframes: {TIMEFRAMES}")
        logger.info(f"CROSSING_SOURCE: {CROSSING_SOURCE}")
        logger.info("=" * 60)

        if not await self.connect_db():
            logger.error("Failed to connect to DB")
            return

        await self.connect_redis()

        self.setup_signal_handlers()

        tasks = [
            self.start_binance_stream(),
            self.start_aggtrade_stream(),
            self.db_flush_loop(),
            self.stats_loop(),
            self._health_file_loop(),
        ]

        await asyncio.gather(*tasks)


def main():
    service = BinanceWsService()
    asyncio.run(service.run())


if __name__ == "__main__":
    main()
