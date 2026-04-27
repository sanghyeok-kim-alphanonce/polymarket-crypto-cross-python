"""
Polymarket V2 cutover (2026-04-28) 운영 Safe 셋업 스크립트.

V2 Exchange는 V1과 다른 컨트랙트라 V1 approve가 그대로 적용 안 됨.
4/28 cutover 이전에 다음을 한 번 실행해야 cutover 직후부터 거래 가능:
  - V2 컨트랙트 10건 approve (USDC.e, pUSD, CTF)
  - (선택) USDC.e → pUSD wrap

이 스크립트는 위 작업을 단일 Safe meta-tx batch로 Polymarket Builder Relayer에
제출함. claimer 서비스가 redeemPositions에 쓰는 것과 같은 메커니즘.

Idempotency:
  - approve 10건은 MAX_UINT256으로 set → 재실행해도 무해
  - wrap은 amount > 0일 때만 실행 (지정 금액만큼 USDC.e 잠그고 pUSD 발행)
    → idempotent하지 않음. 의도적으로 opt-in.

Usage:
  # approve만 (가장 안전, 먼저 실행 권장):
  uv run python scripts/v2_approve_and_wrap.py

  # approve + 50 USDC.e wrap:
  uv run python scripts/v2_approve_and_wrap.py --wrap-usdc 50

  # 사전 검증 (calldata 출력만, 제출 안 함):
  uv run python scripts/v2_approve_and_wrap.py --wrap-usdc 50 --dry-run

Env (.env):
  POLYMARKET_PRIVATE_KEY
  POLYMARKET_PROXY_ADDRESS
  BUILDER_API_KEY_1
  BUILDER_SECRET_1
  BUILDER_PASS_PHRASE_1

References:
  - migration-knowhow.md §3.1 (approve batch), §3.2 (wrap), §4.4 (verify)
  - api-migration-guide.md (pUSD section)
"""

import argparse
import os
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from web3 import Web3

# === Polygon Mainnet contracts (migration-knowhow.md §2.5) ===
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
V2_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"
V2_NEG_RISK_EXCHANGE = "0xe2222d279d744050d28e00520010520000310F59"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"

RELAYER_URL = "https://relayer-v2.polymarket.com"
MAX_UINT256 = (1 << 256) - 1

# Minimal ABIs for calldata encoding (web3 contract.encode_abi)
ERC20_APPROVE_ABI = [{
    "inputs": [
        {"name": "spender", "type": "address"},
        {"name": "amount", "type": "uint256"},
    ],
    "name": "approve",
    "outputs": [{"type": "bool"}],
    "type": "function",
}]
ERC1155_SAFA_ABI = [{
    "inputs": [
        {"name": "operator", "type": "address"},
        {"name": "approved", "type": "bool"},
    ],
    "name": "setApprovalForAll",
    "outputs": [],
    "type": "function",
}]
# Onramp.wrap(asset, recipient, amount) — migration-knowhow.md §3.2
# 시그니처는 우리가 직접 검증하지 않았으므로, 첫 실행은 --dry-run + 작은 금액 권장.
ONRAMP_WRAP_ABI = [{
    "inputs": [
        {"name": "asset", "type": "address"},
        {"name": "recipient", "type": "address"},
        {"name": "amount", "type": "uint256"},
    ],
    "name": "wrap",
    "outputs": [],
    "type": "function",
}]


def build_txs(w3: Web3, safe_address: str, wrap_usdc_units: int) -> list:
    """Returns [(label, to_address, calldata_hex), ...]."""
    txs = []
    erc20 = w3.eth.contract(abi=ERC20_APPROVE_ABI)
    erc1155 = w3.eth.contract(abi=ERC1155_SAFA_ABI)

    # USDC.e approves: 4건 (Onramp 포함 — wrap에 필요)
    for label, spender in [
        ("V2_Exchange", V2_EXCHANGE),
        ("V2_NegRisk", V2_NEG_RISK_EXCHANGE),
        ("NegRiskAdapter", NEG_RISK_ADAPTER),
        ("Onramp", COLLATERAL_ONRAMP),
    ]:
        cd = erc20.encode_abi(
            abi_element_identifier="approve",
            args=[w3.to_checksum_address(spender), MAX_UINT256],
        )
        txs.append((f"USDC.e.approve({label})", USDC_E_ADDRESS, cd))

    # pUSD approves: 3건
    for label, spender in [
        ("V2_Exchange", V2_EXCHANGE),
        ("V2_NegRisk", V2_NEG_RISK_EXCHANGE),
        ("NegRiskAdapter", NEG_RISK_ADAPTER),
    ]:
        cd = erc20.encode_abi(
            abi_element_identifier="approve",
            args=[w3.to_checksum_address(spender), MAX_UINT256],
        )
        txs.append((f"pUSD.approve({label})", PUSD_ADDRESS, cd))

    # CTF setApprovalForAll: 3건
    for label, operator in [
        ("V2_Exchange", V2_EXCHANGE),
        ("V2_NegRisk", V2_NEG_RISK_EXCHANGE),
        ("NegRiskAdapter", NEG_RISK_ADAPTER),
    ]:
        cd = erc1155.encode_abi(
            abi_element_identifier="setApprovalForAll",
            args=[w3.to_checksum_address(operator), True],
        )
        txs.append((f"CTF.setApprovalForAll({label})", CTF_ADDRESS, cd))

    # Optional: wrap USDC.e → pUSD
    if wrap_usdc_units > 0:
        onramp = w3.eth.contract(abi=ONRAMP_WRAP_ABI)
        cd = onramp.encode_abi(
            abi_element_identifier="wrap",
            args=[
                w3.to_checksum_address(USDC_E_ADDRESS),
                w3.to_checksum_address(safe_address),
                wrap_usdc_units,
            ],
        )
        usdc_human = wrap_usdc_units / 1_000_000
        txs.append((f"Onramp.wrap(USDC.e, {usdc_human} USD)", COLLATERAL_ONRAMP, cd))

    return txs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--wrap-usdc",
        type=float,
        default=0.0,
        help="Wrap amount in USDC.e units (default 0 = skip wrap).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build & print calldata, don't submit to relayer.",
    )
    args = parser.parse_args()

    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    safe_address = os.environ.get("POLYMARKET_PROXY_ADDRESS", "")
    builder_key = os.environ.get("BUILDER_API_KEY_1", "")
    builder_secret = os.environ.get("BUILDER_SECRET_1", "")
    builder_pass = os.environ.get("BUILDER_PASS_PHRASE_1", "")

    required = {
        "POLYMARKET_PRIVATE_KEY": private_key,
        "POLYMARKET_PROXY_ADDRESS": safe_address,
        "BUILDER_API_KEY_1": builder_key,
        "BUILDER_SECRET_1": builder_secret,
        "BUILDER_PASS_PHRASE_1": builder_pass,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print("ERROR: required env vars missing:", file=sys.stderr)
        for k, v in required.items():
            print(f"  {'OK ' if v else '?? '} {k}", file=sys.stderr)
        sys.exit(1)

    w3 = Web3()
    safe_address = w3.to_checksum_address(safe_address)
    wrap_usdc_units = int(round(args.wrap_usdc * 1_000_000))  # USDC.e: 6 decimals

    txs = build_txs(w3, safe_address, wrap_usdc_units)

    print("=" * 60)
    print(f"Safe: {safe_address}")
    print(f"Relayer: {RELAYER_URL}")
    print(f"Builder Key: {builder_key[:12]}...")
    print(f"Building batch of {len(txs)} transactions:")
    print("=" * 60)
    for i, (label, to, cd) in enumerate(txs, 1):
        print(f"  [{i:2d}] {label}")
        print(f"       to:   {to}")
        print(f"       data: {cd[:74]}{'...' if len(cd) > 74 else ''}")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN] Not submitting. Re-run without --dry-run to send.")
        return

    # Submit batch via Builder Relayer
    from py_builder_relayer_client.client import RelayClient
    from py_builder_relayer_client.models import OperationType, SafeTransaction
    from py_builder_signing_sdk.config import BuilderConfig
    from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

    relay_client = RelayClient(
        relayer_url=RELAYER_URL,
        chain_id=137,
        private_key=private_key,
        builder_config=BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=builder_key, secret=builder_secret, passphrase=builder_pass,
            )
        ),
    )

    safe_txs = [
        SafeTransaction(
            to=to,
            operation=OperationType.Call,
            data=cd,
            value="0",
        )
        for (_, to, cd) in txs
    ]

    print(f"\nSubmitting {len(safe_txs)}-tx batch via relayer...")
    response = relay_client.execute(
        transactions=safe_txs,
        metadata="Polymarket V2 approves" + (f" + wrap {args.wrap_usdc} USDC.e" if wrap_usdc_units > 0 else ""),
    )
    print(f"transaction_id: {response.transaction_id}")
    print("Waiting for inclusion...")
    result = response.wait()

    if not result:
        print("FAILED: relayer reported no result", file=sys.stderr)
        sys.exit(1)

    tx_hash = result.get("transactionHash", "?")
    print(f"\nSUCCESS")
    print(f"  tx_hash: {tx_hash}")
    print(f"  explorer: https://polygonscan.com/tx/{tx_hash}")
    print()
    print("Verify on-chain (V2 allowances should be MAX_UINT256, pUSD bal > 0 if wrapped):")
    print(f"  SAFE={safe_address}")
    print(f"  USDCE={USDC_E_ADDRESS}")
    print(f"  PUSD={PUSD_ADDRESS}")
    print(f"  V2_EXCH={V2_EXCHANGE}")
    print(f"  RPC=https://polygon-bor-rpc.publicnode.com")
    print('  call() { curl -sS -X POST "$RPC" -H "Content-Type: application/json" \\')
    print('    -d "{\\"jsonrpc\\":\\"2.0\\",\\"id\\":1,\\"method\\":\\"eth_call\\",\\"params\\":[{\\"to\\":\\"$1\\",\\"data\\":\\"$2\\"},\\"latest\\"]}" \\')
    print('    | python3 -c "import json,sys; print(int(json.load(sys.stdin)[\\"result\\"], 16))"; }')
    print('  echo "USDC.e->V2_Exch: $(call $USDCE 0xdd62ed3e000000000000000000000000${SAFE:2}000000000000000000000000${V2_EXCH:2})"')
    print('  echo "pUSD bal:        $(call $PUSD 0x70a08231000000000000000000000000${SAFE:2})"')


if __name__ == "__main__":
    main()
