#!/usr/bin/env python3
"""Proxy Address 확인 - Polymarket API로 조회"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")

print("=" * 60)
print("Proxy Address Verification")
print("=" * 60)

from eth_account import Account
account = Account.from_key(POLYMARKET_PRIVATE_KEY)
eoa_address = account.address

print(f"\nEOA Address: {eoa_address}")
print(f"Proxy in .env: {POLYMARKET_PROXY_ADDRESS}")

try:
    from py_clob_client_v2 import ClobClient

    # 방법 1: funder 없이 클라이언트 생성해서 proxy wallet 조회
    print("\n[1] Creating client WITHOUT funder to derive proxy...")
    client = ClobClient(
        host=POLYMARKET_HOST,
        key=POLYMARKET_PRIVATE_KEY,
        chain_id=POLYMARKET_CHAIN_ID,
    )

    # derive_api_key를 통해 내부적으로 사용하는 proxy 확인
    print(f"  Client creds: {client.creds}")

    # 방법 2: get_order_book 같은 public API 테스트
    print("\n[2] Testing public API (no auth needed)...")
    try:
        # 아무 마켓이나 조회
        result = client.get_last_trade_price(
            token_id="56385669358237702516874498267054444780736435750885538033894195095889556654849"
        )
        print(f"  get_last_trade_price: {result}")
    except Exception as e:
        print(f"  Error: {e}")

    # 방법 3: Signature Type 0 (EOA) 으로 시도
    print("\n[3] Trying with signature_type=0 (EOA, no proxy)...")
    client2 = ClobClient(
        host=POLYMARKET_HOST,
        key=POLYMARKET_PRIVATE_KEY,
        chain_id=POLYMARKET_CHAIN_ID,
        signature_type=0,  # EOA
    )

    try:
        api_creds = client2.create_or_derive_api_key()
        print(f"  API Key: {api_creds.api_key}")
        client2.set_api_creds(api_creds)

        result = client2.get_api_keys()
        print(f"  get_api_keys: {result}")
    except Exception as e:
        print(f"  Error: {e}")

    # 방법 4: Gamma API로 proxy wallet 조회
    print("\n[4] Checking Gamma API for proxy wallet...")
    import requests
    try:
        resp = requests.get(
            f"https://gamma-api.polymarket.com/proxy-wallet/{eoa_address}",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"  Proxy from Gamma API: {data}")
        else:
            print(f"  Status: {resp.status_code}, {resp.text[:100]}")
    except Exception as e:
        print(f"  Error: {e}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
