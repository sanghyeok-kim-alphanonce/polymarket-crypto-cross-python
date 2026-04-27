#!/usr/bin/env python3
"""CLOB API 키 등록"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")

print("=" * 60)
print("CLOB API Key Registration")
print("=" * 60)

try:
    from py_clob_client_v2 import ClobClient

    print("\n[1] Creating ClobClient...")
    client = ClobClient(
        host=POLYMARKET_HOST,
        key=POLYMARKET_PRIVATE_KEY,
        chain_id=POLYMARKET_CHAIN_ID,
        signature_type=2,
        funder=POLYMARKET_PROXY_ADDRESS,
    )

    print("\n[2] Creating new API key (registering with server)...")
    try:
        # create_api_key()는 서버에 새 API 키를 등록합니다
        api_creds = client.create_api_key()
        print(f"  SUCCESS!")
        print(f"  API Key: {api_creds.api_key}")
        print(f"  API Secret: {api_creds.api_secret}")
        print(f"  Passphrase: {api_creds.api_passphrase}")

        # 저장
        client.set_api_creds(api_creds)
        print("\n[3] Verifying API key...")

        # 테스트
        result = client.get_api_keys()
        print(f"  get_api_keys: {result}")

    except Exception as e:
        print(f"  create_api_key failed: {e}")

        print("\n[2b] Trying create_or_derive_api_key (creates if not exists)...")
        api_creds = client.create_or_derive_api_key()
        print(f"  API Key: {api_creds.api_key}")
        client.set_api_creds(api_creds)

        print("\n[3] Verifying...")
        try:
            result = client.get_api_keys()
            print(f"  get_api_keys: {result}")
        except Exception as e2:
            print(f"  Still failing: {e2}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
