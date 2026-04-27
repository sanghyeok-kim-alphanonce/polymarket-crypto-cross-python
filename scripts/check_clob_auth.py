#!/usr/bin/env python3
"""CLOB API 인증 디버깅"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")

print("=" * 60)
print("CLOB API Authentication Debug")
print("=" * 60)

try:
    from py_clob_client_v2 import ClobClient

    print("\n[1] Creating ClobClient...")
    print(f"  Host: {POLYMARKET_HOST}")
    print(f"  Chain ID: {POLYMARKET_CHAIN_ID}")
    print(f"  Signature Type: 2 (Poly Proxy)")
    print(f"  Funder (Proxy): {POLYMARKET_PROXY_ADDRESS}")

    client = ClobClient(
        host=POLYMARKET_HOST,
        key=POLYMARKET_PRIVATE_KEY,
        chain_id=POLYMARKET_CHAIN_ID,
        signature_type=2,
        funder=POLYMARKET_PROXY_ADDRESS,
    )

    print("\n[2] Deriving API credentials...")
    try:
        api_creds = client.derive_api_key()
        print(f"  Derived API Key: {api_creds.api_key[:20]}...")
        print(f"  API Secret: {api_creds.api_secret[:20]}...")
        print(f"  Passphrase: {api_creds.api_passphrase[:10]}...")
        client.set_api_creds(api_creds)
        print("  API credentials set!")
    except Exception as e:
        print(f"  derive_api_key failed: {e}")
        print("\n[2b] Trying create_or_derive_api_key...")
        api_creds = client.create_or_derive_api_key()
        print(f"  API Key: {api_creds.api_key[:20]}...")
        client.set_api_creds(api_creds)

    print("\n[3] Testing API - get_ok...")
    try:
        result = client.get_ok()
        print(f"  get_ok: {result}")
    except Exception as e:
        print(f"  get_ok failed: {e}")

    print("\n[4] Testing API - get_api_keys...")
    try:
        result = client.get_api_keys()
        print(f"  get_api_keys: {result}")
    except Exception as e:
        print(f"  get_api_keys failed: {e}")

    print("\n[5] Testing API - get_balance_allowance...")
    try:
        # V2: AssetType.COLLATERAL (V1 USDC.e → V2 pUSD)
        from py_clob_client_v2 import AssetType, BalanceAllowanceParams
        result = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        print(f"  Balance: {result}")
    except Exception as e:
        print(f"  get_balance_allowance failed: {e}")

except ImportError as e:
    print(f"Import error: {e}")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
