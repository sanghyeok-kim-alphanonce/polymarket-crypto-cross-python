"""
P1: RTDS (Real-Time Data Service)

Polymarket CLOB WebSocket + RTDS WebSocket + Chainlink REST 통합 I/O 전용 서비스.
- Polymarket CLOB WS: 오더북 실시간 수집 + Redis pub/sub publish
- RTDS WS: Binance/Chainlink 실시간 가격 (primary)
- Chainlink REST: 가격 수집 fallback (10초마다)
- Redis pub/sub: ch:orderbook:{coin}_{tf}_{side}, ch:candle_boundary publish

단일 쓰레드 asyncio 구조
"""
import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any

import aiohttp
import websockets

from config import (
    ORDERBOOK_COINS, PRICE_COINS, TIMEFRAMES,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    REDIS_HOST, REDIS_PORT,
    CHAINLINK_FEED_IDS, CHAINLINK_INTERVAL,
    POLYMARKET_WS_URL,
    RTDS_WSS_URL, RTDS_BINANCE_SYMBOLS, RTDS_CHAINLINK_SYMBOLS,
    RTDS_SYMBOL_MAP, RTDS_RECONNECT_DELAY,
    DB_FLUSH_INTERVAL, STATS_INTERVAL,
    GAMMA_API,
)

# Packages
from polymarket_common import ET, KST, generate_slug, get_seconds_to_next_candle, get_next_candle_start
from orderbook_shared import OrderbookSnapshot, OrderbookLevel
from service_common import AsyncServiceBase, setup_logging

logger = setup_logging(__name__)


@dataclass
class OrderbookState:
    """오더북 상태"""
    bids: Dict[float, int] = field(default_factory=dict)
    asks: Dict[float, int] = field(default_factory=dict)
    market_slug: str = ""
    token_id: str = ""
    updated_at: float = 0.0


class RtdsService(AsyncServiceBase):
    """P1: Real-Time Data Service"""

    def __init__(self):
        super().__init__(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, REDIS_HOST, REDIS_PORT)

        # Polymarket WebSocket
        self.orderbooks: Dict[str, OrderbookState] = {}
        self.token_to_market: Dict[str, Dict[str, Any]] = {}
        self.asset_ids: List[str] = []
        self._poly_reconnect = asyncio.Event()
        self._poly_ws = None
        self._next_poly_ws = None

        # Binance prices (from RTDS)
        self.binance_prices: Dict[str, float] = {}

        # Chainlink prices
        self.chainlink_prices: Dict[str, float] = {}

        # 버퍼
        self.price_buffer: List[Dict] = []
        self.ob_book_buffer: List[tuple] = []
        self.ob_change_buffer: List[tuple] = []

        # 대시보드용 캔들 추적
        self.binance_candles: Dict[str, Dict] = {}
        self.chainlink_candles: Dict[str, Dict] = {}

        # RTDS 상태
        self.rtds_connected = False

        # 통계
        self.stats = defaultdict(int)
        self.start_time = time.time()

    def is_healthy(self) -> bool:
        return super().is_healthy() and bool(self.asset_ids)

    async def on_shutdown(self):
        await self.flush_buffers()

    # =========================================================================
    # Polymarket - 마켓 로드
    # =========================================================================
    async def _fetch_markets(self, quiet: bool = False, override_timestamp: int = None) -> Optional[Tuple[Dict, List]]:
        try:
            now_et = datetime.now(ET)
            if not quiet:
                logger.info(f"Fetching markets at {now_et.strftime('%Y-%m-%d %H:%M:%S')} ET")

            token_to_market = {}
            asset_ids = []
            loaded_markets = []

            async with aiohttp.ClientSession() as session:
                for coin in ORDERBOOK_COINS:
                    for timeframe in TIMEFRAMES:
                        # 각 타임프레임별로 올바른 캔들 시작 timestamp 계산
                        if override_timestamp:
                            # override_timestamp는 5분봉 기준이므로, 해당 시점의 각 타임프레임 캔들 시작 계산
                            from polymarket_common import get_candle_start_for_timestamp
                            tf_timestamp = get_candle_start_for_timestamp(timeframe, override_timestamp)
                        else:
                            tf_timestamp = None
                        slug = generate_slug(coin, timeframe, timestamp=tf_timestamp)

                        try:
                            async with session.get(
                                f"{GAMMA_API}/markets/slug/{slug}",
                                timeout=aiohttp.ClientTimeout(total=10)
                            ) as response:
                                if response.status == 200:
                                    market = await response.json()
                                    clob_token_ids = market.get("clobTokenIds", "[]")
                                    if isinstance(clob_token_ids, str):
                                        token_ids = json.loads(clob_token_ids)
                                    else:
                                        token_ids = clob_token_ids

                                    outcomes = market.get("outcomes", "[]")
                                    if isinstance(outcomes, str):
                                        outcomes = json.loads(outcomes)

                                    if token_ids:
                                        asset_ids.extend(token_ids)
                                        for i, token_id in enumerate(token_ids):
                                            side = outcomes[i].lower() if i < len(outcomes) else ("up" if i == 0 else "down")
                                            token_to_market[token_id] = {
                                                "coin": coin,
                                                "timeframe": timeframe,
                                                "side": side,
                                                "slug": slug,
                                                "market_key": f"{coin}_{timeframe}_{side}",
                                            }
                                        loaded_markets.append(f"{coin}_{timeframe}")
                                        if not quiet:
                                            logger.info(f"  {coin.upper()}/{timeframe}: {slug} ({len(token_ids)} tokens)")
                                else:
                                    if not quiet:
                                        logger.warning(f"  {coin.upper()}/{timeframe}: {response.status} - {slug}")
                        except Exception as e:
                            if not quiet:
                                logger.warning(f"  {coin.upper()}/{timeframe}: {e}")

            if not quiet:
                logger.info(f"Fetched {len(loaded_markets)} markets, {len(asset_ids)} tokens")

            if not asset_ids:
                return None
            return token_to_market, asset_ids

        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return None

    def _apply_markets(self, token_to_market: Dict, asset_ids: List):
        self.token_to_market = token_to_market
        self.asset_ids = asset_ids
        logger.info(f"Applied {len(asset_ids)} tokens from {len(set(m['coin'] for m in token_to_market.values()))} coins")

    async def load_active_markets(self, quiet: bool = False) -> bool:
        result = await self._fetch_markets(quiet=quiet)
        if result is None:
            return False
        self._apply_markets(*result)
        return True

    # =========================================================================
    # 대시보드 Redis (캔들/가격)
    # =========================================================================
    def _get_candle_window(self, timeframe: str = '15m'):
        now = datetime.now(timezone.utc)
        minutes = 15 if timeframe == '15m' else 60
        total_min = now.hour * 60 + now.minute
        start_min = (total_min // minutes) * minutes
        start = now.replace(hour=start_min // 60, minute=start_min % 60, second=0, microsecond=0)
        end = start + timedelta(minutes=minutes)
        return start, end

    async def _update_binance_candle(self, coin: str, price: float):
        if not self.redis_client:
            return

        for tf in TIMEFRAMES:
            key = f"{coin}_{tf}"
            candle_start, candle_end = self._get_candle_window(tf)

            candle = self.binance_candles.get(key)
            if not candle or candle["candle_start"] != candle_start.isoformat():
                candle = {
                    "open": price, "high": price, "low": price, "close": price,
                    "volume": 0, "candle_start": candle_start.isoformat(),
                    "candle_end": candle_end.isoformat(),
                }
                self.binance_candles[key] = candle
            else:
                candle["high"] = max(candle["high"], price)
                candle["low"] = min(candle["low"], price)
                candle["close"] = price

            direction = "up" if price > candle["open"] else ("down" if price < candle["open"] else "flat")
            pct = ((price - candle["open"]) / candle["open"] * 100) if candle["open"] else 0

            redis_data = {
                "coin": coin, "timeframe": tf, "direction": direction,
                "open": candle["open"], "high": candle["high"],
                "low": candle["low"], "close": candle["close"],
                "volume": candle["volume"], "price_change_pct": round(pct, 4),
                "candle_start": candle["candle_start"],
                "candle_end": candle["candle_end"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            try:
                await self.redis_client.setex(f"current_candle:{key}", 60, json.dumps(redis_data))
            except Exception as e:
                logger.error(f"[REDIS] binance_candle write error: {e}")

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

    async def _update_chainlink_candle(self, coin: str, price: float):
        if not self.redis_client:
            return

        for tf in TIMEFRAMES:
            key = f"{coin}_{tf}"
            candle_start, candle_end = self._get_candle_window(tf)

            candle = self.chainlink_candles.get(key)
            if not candle or candle["candle_start"] != candle_start.isoformat():
                candle = {
                    "open": price, "high": price, "low": price, "close": price,
                    "candle_start": candle_start.isoformat(),
                    "candle_end": candle_end.isoformat(),
                }
                self.chainlink_candles[key] = candle
            else:
                candle["high"] = max(candle["high"], price)
                candle["low"] = min(candle["low"], price)
                candle["close"] = price

            direction = "up" if price > candle["open"] else ("down" if price < candle["open"] else "flat")
            pct = ((price - candle["open"]) / candle["open"] * 100) if candle["open"] else 0

            redis_data = {
                "coin": coin, "timeframe": tf, "direction": direction,
                "open": candle["open"], "high": candle["high"],
                "low": candle["low"], "close": candle["close"],
                "price_change_pct": round(pct, 4),
                "candle_start": candle["candle_start"],
                "candle_end": candle["candle_end"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            try:
                await self.redis_client.setex(f"chainlink_candle:{key}", 60, json.dumps(redis_data))
            except Exception as e:
                logger.error(f"[REDIS] chainlink_candle write error: {e}")

    # =========================================================================
    # Redis Pub/Sub - P4 paper_trader용
    # =========================================================================
    async def _pubsub_publish_orderbook(self, asset_id: str, market_info: Dict):
        """오더북 상태를 ch:orderbook:{coin}_{tf}_{side} 채널로 publish"""
        if not self.redis_client:
            return

        ob = self.orderbooks.get(asset_id)
        if not ob:
            return

        best_bid = max(ob.bids.keys()) if ob.bids else 0.0
        best_ask = min(ob.asks.keys()) if ob.asks else 1.0
        best_bid_size = ob.bids.get(best_bid, 0) if ob.bids else 0
        best_ask_size = ob.asks.get(best_ask, 0) if ob.asks else 0

        candle_start, candle_end = self._get_candle_window(TIMEFRAMES[0])

        msg = json.dumps({
            "coin": market_info["coin"],
            "timeframe": market_info["timeframe"],
            "side": market_info["side"],
            "best_bid": best_bid,
            "best_ask": best_ask,
            "best_bid_size": best_bid_size,
            "best_ask_size": best_ask_size,
            "mid_price": round((best_bid + best_ask) / 2, 4),
            "slug": ob.market_slug,
            "token_id": asset_id,
            "timestamp": time.time(),
            "candle_start_ts": int(candle_start.timestamp()),
            "candle_end_ts": int(candle_end.timestamp()),
        })

        channel = f"ch:orderbook:{market_info['coin']}_{market_info['timeframe']}_{market_info['side']}"
        try:
            await self.redis_client.publish(channel, msg)
            self.stats["pubsub_published"] += 1
        except Exception as e:
            logger.error(f"[PUBSUB] publish error: {e}")

    # =========================================================================
    # Polymarket CLOB WebSocket
    # =========================================================================
    async def start_polymarket_ws(self):
        while True:
            ws = None
            try:
                if self._next_poly_ws:
                    ws = self._next_poly_ws
                    self._next_poly_ws = None
                    logger.info("Polymarket WS: hot-swap to pre-connected WS")
                else:
                    if not self.asset_ids:
                        await asyncio.sleep(5)
                        continue
                    logger.info(f"Polymarket WS connecting, subscribing to {len(self.asset_ids)} tokens")
                    ws = await websockets.connect(
                        POLYMARKET_WS_URL,
                        ping_interval=None,
                        ping_timeout=None,
                    )
                    await ws.send(json.dumps({"assets_ids": self.asset_ids, "type": "market"}))
                    logger.info("Polymarket WS connected")

                self._poly_ws = ws

                async def ping_loop():
                    while True:
                        try:
                            await asyncio.sleep(10)
                            await ws.send("PING")
                        except Exception:
                            break

                ping_task = asyncio.create_task(ping_loop())
                self._poly_reconnect.clear()

                try:
                    async for message in ws:
                        if self._poly_reconnect.is_set():
                            self._poly_reconnect.clear()
                            logger.info("Polymarket WS: stopping current connection")
                            break

                        if message in ("PING", "PONG"):
                            continue

                        try:
                            data = json.loads(message)
                            if isinstance(data, list):
                                for item in data:
                                    await self._process_poly_message(item)
                            else:
                                await self._process_poly_message(data)
                        except Exception as e:
                            logger.error(f"Poly message error: {e}")
                            self.stats["poly_errors"] += 1
                finally:
                    self._poly_ws = None
                    ping_task.cancel()
                    try:
                        await ws.close()
                    except Exception:
                        pass

            except websockets.ConnectionClosed as e:
                logger.warning(f"Polymarket WS closed: {e}")
            except Exception as e:
                logger.error(f"Polymarket WS error: {e}")

            if self._next_poly_ws:
                continue
            logger.info("Polymarket WS reconnecting in 3s...")
            await asyncio.sleep(3)

    async def _process_poly_message(self, data: Dict):
        event_type = data.get("event_type")
        if event_type == "book":
            await self._handle_book(data)
        elif event_type == "price_change":
            await self._handle_price_change(data)

    def _parse_poly_timestamp(self, data: Dict) -> datetime:
        ts = data.get("timestamp")
        if ts is not None:
            try:
                ts_float = float(ts)
                if ts_float > 1e12:
                    return datetime.fromtimestamp(ts_float / 1000, tz=timezone.utc)
                return datetime.fromtimestamp(ts_float, tz=timezone.utc)
            except (ValueError, OSError):
                pass
        return datetime.now(timezone.utc)

    async def _handle_book(self, data: Dict):
        try:
            asset_id = data.get("asset_id")
            market_info = self.token_to_market.get(asset_id)
            if not market_info:
                return

            bids = {float(b["price"]): int(float(b["size"])) for b in data.get("bids", [])}
            asks = {float(a["price"]): int(float(a["size"])) for a in data.get("asks", [])}

            self.orderbooks[asset_id] = OrderbookState(
                bids=bids, asks=asks,
                market_slug=market_info["slug"],
                token_id=asset_id,
                updated_at=time.time()
            )

            msg_time = self._parse_poly_timestamp(data)
            self.stats["book_received"] += 1
            await self._publish_orderbook_to_redis(asset_id, market_info)
            self._buffer_orderbook_book(asset_id, market_info, msg_time)

            # Pub/sub for P4 paper_trader
            await self._pubsub_publish_orderbook(asset_id, market_info)

        except Exception as e:
            logger.error(f"Handle book error: {e}")
            self.stats["poly_errors"] += 1

    async def _handle_price_change(self, data: Dict):
        try:
            self.stats["price_change_received"] += 1
            triggered_markets = set()
            msg_time = self._parse_poly_timestamp(data)

            for change in data.get("price_changes", []):
                asset_id = change.get("asset_id")

                if asset_id not in self.orderbooks:
                    continue

                ob = self.orderbooks[asset_id]
                price = float(change["price"])
                size = int(float(change["size"]))
                side = change["side"].upper()
                book_side = ob.bids if side == "BUY" else ob.asks

                if size == 0:
                    book_side.pop(price, None)
                else:
                    book_side[price] = size

                ob.updated_at = time.time()

                market_info = self.token_to_market.get(asset_id)
                if market_info:
                    await self._publish_orderbook_to_redis(asset_id, market_info)
                    self._buffer_orderbook_change(asset_id, price, size, side, msg_time)

                    # Pub/sub for P4 (deduplicate per market pair)
                    market_key = (market_info["coin"], market_info["timeframe"], market_info["side"])
                    if market_key not in triggered_markets:
                        triggered_markets.add(market_key)
                        await self._pubsub_publish_orderbook(asset_id, market_info)

        except Exception as e:
            logger.error(f"Handle price_change error: {e}")
            self.stats["poly_errors"] += 1

    def _buffer_orderbook_book(self, asset_id: str, market_info: Dict, msg_time: datetime):
        try:
            ob = self.orderbooks.get(asset_id)
            if not ob or not ob.bids or not ob.asks:
                return
            sorted_bids = sorted(ob.bids.items(), reverse=True)
            sorted_asks = sorted(ob.asks.items())
            self.ob_book_buffer.append((
                msg_time,
                market_info["coin"], market_info["timeframe"], market_info["side"],
                json.dumps([[p, s] for p, s in sorted_bids]),
                json.dumps([[p, s] for p, s in sorted_asks]),
                ob.market_slug, asset_id,
            ))
        except Exception as e:
            logger.debug(f"Buffer orderbook book error: {e}")

    def _buffer_orderbook_change(self, asset_id: str, price: float, size: int, book_side: str, msg_time: datetime):
        try:
            self.ob_change_buffer.append((
                msg_time, asset_id, price, size, book_side,
            ))
        except Exception as e:
            logger.debug(f"Buffer orderbook change error: {e}")

    async def _publish_orderbook_to_redis(self, asset_id: str, market_info: Dict):
        """대시보드용 Redis key 업데이트"""
        if not self.redis_client:
            return
        try:
            if asset_id not in self.orderbooks:
                return
            ob = self.orderbooks[asset_id]
            bids = dict(ob.bids)
            asks = dict(ob.asks)

            best_bid = max(bids.keys()) if bids else 0.0
            best_ask = min(asks.keys()) if asks else 1.0

            sorted_bids = sorted(bids.items(), key=lambda x: x[0], reverse=True)[:5]
            sorted_asks = sorted(asks.items(), key=lambda x: x[0])[:5]

            market_key = f"{market_info['coin']}_{market_info['timeframe']}_{market_info['side']}"
            snapshot = OrderbookSnapshot(
                timestamp=time.time(),
                market_key=market_key,
                market_slug=market_info["slug"],
                token_id=asset_id,
                best_bid=best_bid,
                best_ask=best_ask,
                bids=[OrderbookLevel(price=p, size=s) for p, s in sorted_bids],
                asks=[OrderbookLevel(price=p, size=s) for p, s in sorted_asks],
            )

            key = f"orderbook:{market_key}"
            await self.redis_client.setex(key, 60, json.dumps(snapshot.to_dict()))
            self.stats["redis_updates"] += 1

        except Exception as e:
            logger.error(f"Redis publish error: {e}")

    # =========================================================================
    # Market Refresh (캔들 경계 hot-swap) - 5분 주기
    # =========================================================================
    async def market_refresh_loop(self):
        while True:
            try:
                # 5분봉 기준으로 리프레시 (가장 짧은 주기)
                sleep_seconds = get_seconds_to_next_candle("5m")

                prefetch_lead = 5.0
                if sleep_seconds > prefetch_lead:
                    await asyncio.sleep(sleep_seconds - prefetch_lead)

                next_ts = int(get_next_candle_start("5m"))
                logger.info(f"[MARKET_REFRESH] Pre-fetching next candle markets (ts={next_ts})...")
                result = await self._fetch_markets(quiet=True, override_timestamp=next_ts)
                if not result:
                    logger.warning("[MARKET_REFRESH] Pre-fetch failed, retrying at boundary")
                    remaining = get_seconds_to_next_candle("5m")
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                    result = await self._fetch_markets(quiet=False)
                    if not result:
                        logger.warning("[MARKET_REFRESH] Failed to reload markets")
                        continue

                new_token_to_market, new_asset_ids = result
                new_ws = None
                try:
                    new_ws = await websockets.connect(
                        POLYMARKET_WS_URL,
                        ping_interval=None,
                        ping_timeout=None,
                    )
                    await new_ws.send(json.dumps({"assets_ids": new_asset_ids, "type": "market"}))
                    logger.info(f"[MARKET_REFRESH] Pre-connected new WS with {len(new_asset_ids)} tokens")
                except Exception as e:
                    logger.warning(f"[MARKET_REFRESH] Pre-connect failed: {e}")
                    if new_ws:
                        try:
                            await new_ws.close()
                        except Exception:
                            pass
                        new_ws = None

                remaining = get_seconds_to_next_candle("5m")
                if remaining > 0:
                    await asyncio.sleep(remaining)

                # 경계 도달 → 스냅샷 저장 + 오더북 클리어
                boundary_time = datetime.now(timezone.utc)
                for asset_id in list(self.orderbooks.keys()):
                    market_info = self.token_to_market.get(asset_id)
                    if market_info:
                        self._buffer_orderbook_book(asset_id, market_info, boundary_time)
                self.orderbooks.clear()
                self._apply_markets(*result)

                # P4에 캔들 경계 알림 (채널명에 timeframe 포함)
                if self.redis_client:
                    try:
                        ts = time.time()
                        await self.redis_client.publish("ch:candle_boundary:5m", json.dumps({
                            "event": "candle_boundary",
                            "timeframe": "5m",
                            "timestamp": ts,
                        }))
                        logger.info("[MARKET_REFRESH] Published candle_boundary:5m")

                        # 15분/1시간/4시간 경계 체크
                        now_et = datetime.now(ET)
                        now_utc = datetime.now(timezone.utc)
                        if now_et.minute % 15 == 0:
                            await self.redis_client.publish("ch:candle_boundary:15m", json.dumps({
                                "event": "candle_boundary",
                                "timeframe": "15m",
                                "timestamp": ts,
                            }))
                            logger.info("[MARKET_REFRESH] Published candle_boundary:15m")

                        if now_et.minute == 0:
                            await self.redis_client.publish("ch:candle_boundary:1h", json.dumps({
                                "event": "candle_boundary",
                                "timeframe": "1h",
                                "timestamp": ts,
                            }))
                            logger.info("[MARKET_REFRESH] Published candle_boundary:1h")

                            # 4시간봉은 UTC 기준 (0, 4, 8, 12, 16, 20시)
                            if now_utc.hour % 4 == 0:
                                await self.redis_client.publish("ch:candle_boundary:4h", json.dumps({
                                    "event": "candle_boundary",
                                    "timeframe": "4h",
                                    "timestamp": ts,
                                }))
                                logger.info("[MARKET_REFRESH] Published candle_boundary:4h")
                    except Exception as e:
                        logger.error(f"[MARKET_REFRESH] Failed to publish candle_boundary: {e}")

                # Hot-swap
                if new_ws:
                    self._next_poly_ws = new_ws
                    self._poly_reconnect.set()
                    logger.info("[MARKET_REFRESH] Hot-swap triggered → new WS ready")
                elif self._poly_ws:
                    try:
                        await self._poly_ws.send(json.dumps({"assets_ids": self.asset_ids, "type": "market"}))
                        logger.info(f"[MARKET_REFRESH] Fallback: re-subscribed on existing WS")
                    except Exception as e:
                        logger.warning(f"[MARKET_REFRESH] Re-subscribe failed ({e}), forcing reconnect")
                        self._poly_reconnect.set()
                else:
                    logger.warning("[MARKET_REFRESH] No active WS, will connect on next loop")

            except Exception as e:
                logger.error(f"[MARKET_REFRESH] Error: {e}")
                await asyncio.sleep(60)

    # =========================================================================
    # RTDS WebSocket (Binance + Chainlink 실시간 가격)
    # =========================================================================
    async def _publish_rtds_price(self, source: str, coin: str, price: float, timestamp: int):
        if not self.redis_client:
            return
        try:
            data = json.dumps({
                "value": price,
                "timestamp": timestamp,
                "source": source,
                "symbol": coin.upper(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            await self.redis_client.setex(f"rtds_price:{source.lower()}:{coin}", 60, data)
            hash_key = f"rtds_price:{coin}"
            await self.redis_client.hset(hash_key, source.lower(), data)
            await self.redis_client.expire(hash_key, 60)
        except Exception as e:
            logger.error(f"[REDIS] rtds_price write error: {e}")

    async def start_rtds_stream(self):
        reconnect_count = 0

        while True:
            try:
                logger.info(f"[RTDS] Connecting to {RTDS_WSS_URL}...")

                async with websockets.connect(
                    RTDS_WSS_URL,
                    ping_interval=None,
                    ping_timeout=None,
                ) as ws:
                    logger.info("[RTDS] Connected successfully!")
                    self.rtds_connected = True
                    reconnect_count = 0

                    async def ping_loop():
                        while True:
                            try:
                                await asyncio.sleep(5)
                                await ws.send("PING")
                            except Exception:
                                break

                    ping_task = asyncio.create_task(ping_loop())

                    await ws.send(json.dumps({
                        "action": "subscribe",
                        "subscriptions": [{"topic": "crypto_prices", "type": "update"}],
                    }))
                    logger.info(f"[RTDS] Subscribed to crypto_prices (Binance): {RTDS_BINANCE_SYMBOLS}")

                    await ws.send(json.dumps({
                        "action": "subscribe",
                        "subscriptions": [{"topic": "crypto_prices_chainlink", "type": "*"}],
                    }))
                    logger.info(f"[RTDS] Subscribed to crypto_prices_chainlink: {RTDS_CHAINLINK_SYMBOLS}")

                    try:
                        async for message in ws:
                            await self._handle_rtds_message(message)
                    finally:
                        ping_task.cancel()

            except websockets.ConnectionClosed as e:
                logger.warning(f"[RTDS] Connection closed: {e}")
            except Exception as e:
                logger.error(f"[RTDS] Connection error: {e}")

            self.rtds_connected = False
            reconnect_count += 1
            delay = min(RTDS_RECONNECT_DELAY * (2 ** min(reconnect_count - 1, 4)), 60)
            logger.info(f"[RTDS] Reconnecting in {delay}s... (attempt {reconnect_count})")
            await asyncio.sleep(delay)

    async def _handle_rtds_message(self, message: str):
        if not message.strip():
            return

        try:
            data = json.loads(message)

            if isinstance(data, dict) and "statusCode" in data:
                if data.get("statusCode") != 200:
                    logger.warning(f"[RTDS] Error: {data.get('body', {}).get('message', 'Unknown')}")
                return

            if isinstance(data, dict) and data.get("type") == "subscribed":
                logger.info(f"[RTDS] Subscription confirmed: {data.get('topic', 'unknown')}")
                return

            if not (isinstance(data, dict) and "payload" in data):
                return

            topic = data.get("topic", "")
            payload = data.get("payload", {})

            is_chainlink = "chainlink" in topic.lower()
            source = "chainlink" if is_chainlink else "binance"

            symbol_raw = payload.get("symbol", "")
            timestamp = payload.get("timestamp", 0)
            value = payload.get("value", 0)

            if not (value > 0 and symbol_raw):
                return

            symbol_lower = symbol_raw.lower()
            if source == "binance" and symbol_lower not in RTDS_BINANCE_SYMBOLS:
                return
            if source == "chainlink" and symbol_lower not in RTDS_CHAINLINK_SYMBOLS:
                return

            coin = RTDS_SYMBOL_MAP.get(symbol_lower)
            if not coin:
                return

            await self._publish_rtds_price(source, coin, value, timestamp)

            if source == "binance":
                self.binance_prices[coin] = value
                self.stats["rtds_binance"] += 1

                await self._publish_exchange_price(coin, value)
                await self._update_binance_candle(coin, value)

                if self.redis_client:
                    try:
                        await self.redis_client.setex(
                            f"binance_price:{coin}", 30,
                            json.dumps({"coin": coin, "price": value})
                        )
                    except Exception as e:
                        logger.error(f"[REDIS] binance_price(rtds) write error: {e}")

                ts = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc) if timestamp else datetime.now(timezone.utc)
                self.price_buffer.append({
                    "time": ts.replace(microsecond=0),
                    "coin": coin,
                    "binance_price": value,
                })

            else:  # chainlink
                self.chainlink_prices[coin] = value
                self.stats["rtds_chainlink"] += 1

                await self._update_chainlink_candle(coin, value)

                ts = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc) if timestamp else datetime.now(timezone.utc)
                self.price_buffer.append({
                    "time": ts.replace(microsecond=0),
                    "coin": coin,
                    "chainlink_price": value,
                    "chainlink_source": "rtds",
                })

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"[RTDS] Message error: {e}")
            self.stats["rtds_errors"] += 1

    # =========================================================================
    # Chainlink (Fallback)
    # =========================================================================
    async def fetch_chainlink_prices(self):
        async with aiohttp.ClientSession() as session:
            for coin, feed_id in CHAINLINK_FEED_IDS.items():
                try:
                    query = "LIVE_STREAM_REPORTS_QUERY"
                    variables = f'{{"feedId":"{feed_id}"}}'
                    url = f"https://data.chain.link/api/query-timescale?query={query}&variables={variables}"

                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                        if response.status == 200:
                            data = await response.json()
                            nodes = data.get("data", {}).get("liveStreamReports", {}).get("nodes", [])
                            if nodes:
                                latest_price = float(nodes[0]["price"]) / 1e18
                                self.chainlink_prices[coin] = latest_price
                                self.stats["chainlink_updates"] += 1
                                await self._update_chainlink_candle(coin, latest_price)

                                for node in nodes:
                                    price = float(node["price"]) / 1e18
                                    ts = datetime.fromisoformat(node["validFromTimestamp"])
                                    self.price_buffer.append({
                                        "time": ts.replace(microsecond=0),
                                        "coin": coin,
                                        "rest_chainlink_price": price,
                                    })

                except Exception as e:
                    logger.error(f"Chainlink {coin} error: {e}")

    async def chainlink_loop(self):
        while True:
            try:
                await self.fetch_chainlink_prices()
            except Exception as e:
                logger.error(f"Chainlink loop error: {e}")
            await asyncio.sleep(CHAINLINK_INTERVAL)

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

    async def flush_buffers(self):
        prices = list(self.price_buffer)
        self.price_buffer.clear()

        ob_books = list(self.ob_book_buffer)
        self.ob_book_buffer.clear()
        ob_changes = list(self.ob_change_buffer)
        self.ob_change_buffer.clear()

        async with self.db_pool.acquire() as conn:
            # --- coin_prices (RTDS binance/chainlink + REST chainlink) ---
            price_agg = {}
            for p in prices:
                key = (p["time"], p["coin"])
                if key not in price_agg:
                    price_agg[key] = {
                        "chainlink_price": None, "binance_price": None,
                        "rest_chainlink_price": None, "chainlink_source": None,
                    }
                if p.get("chainlink_price") is not None:
                    price_agg[key]["chainlink_price"] = p["chainlink_price"]
                    price_agg[key]["chainlink_source"] = p.get("chainlink_source")
                if p.get("binance_price") is not None:
                    price_agg[key]["binance_price"] = p["binance_price"]
                if p.get("rest_chainlink_price") is not None:
                    price_agg[key]["rest_chainlink_price"] = p["rest_chainlink_price"]

            if price_agg:
                price_rows = [
                    (t, coin, vals["chainlink_price"], vals["rest_chainlink_price"],
                     vals["binance_price"], vals["chainlink_source"])
                    for (t, coin), vals in price_agg.items()
                ]
                try:
                    await conn.executemany("""
                        INSERT INTO coin_prices (time, coin, chainlink_price, rest_chainlink_price, binance_price, chainlink_source)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (coin, time) DO UPDATE SET
                            chainlink_price = COALESCE(
                                EXCLUDED.chainlink_price, coin_prices.chainlink_price,
                                EXCLUDED.rest_chainlink_price, coin_prices.rest_chainlink_price
                            ),
                            rest_chainlink_price = COALESCE(EXCLUDED.rest_chainlink_price, coin_prices.rest_chainlink_price),
                            binance_price = COALESCE(EXCLUDED.binance_price, coin_prices.binance_price),
                            chainlink_source = COALESCE(
                                EXCLUDED.chainlink_source, coin_prices.chainlink_source,
                                CASE WHEN COALESCE(EXCLUDED.rest_chainlink_price, coin_prices.rest_chainlink_price) IS NOT NULL
                                     THEN 'rest' END
                            )
                    """, price_rows)
                except Exception as e:
                    logger.error(f"Flush coin_prices error: {e}")

            # --- orderbook_books ---
            if ob_books:
                try:
                    await conn.executemany("""
                        INSERT INTO orderbook_books (time, coin, timeframe, side, bids, asks, market_slug, token_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        ON CONFLICT (coin, timeframe, side, time) DO UPDATE
                        SET bids=EXCLUDED.bids, asks=EXCLUDED.asks
                    """, ob_books)
                    self.stats["ob_book_saves"] += len(ob_books)
                except Exception as e:
                    logger.error(f"Flush orderbook books error: {e}")

            # --- orderbook_changes ---
            if ob_changes:
                try:
                    await conn.executemany("""
                        INSERT INTO orderbook_changes (time, token_id, price, size, book_side)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (token_id, time, price, book_side) DO UPDATE SET size=EXCLUDED.size
                    """, ob_changes)
                    self.stats["ob_change_saves"] += len(ob_changes)
                except Exception as e:
                    logger.error(f"Flush orderbook changes error: {e}")

    # =========================================================================
    # Stats
    # =========================================================================
    async def stats_loop(self):
        while True:
            await asyncio.sleep(STATS_INTERVAL)
            uptime = int(time.time() - self.start_time)
            rtds_status = "ON" if self.rtds_connected else "OFF"
            logger.info(
                f"[STATS] uptime={uptime}s | "
                f"rtds={rtds_status} (bin={self.stats.get('rtds_binance', 0)}/cl={self.stats.get('rtds_chainlink', 0)}) | "
                f"book={self.stats['book_received']} | "
                f"price_change={self.stats.get('price_change_received', 0)} | "
                f"redis_ob={self.stats.get('redis_updates', 0)} | "
                f"pubsub={self.stats.get('pubsub_published', 0)} | "
                f"ob_books={self.stats.get('ob_book_saves', 0)} ob_changes={self.stats.get('ob_change_saves', 0)} | "
                f"chainlink={self.stats.get('chainlink_updates', 0)}"
            )

    # =========================================================================
    # 메인 실행
    # =========================================================================
    async def run(self):
        logger.info("=" * 60)
        logger.info("P1: RTDS (Real-Time Data Service) Starting")
        logger.info("=" * 60)
        logger.info(f"Orderbook Coins: {ORDERBOOK_COINS}")
        logger.info(f"Price Coins: {PRICE_COINS}")
        logger.info(f"Timeframes: {TIMEFRAMES}")
        logger.info("=" * 60)

        if not await self.connect_db():
            logger.error("Failed to connect to DB")
            return

        await self.connect_redis()

        if not await self.load_active_markets():
            logger.warning("No Polymarket markets found")

        self.setup_signal_handlers()

        await asyncio.gather(
            self.start_polymarket_ws(),
            self.start_rtds_stream(),
            self.chainlink_loop(),
            self.market_refresh_loop(),
            self.db_flush_loop(),
            self.stats_loop(),
            self._health_file_loop(),
        )

def main():
    service = RtdsService()
    asyncio.run(service.run())


if __name__ == "__main__":
    main()
