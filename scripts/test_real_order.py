#!/usr/bin/env python3
"""
Real Order 테스트 스크립트

BTC 15분 마켓에 $1 테스트 주문을 날려봅니다.
- Redis에서 현재 오더북 조회
- Polymarket CLOB에 FAK 주문
- 텔레그램 알림 테스트

Usage:
    uv run python scripts/test_real_order.py          # 대화형
    uv run python scripts/test_real_order.py --yes    # 자동 실행
    uv run python scripts/test_real_order.py --dry    # 주문 안함 (테스트만)
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import aiohttp
import redis.asyncio as redis

# 환경변수
POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))

# 테스트 금액 (Polymarket 최소 $1)
TEST_BET_AMOUNT = 2.0  # $2 (최소 $1 이상 필요)


def get_current_candle_times(timeframe: str = "15m"):
    """현재 캔들 시작/종료 시간 계산"""
    now = datetime.now(timezone.utc)
    minutes = 15 if timeframe == "15m" else 60

    total_minutes = now.hour * 60 + now.minute
    candle_index = total_minutes // minutes
    candle_start_minute = candle_index * minutes

    candle_start = now.replace(
        hour=candle_start_minute // 60,
        minute=candle_start_minute % 60,
        second=0, microsecond=0
    )
    candle_end = candle_start + timedelta(minutes=minutes)

    return candle_start, candle_end


def generate_slug(coin: str, timeframe: str) -> str:
    """마켓 slug 생성"""
    candle_start, _ = get_current_candle_times(timeframe)
    ts = int(candle_start.timestamp())
    coin_upper = coin.upper()

    if timeframe == "15m":
        return f"Will {coin_upper} go up or down in the next 15 minutes?-{ts}"
    else:
        return f"Will {coin_upper} go up or down in the next hour?-{ts}"


async def send_telegram(message: str):
    """텔레그램 메시지 전송"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Not configured, skipping")
        return False

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = await session.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            }, timeout=aiohttp.ClientTimeout(total=10))

            result = await resp.json()
            if result.get("ok"):
                print(f"[TELEGRAM] Message sent successfully")
                return True
            else:
                print(f"[TELEGRAM] Failed: {result}")
                return False
    except Exception as e:
        print(f"[TELEGRAM] Error: {e}")
        return False


async def get_orderbook_from_redis(coin: str = "btc", timeframe: str = "15m", side: str = "up"):
    """Redis에서 오더북 조회"""
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

        # 오더북 캐시 키
        key = f"orderbook:{coin}_{timeframe}_{side}"
        data = await r.get(key)

        if data:
            return json.loads(data)

        # 다른 키 형식 시도
        key2 = f"ch:orderbook:{coin}_{timeframe}_{side}"
        data2 = await r.get(key2)

        if data2:
            return json.loads(data2)

        print(f"[REDIS] No orderbook found for {key}")

        # 현재 Redis 키 목록 출력
        keys = await r.keys("*orderbook*")
        print(f"[REDIS] Available orderbook keys: {keys[:10]}")

        await r.close()
        return None

    except Exception as e:
        print(f"[REDIS] Error: {e}")
        return None


async def place_test_order(token_id: str, price: float, contracts: int, max_bump: int = 5):
    """테스트 주문 실행 (price bump retry 포함)"""
    if not POLYMARKET_PRIVATE_KEY:
        print("[CLOB] POLYMARKET_PRIVATE_KEY not set")
        return None

    if not POLYMARKET_PROXY_ADDRESS:
        print("[CLOB] POLYMARKET_PROXY_ADDRESS not set")
        return None

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        print(f"[CLOB] Initializing client...")
        print(f"  Host: {POLYMARKET_HOST}")
        print(f"  Chain ID: {POLYMARKET_CHAIN_ID}")
        print(f"  Proxy: {POLYMARKET_PROXY_ADDRESS[:10]}...{POLYMARKET_PROXY_ADDRESS[-6:]}")

        # signature_type=2 (Poly Proxy) - Proxy 사용
        client = ClobClient(
            host=POLYMARKET_HOST,
            key=POLYMARKET_PRIVATE_KEY,
            chain_id=POLYMARKET_CHAIN_ID,
            signature_type=2,  # Poly Proxy
            funder=POLYMARKET_PROXY_ADDRESS,
        )

        # API credentials 생성
        print("[CLOB] Creating API credentials...")
        api_creds = client.create_or_derive_api_creds()
        client.set_api_creds(api_creds)
        print("[CLOB] API credentials set")

        # Price bump retry loop
        for bump in range(max_bump + 1):
            adjusted_price = round(price + (bump * 0.01), 2)

            if adjusted_price >= 0.99:
                print(f"[CLOB] Price too high: {adjusted_price}, stopping")
                break

            print(f"[CLOB] Creating order: token_id={token_id[:20]}..., price={adjusted_price} (bump={bump}), contracts={contracts}")

            order_args = OrderArgs(
                token_id=token_id,
                price=adjusted_price,
                size=float(contracts),
                side=BUY,
            )

            signed_order = client.create_order(order_args)
            print("[CLOB] Order signed, posting...")

            try:
                response = client.post_order(signed_order, OrderType.FAK)
                print(f"[CLOB] Response: {response}")

                # 성공 체크
                if isinstance(response, dict):
                    order_id = response.get("orderID") or response.get("orderId", "")
                    if order_id:
                        print(f"[CLOB] SUCCESS! Order ID: {order_id}")
                        return response

                return response

            except Exception as e:
                error_msg = str(e).lower()
                if "no orders found" in error_msg and bump < max_bump:
                    print(f"[CLOB] No match at {adjusted_price}, trying bump={bump+1}...")
                    continue
                else:
                    raise

        print("[CLOB] All bumps exhausted, no match found")
        return None

    except ImportError:
        print("[CLOB] py-clob-client not installed. Run: pip install py-clob-client")
        return None
    except Exception as e:
        print(f"[CLOB] Error: {e}")
        import traceback
        traceback.print_exc()
        return None


async def main(auto_yes: bool = False, dry_run: bool = False):
    print("=" * 60)
    print("Real Order Test Script")
    if dry_run:
        print("  [DRY RUN MODE - No actual order will be placed]")
    print("=" * 60)

    # 1. 환경변수 체크
    print("\n[1] Checking environment variables...")
    print(f"  POLYMARKET_PRIVATE_KEY: {'SET' if POLYMARKET_PRIVATE_KEY else 'NOT SET'}")
    print(f"  POLYMARKET_PROXY_ADDRESS: {'SET' if POLYMARKET_PROXY_ADDRESS else 'NOT SET'}")
    print(f"  TELEGRAM_BOT_TOKEN: {'SET' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
    print(f"  TELEGRAM_CHAT_ID: {'SET' if TELEGRAM_CHAT_ID else 'NOT SET'}")
    print(f"  REDIS: {REDIS_HOST}:{REDIS_PORT}")

    if not POLYMARKET_PRIVATE_KEY or not POLYMARKET_PROXY_ADDRESS:
        print("\n[ERROR] Polymarket credentials not set in .env")
        return

    # 2. 텔레그램 테스트
    print("\n[2] Testing Telegram...")
    await send_telegram("<b>[TEST]</b> Real Order Test Script Started")

    # 3. 현재 마켓 정보
    print("\n[3] Current market info...")
    coin = "btc"
    timeframe = "15m"
    slug = generate_slug(coin, timeframe)
    candle_start, candle_end = get_current_candle_times(timeframe)

    print(f"  Coin: {coin.upper()}")
    print(f"  Timeframe: {timeframe}")
    print(f"  Candle: {candle_start.isoformat()} ~ {candle_end.isoformat()}")
    print(f"  Slug: {slug}")

    # 4. Redis에서 오더북 조회
    print("\n[4] Getting orderbook from Redis...")
    up_orderbook = await get_orderbook_from_redis(coin, timeframe, "up")
    down_orderbook = await get_orderbook_from_redis(coin, timeframe, "down")

    if not up_orderbook:
        print("[WARNING] No UP orderbook in Redis. Is rtds running?")
        print("Using manual token_id input...")

        # 수동 입력 모드
        token_id = input("Enter UP token_id (or press Enter to skip order test): ").strip()
        if not token_id:
            print("Skipping order test.")
            return

        price = float(input("Enter price (e.g., 0.50): ").strip())
        up_orderbook = {"token_id": token_id, "best_ask": price}

    print(f"  UP orderbook: best_ask={up_orderbook.get('best_ask')}, token_id={up_orderbook.get('token_id', '')[:30]}...")

    if down_orderbook:
        print(f"  DOWN orderbook: best_ask={down_orderbook.get('best_ask')}")

    # 5. 테스트 주문 확인
    print("\n[5] Test order details...")
    token_id = up_orderbook.get("token_id", "")
    entry_price = up_orderbook.get("best_ask", 0.5)
    contracts = int(TEST_BET_AMOUNT / entry_price)
    cost = contracts * entry_price

    print(f"  Side: UP")
    print(f"  Entry price: ${entry_price:.4f}")
    print(f"  Contracts: {contracts}")
    print(f"  Cost: ${cost:.4f}")
    print(f"  Token ID: {token_id[:50]}...")

    # 6. 사용자 확인
    if dry_run:
        print("\n[DRY RUN] Skipping actual order placement")
        await send_telegram("<b>[TEST DRY RUN]</b> Order would have been placed (dry run mode)")
        return

    if auto_yes:
        print("\n>>> Auto-confirming order (--yes flag)")
        confirm = "yes"
    else:
        confirm = input("\n>>> Place this order? (yes/no): ").strip().lower()

    if confirm != "yes":
        print("Order cancelled.")
        await send_telegram("<b>[TEST]</b> Order cancelled by user")
        return

    # 7. 주문 실행
    print("\n[6] Placing order...")
    await send_telegram(
        f"<b>[TEST ORDER]</b>\n"
        f"Side: UP\n"
        f"Price: ${entry_price:.4f}\n"
        f"Contracts: {contracts}\n"
        f"Cost: ${cost:.4f}"
    )

    result = await place_test_order(token_id, entry_price, contracts)

    # 8. 결과
    print("\n[7] Result:")
    if result:
        if isinstance(result, dict) and result.get("orderID"):
            order_id = result.get("orderID") or result.get("orderId", "")
            print(f"  SUCCESS! Order ID: {order_id}")
            await send_telegram(
                f"<b>[TEST ORDER SUCCESS]</b>\n"
                f"Order ID: {order_id}"
            )
        else:
            print(f"  Response: {result}")
            await send_telegram(f"<b>[TEST ORDER]</b> Response: {result}")
    else:
        print("  FAILED")
        await send_telegram("<b>[TEST ORDER FAILED]</b>")

    print("\n" + "=" * 60)
    print("Test completed!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real Order Test Script")
    parser.add_argument("--yes", "-y", action="store_true", help="Auto-confirm order placement")
    parser.add_argument("--dry", "-d", action="store_true", help="Dry run (no actual order)")
    args = parser.parse_args()

    asyncio.run(main(auto_yes=args.yes, dry_run=args.dry))
