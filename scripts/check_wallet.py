#!/usr/bin/env python3
"""지갑 주소 확인"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from eth_account import Account

private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
proxy_address = os.getenv("POLYMARKET_PROXY_ADDRESS", "")

if private_key:
    account = Account.from_key(private_key)
    print(f"EOA Address (from private key): {account.address}")
    print(f"Proxy Address (from .env):      {proxy_address}")
    print()
    if account.address.lower() == proxy_address.lower():
        print("WARNING: EOA와 Proxy가 같습니다. Proxy 주소는 Polymarket에서 발급받은 별도 주소여야 합니다.")
    else:
        print("EOA와 Proxy가 다릅니다. (정상)")
else:
    print("POLYMARKET_PRIVATE_KEY not set")
