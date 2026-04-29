"""
Polymarket Auto-Claim Service (Multi-Account)

Periodically detects and claims winning Polymarket positions (GASLESS).

Uses Polymarket's Builder Relayer for gasless claim transactions.

Supports multiple accounts via ACCOUNT_{N}_* environment variables.

Environment Variables (Builder API Keys - for rate limit rotation):
    - BUILDER_API_KEY_{N}: Polymarket Builder API key N (N=1,2,3,...)
    - BUILDER_SECRET_{N}: Polymarket Builder secret N
    - BUILDER_PASS_PHRASE_{N}: Polymarket Builder passphrase N
    Note: BUILDER_API_KEY_1 is used as the primary key for claim transactions.

Environment Variables (Per-Account):
    - ACCOUNT_{N}_PRIVATE_KEY: EOA private key for account N
    - ACCOUNT_{N}_PROXY_ADDRESS: Safe proxy wallet address for account N
    - ACCOUNT_{N}_NAME: Account name (optional, default: account_{N})

Common Settings:
    - CLAIM_INTERVAL: Seconds between claim cycles (default: 300)
    - PARALLEL_CLAIMS: If "true", claim all accounts in parallel (default: true)

Adapted from upstream/main-jd-trader:polyDB/services/auto_claimer/main.py
- Removed trader_common.vault dependency (uses .env directly)
"""

import os

# Load .env file
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

import asyncio
import logging
import re
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import httpx
from web3 import Web3

from py_clob_client_v2 import ClobClient, BalanceAllowanceParams, AssetType

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Telegram notifications
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.environ.get("TELEGRAM_THREAD_ID", "")


async def tg_send(text: str):
    """Send Telegram notification. Fire-and-forget."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        if TELEGRAM_THREAD_ID:
            payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload,
            )
    except Exception:
        pass


# =============================================================================
# Constants
# =============================================================================

# Polygon Mainnet Contract Addresses
# CTF (Conditional Tokens) 주소는 V1/V2 동일.
CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
# Collateral 주소: 2026-04-28 cutover로 pUSD 신규, USDC.e 기존.
# `redeemPositions(collateralToken, ...)` 의 collateralToken은 마켓 생성 시점의
# collateral과 일치해야 하므로, 마켓별로 on-chain CTF.getPositionId 매칭으로 자동 결정한다.
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
# Onramp.wrap(asset, recipient, amount) — converts USDC.e → pUSD on-chain.
# V2 markets use pUSD; redeemed USDC.e from V1-collateralized markets must be
# wrapped manually (otherwise Polymarket UI shows "Activate Funds").
COLLATERAL_ONRAMP_ADDRESS = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
COLLATERAL_CANDIDATES: List[Tuple[str, str]] = [
    (USDC_E_ADDRESS, "USDC.e"),
    (PUSD_ADDRESS, "pUSD"),
]
# Polygon RPC for on-chain collateral resolution (CTF view calls).
# polygon-rpc.com requires an API key now; publicnode is keyless.
POLYGON_RPC_URL = os.environ.get(
    "POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com"
)

# Relayer URL (v2 for gasless transactions)
RELAYER_URL = "https://relayer-v2.polymarket.com"

# Zero bytes32 for parentCollectionId
PARENT_COLLECTION_ID = bytes(32)

# Index sets for binary markets: YES=1, NO=2
BINARY_INDEX_SETS = [1, 2]

# Rate limit settings
CLAIM_DELAY_SECONDS = 3
RATE_LIMIT_BACKOFF_BASE = 30
RATE_LIMIT_BACKOFF_MAX = 300

# Batch claim settings
BATCH_SIZES = [10, 5, 2, 1]
BATCH_DELAY_SECONDS = 2

# Builder API Key rotation settings
MAX_BUILDER_KEYS = 20

# Conditional Tokens ABI (minimal for redeemPositions)
CONDITIONAL_TOKENS_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "type": "function",
    }
]

# Onramp.wrap(asset, recipient, amount) — converts USDC.e → pUSD.
ONRAMP_WRAP_ABI = [
    {
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "recipient", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "wrap",
        "outputs": [],
        "type": "function",
    }
]
ERC20_APPROVE_ABI = [
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"type": "bool"}],
        "type": "function",
    }
]
# ERC20 balanceOf selector for eth_call.
_ERC20_BALANCE_OF_SEL = "70a08231"
_MAX_UINT256 = (1 << 256) - 1


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class BuilderCredentials:
    """Builder API credentials."""

    api_key: str
    secret: str
    passphrase: str
    index: int = 0

    @property
    def is_valid(self) -> bool:
        return all([self.api_key, self.secret, self.passphrase])


class BuilderKeyManager:
    """Manages multiple Builder API keys with rotation on rate limit."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.credentials: List[BuilderCredentials] = []
        self.current_index: int = 0
        self.rate_limited_until: Dict[int, float] = {}
        self._load_credentials()

    def _load_credentials(self):
        """Load all builder credentials from environment."""
        for i in range(1, MAX_BUILDER_KEYS + 1):
            api_key = os.environ.get(f"BUILDER_API_KEY_{i}", "")
            secret = os.environ.get(f"BUILDER_SECRET_{i}", "")
            passphrase = os.environ.get(f"BUILDER_PASS_PHRASE_{i}", "")

            if api_key and secret and passphrase:
                cred = BuilderCredentials(
                    api_key=api_key,
                    secret=secret,
                    passphrase=passphrase,
                    index=i,
                )
                self.credentials.append(cred)
                logger.info(f"Loaded Builder API Key #{i}: {api_key[:12]}...")

        logger.info(
            f"BuilderKeyManager initialized with {len(self.credentials)} API keys"
        )

    def get_current_credentials(self) -> Optional[BuilderCredentials]:
        """Get currently active credentials, skipping rate-limited keys."""
        if not self.credentials:
            return None

        now = time.time()
        attempts = 0

        while attempts < len(self.credentials):
            cred = self.credentials[self.current_index]

            rate_limit_time = self.rate_limited_until.get(self.current_index, 0)
            if now >= rate_limit_time:
                return cred

            logger.info(
                f"Key #{cred.index} still rate limited for "
                f"{int(rate_limit_time - now)}s, trying next..."
            )
            self.current_index = (self.current_index + 1) % len(self.credentials)
            attempts += 1

        logger.warning("All Builder API keys are rate limited!")
        return self.credentials[self.current_index]

    def mark_rate_limited(self, duration_seconds: int = 60):
        """Mark current key as rate limited and rotate to next."""
        cred = self.credentials[self.current_index]
        self.rate_limited_until[self.current_index] = time.time() + duration_seconds
        logger.warning(
            f"Builder Key #{cred.index} rate limited for {duration_seconds}s"
        )
        self.current_index = (self.current_index + 1) % len(self.credentials)
        new_cred = self.credentials[self.current_index]
        logger.info(f"Rotated from Key #{cred.index} to Key #{new_cred.index}")

    def mark_success(self):
        """Clear rate limit for current key on success."""
        if self.current_index in self.rate_limited_until:
            del self.rate_limited_until[self.current_index]


_builder_key_manager: Optional[BuilderKeyManager] = None


def get_builder_key_manager() -> BuilderKeyManager:
    global _builder_key_manager
    if _builder_key_manager is None:
        _builder_key_manager = BuilderKeyManager()
    return _builder_key_manager


@dataclass
class ClaimablePosition:
    """A position that can be claimed."""

    condition_id: str
    token_id: str
    asset: str
    quantity: float
    payout_amount: float
    redeemable: bool
    collateral: str = ""  # CTF collateralToken — resolved per-market on-chain


@dataclass
class ClaimResult:
    """Result of a claim operation."""

    condition_id: str
    success: bool
    tx_hash: Optional[str] = None
    transaction_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class BatchClaimResult:
    """Result of a batch claim operation."""

    condition_ids: List[str]
    success: bool
    tx_hash: Optional[str] = None
    transaction_id: Optional[str] = None
    error: Optional[str] = None

    @property
    def count(self) -> int:
        return len(self.condition_ids)


# =============================================================================
# Collateral Resolver (on-chain CTF.getPositionId lookup)
# =============================================================================


# selector("getCollectionId(bytes32,bytes32,uint256)") = 0x856296f7
_GET_COLLECTION_ID_SEL = "856296f7"
# selector("getPositionId(address,bytes32)") = 0x39dd7530
_GET_POSITION_ID_SEL = "39dd7530"


class CollateralResolver:
    """Resolve a position's collateral by matching the CTF positionId on-chain.

    data-api returns `asset` (=CTF positionId). Each positionId is derived from
    (collateralToken, conditionId, indexSet). We brute-force match against known
    collateral candidates so the redeemPositions call uses the correct token —
    otherwise the CTF silently no-ops (no revert, no payout).
    """

    def __init__(self, http_client: httpx.AsyncClient, rpc_url: str = POLYGON_RPC_URL):
        self._client = http_client
        self._rpc_url = rpc_url
        self._cache: Dict[str, str] = {}  # conditionId(0x…) -> collateral address

    async def _eth_call(self, data_hex: str) -> str:
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": CONDITIONAL_TOKENS_ADDRESS, "data": data_hex}, "latest"],
            "id": 1,
        }
        r = await self._client.post(self._rpc_url, json=payload, timeout=15.0)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"eth_call error: {body['error']}")
        return body["result"]

    async def _get_position_id(
        self, collateral: str, condition_id_hex: str, index_set: int
    ) -> int:
        cond = condition_id_hex.replace("0x", "")
        zero32 = "00" * 32
        idx32 = index_set.to_bytes(32, "big").hex()
        coll_id_hex = await self._eth_call(
            "0x" + _GET_COLLECTION_ID_SEL + zero32 + cond + idx32
        )
        addr_padded = ("00" * 12) + collateral.lower().replace("0x", "")
        pid_hex = await self._eth_call(
            "0x" + _GET_POSITION_ID_SEL + addr_padded + coll_id_hex.replace("0x", "")
        )
        return int(pid_hex, 16)

    async def get_erc20_balance(self, token_address: str, holder: str) -> int:
        """Generic ERC20 balanceOf via the same eth_call infra. 0 on failure."""
        addr_padded = ("00" * 12) + holder.lower().replace("0x", "")
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {"to": token_address, "data": "0x" + _ERC20_BALANCE_OF_SEL + addr_padded},
                "latest",
            ],
            "id": 1,
        }
        try:
            r = await self._client.post(self._rpc_url, json=payload, timeout=15.0)
            r.raise_for_status()
            body = r.json()
            if "error" in body:
                logger.warning(f"balanceOf eth_call error: {body['error']}")
                return 0
            return int(body["result"], 16)
        except Exception as e:
            logger.warning(f"balanceOf eth_call failed: {e}")
            return 0

    async def detect(self, condition_id: str, asset_id: str) -> Optional[str]:
        """Return the matching collateral address, or None on no match / failure."""
        cid = condition_id if condition_id.startswith("0x") else "0x" + condition_id
        if cid in self._cache:
            return self._cache[cid]
        try:
            target = int(asset_id)
        except (TypeError, ValueError):
            return None
        for addr, _name in COLLATERAL_CANDIDATES:
            for index_set in BINARY_INDEX_SETS:
                try:
                    pid = await self._get_position_id(addr, cid, index_set)
                except Exception as e:
                    logger.warning(f"eth_call failed for {cid[:20]}...: {e}")
                    return None
                if pid == target:
                    self._cache[cid] = addr
                    return addr
        return None


# =============================================================================
# Position Detector
# =============================================================================


class PositionDetector:
    """Detects claimable positions using Polymarket Data API."""

    def __init__(self, proxy_address: str):
        self.proxy_address = proxy_address
        self.data_api_url = "https://data-api.polymarket.com"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.resolver = CollateralResolver(self.http_client)
        self.claimed_cache: Set[str] = set()

    async def get_positions(self) -> List[Dict]:
        """Fetch all positions from Data API."""
        url = f"{self.data_api_url}/positions"
        params = {"user": self.proxy_address}

        try:
            response = await self.http_client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []

    async def detect_claimable(self) -> List[ClaimablePosition]:
        """Detect positions that are redeemable."""
        positions = await self.get_positions()
        claimable = []

        for pos in positions:
            condition_id = pos.get("conditionId", "")
            if not condition_id:
                continue

            if condition_id in self.claimed_cache:
                continue

            redeemable = pos.get("redeemable", False)
            if not redeemable:
                continue

            quantity = float(pos.get("size", 0))
            if quantity <= 0:
                continue

            asset_id = pos.get("asset", "")
            collateral = await self.resolver.detect(condition_id, asset_id)
            if collateral is None:
                # NegRisk markets use NegRiskAdapter (different positionId derivation).
                # Skip rather than risk a silent no-op redeem with the wrong collateral.
                logger.warning(
                    f"Skipping {condition_id[:20]}... — collateral unresolved "
                    f"(negRisk={pos.get('negativeRisk')}, asset={asset_id[:24]}...)"
                )
                continue

            claimable.append(
                ClaimablePosition(
                    condition_id=condition_id,
                    token_id=pos.get("tokenId", ""),
                    asset=asset_id,
                    quantity=quantity,
                    payout_amount=quantity,
                    redeemable=True,
                    collateral=collateral,
                )
            )

        return claimable

    def mark_claimed(self, condition_id: str):
        """Mark a condition as claimed."""
        self.claimed_cache.add(condition_id)

    async def close(self):
        await self.http_client.aclose()


# =============================================================================
# Claim Executor (Gasless via Builder Relayer)
# =============================================================================


class ClaimExecutor:
    """Executor for claiming winning positions via Builder Relayer (GASLESS)."""

    def __init__(self, private_key: str):
        self.web3 = Web3()
        self.private_key = private_key
        self.key_manager = get_builder_key_manager()

        self.ctf_contract = self.web3.eth.contract(
            address=self.web3.to_checksum_address(CONDITIONAL_TOKENS_ADDRESS),
            abi=CONDITIONAL_TOKENS_ABI,
        )
        self.onramp_contract = self.web3.eth.contract(
            address=self.web3.to_checksum_address(COLLATERAL_ONRAMP_ADDRESS),
            abi=ONRAMP_WRAP_ABI,
        )
        self.usdc_e_contract = self.web3.eth.contract(
            address=self.web3.to_checksum_address(USDC_E_ADDRESS),
            abi=ERC20_APPROVE_ABI,
        )

        logger.info(
            "ClaimExecutor initialized (gasless via Builder Relayer with key rotation)"
        )

    def _build_wrap_calldata(self, recipient: str, amount_units: int) -> str:
        """Onramp.wrap(USDC.e, recipient, amount_units) — converts USDC.e → pUSD."""
        return self.onramp_contract.encode_abi(
            abi_element_identifier="wrap",
            args=[
                self.web3.to_checksum_address(USDC_E_ADDRESS),
                self.web3.to_checksum_address(recipient),
                amount_units,
            ],
        )

    async def wrap_usdc_to_pusd(self, recipient: str, amount_units: int) -> dict:
        """Wrap a specific amount of Safe USDC.e to pUSD via Builder Relayer."""
        calldata = self._build_wrap_calldata(recipient, amount_units)
        try:
            return await asyncio.to_thread(self._execute_wrap_via_relayer, calldata)
        except Exception as e:
            logger.error(f"wrap_usdc_to_pusd error: {e}")
            return {"success": False, "error": str(e)}

    def _execute_wrap_via_relayer(self, calldata: str) -> dict:
        from py_builder_relayer_client.models import SafeTransaction, OperationType

        creds = self.key_manager.get_current_credentials()
        if not creds:
            return {"success": False, "error": "No Builder API credentials available"}

        # Bundle USDC.e.approve(Onramp, MAX_UINT256) + Onramp.wrap(...) into one
        # Safe meta-tx. approve(MAX) is idempotent — safe to send every cycle —
        # and prevents revert when the cutover allowance was never set.
        approve_calldata = self.usdc_e_contract.encode_abi(
            abi_element_identifier="approve",
            args=[
                self.web3.to_checksum_address(COLLATERAL_ONRAMP_ADDRESS),
                _MAX_UINT256,
            ],
        )
        approve_tx = SafeTransaction(
            to=USDC_E_ADDRESS,
            operation=OperationType.Call,
            data=approve_calldata,
            value="0",
        )
        wrap_tx = SafeTransaction(
            to=COLLATERAL_ONRAMP_ADDRESS,
            operation=OperationType.Call,
            data=calldata,
            value="0",
        )
        try:
            relay_client = self._create_relay_client(creds)
            response = relay_client.execute(
                transactions=[approve_tx, wrap_tx],
                metadata="Approve+Wrap USDC.e -> pUSD",
            )
            result = response.wait()
            if result:
                self.key_manager.mark_success()
                return {
                    "success": True,
                    "tx_hash": result.get("transactionHash"),
                    "transaction_id": response.transaction_id,
                }
            return {"success": False, "error": "Wrap tx failed or timed out"}
        except Exception as e:
            error_str = str(e)
            logger.error(f"Wrap relayer execution error: {error_str}")
            if self._is_rate_limit_error(error_str):
                reset_time = self._extract_reset_time(error_str)
                self.key_manager.mark_rate_limited(reset_time)
            return {"success": False, "error": error_str}

    def _create_relay_client(self, creds: BuilderCredentials):
        """Create a new RelayClient with given credentials."""
        from py_builder_relayer_client.client import RelayClient
        from py_builder_signing_sdk.config import BuilderConfig
        from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

        builder_creds = BuilderApiKeyCreds(
            key=creds.api_key,
            secret=creds.secret,
            passphrase=creds.passphrase,
        )

        builder_config = BuilderConfig(local_builder_creds=builder_creds)

        return RelayClient(
            relayer_url=RELAYER_URL,
            chain_id=137,
            private_key=self.private_key,
            builder_config=builder_config,
        )

    def _build_redeem_calldata(self, condition_id: str, collateral_address: str) -> str:
        """Build calldata for redeemPositions function."""
        if not condition_id.startswith("0x"):
            condition_id = "0x" + condition_id

        condition_bytes = bytes.fromhex(condition_id[2:])

        calldata = self.ctf_contract.encode_abi(
            abi_element_identifier="redeemPositions",
            args=[
                self.web3.to_checksum_address(collateral_address),
                PARENT_COLLECTION_ID,
                condition_bytes,
                BINARY_INDEX_SETS,
            ],
        )

        return calldata

    def _is_rate_limit_error(self, error_msg: str) -> bool:
        """Check if error indicates rate limiting."""
        if not error_msg:
            return False
        error_lower = error_msg.lower()
        return (
            "429" in error_msg
            or "rate limit" in error_lower
            or "1015" in error_msg
            or "quota" in error_lower
        )

    def _extract_reset_time(self, error_msg: str) -> int:
        """Extract reset time from error message if present."""
        match = re.search(r"resets in (\d+) seconds", error_msg)
        if match:
            return int(match.group(1))
        return 300

    async def claim(self, condition_id: str, collateral_address: str) -> ClaimResult:
        """Claim a winning position (GASLESS)."""
        if not condition_id.startswith("0x"):
            condition_id = "0x" + condition_id

        if len(condition_id) != 66:
            return ClaimResult(
                condition_id=condition_id,
                success=False,
                error=f"Invalid condition_id length: {len(condition_id)}",
            )

        calldata = self._build_redeem_calldata(condition_id, collateral_address)
        logger.info(f"Claiming condition (gasless): {condition_id[:20]}...")

        try:
            result = await asyncio.to_thread(self._execute_via_relayer, calldata)
            return ClaimResult(
                condition_id=condition_id,
                success=result["success"],
                tx_hash=result.get("tx_hash"),
                transaction_id=result.get("transaction_id"),
                error=result.get("error"),
            )
        except Exception as e:
            logger.error(f"Claim error: {e}")
            return ClaimResult(
                condition_id=condition_id,
                success=False,
                error=str(e),
            )

    def _execute_via_relayer(self, calldata: str) -> dict:
        """Execute transaction via Builder Relayer (GASLESS) with key rotation."""
        from py_builder_relayer_client.models import SafeTransaction, OperationType

        creds = self.key_manager.get_current_credentials()
        if not creds:
            return {"success": False, "error": "No Builder API credentials available"}

        logger.info(f"Using Builder Key #{creds.index}")

        safe_tx = SafeTransaction(
            to=CONDITIONAL_TOKENS_ADDRESS,
            operation=OperationType.Call,
            data=calldata,
            value="0",
        )

        try:
            relay_client = self._create_relay_client(creds)

            response = relay_client.execute(
                transactions=[safe_tx],
                metadata="Redeem winning position",
            )

            result = response.wait()

            if result:
                self.key_manager.mark_success()
                return {
                    "success": True,
                    "tx_hash": result.get("transactionHash"),
                    "transaction_id": response.transaction_id,
                }
            else:
                return {"success": False, "error": "Transaction failed or timed out"}

        except Exception as e:
            error_str = str(e)
            logger.error(f"Relayer execution error: {error_str}")

            if self._is_rate_limit_error(error_str):
                reset_time = self._extract_reset_time(error_str)
                self.key_manager.mark_rate_limited(reset_time)

            return {"success": False, "error": error_str}

    async def claim_batch(self, items: List[Tuple[str, str]]) -> BatchClaimResult:
        """Claim multiple winning positions in a single transaction (GASLESS).

        Each item is (condition_id, collateral_address). Mixing collaterals in
        the same batch is fine — each redeemPositions call carries its own
        collateralToken parameter.
        """
        if not items:
            return BatchClaimResult(
                condition_ids=[], success=False, error="No items provided"
            )

        valid_items: List[Tuple[str, str]] = []
        for cid, collateral in items:
            if not cid.startswith("0x"):
                cid = "0x" + cid
            if len(cid) == 66:
                valid_items.append((cid, collateral))
            else:
                logger.warning(
                    f"Skipping invalid condition_id: {cid[:20]}... (len={len(cid)})"
                )

        if not valid_items:
            return BatchClaimResult(
                condition_ids=[c for c, _ in items],
                success=False,
                error="No valid condition_ids after validation",
            )

        valid_cids = [c for c, _ in valid_items]
        logger.info(f"Batch claiming {len(valid_items)} positions (gasless)...")

        try:
            result = await asyncio.to_thread(
                self._execute_batch_via_relayer, valid_items
            )
            return BatchClaimResult(
                condition_ids=valid_cids,
                success=result["success"],
                tx_hash=result.get("tx_hash"),
                transaction_id=result.get("transaction_id"),
                error=result.get("error"),
            )
        except Exception as e:
            logger.error(f"Batch claim error: {e}")
            return BatchClaimResult(
                condition_ids=valid_cids,
                success=False,
                error=str(e),
            )

    def _execute_batch_via_relayer(self, items: List[Tuple[str, str]]) -> dict:
        """Execute batch transaction via Builder Relayer (GASLESS)."""
        from py_builder_relayer_client.models import SafeTransaction, OperationType

        creds = self.key_manager.get_current_credentials()
        if not creds:
            return {"success": False, "error": "No Builder API credentials available"}

        logger.info(
            f"Using Builder Key #{creds.index} for batch of {len(items)}"
        )

        transactions = []
        for condition_id, collateral in items:
            calldata = self._build_redeem_calldata(condition_id, collateral)
            tx = SafeTransaction(
                to=CONDITIONAL_TOKENS_ADDRESS,
                operation=OperationType.Call,
                data=calldata,
                value="0",
            )
            transactions.append(tx)

        try:
            relay_client = self._create_relay_client(creds)

            response = relay_client.execute(
                transactions=transactions,
                metadata=f"Batch redeem {len(transactions)} positions",
            )

            result = response.wait()

            if result:
                self.key_manager.mark_success()
                logger.info(
                    f"Batch claim SUCCESS: {len(items)} positions, "
                    f"tx={result.get('transactionHash', 'N/A')[:20]}..."
                )
                return {
                    "success": True,
                    "tx_hash": result.get("transactionHash"),
                    "transaction_id": response.transaction_id,
                }
            else:
                return {
                    "success": False,
                    "error": "Batch transaction failed or timed out",
                }

        except Exception as e:
            error_str = str(e)
            logger.error(f"Batch relayer execution error: {error_str}")

            if self._is_rate_limit_error(error_str):
                reset_time = self._extract_reset_time(error_str)
                self.key_manager.mark_rate_limited(reset_time)

            return {"success": False, "error": error_str}


# =============================================================================
# Account Claimer
# =============================================================================


@dataclass
class ClaimAccountConfig:
    """Configuration for a single Polymarket account (for claiming)."""

    name: str
    private_key: str
    proxy_address: str
    builder_api_key: str
    builder_secret: str
    builder_pass_phrase: str

    @property
    def is_valid(self) -> bool:
        return all(
            [
                self.private_key,
                self.proxy_address,
                self.builder_api_key,
                self.builder_secret,
                self.builder_pass_phrase,
            ]
        )


@dataclass
class AccountStats:
    """Per-account statistics."""

    name: str
    cycles: int = 0
    total_claimed: int = 0
    total_failed: int = 0
    total_amount: float = 0.0


class AccountClaimer:
    """Handles claiming for a single account."""

    def __init__(self, config: ClaimAccountConfig, dry_run: bool = False):
        self.config = config
        self.name = config.name
        self.dry_run = dry_run

        self.detector = PositionDetector(config.proxy_address)
        self.executor = ClaimExecutor(private_key=config.private_key)

        self.stats = AccountStats(name=config.name)

        self.rate_limit_until: float = 0
        self.consecutive_rate_limits: int = 0

        # CLOB v2 client for balance verification
        # 2026-04-28 cutover로 같은 URL이 V2 backend로 교체됨. 4/28 이전 검증은 clob-v2 URL 사용.
        self._clob_client: Optional[ClobClient] = None
        try:
            host = os.environ.get("POLYMARKET_HOST", "https://clob.polymarket.com")
            client = ClobClient(
                host=host,
                key=config.private_key,
                chain_id=137,
                signature_type=2,
                funder=config.proxy_address,
            )
            creds = client.create_or_derive_api_key()
            client.set_api_creds(creds)
            self._clob_client = client
            logger.info(f"[{config.name}] CLOB v2 balance checker initialized")
        except Exception as e:
            logger.warning(f"[{config.name}] CLOB client init failed: {e}")

        logger.info(
            f"AccountClaimer initialized for {config.name}: "
            f"{config.proxy_address[:20]}..."
        )

    def _is_rate_limited(self, error_msg: str) -> bool:
        if not error_msg:
            return False
        error_lower = error_msg.lower()
        return "429" in error_msg or "rate limit" in error_lower or "1015" in error_msg

    def _handle_rate_limit(self):
        self.consecutive_rate_limits += 1
        backoff = min(
            RATE_LIMIT_BACKOFF_BASE * (2 ** (self.consecutive_rate_limits - 1)),
            RATE_LIMIT_BACKOFF_MAX,
        )
        self.rate_limit_until = time.time() + backoff
        logger.warning(
            f"[{self.name}] Rate limited! Backing off for {backoff}s "
            f"(consecutive: {self.consecutive_rate_limits})"
        )

    def _get_usdc_balance(self) -> Optional[float]:
        """Get collateral balance via CLOB v2 API. Returns None on failure.

        V2: COLLATERAL is pUSD (post-cutover). Decimals (6) unchanged from USDC.e.
        SDK auto-injects signature_type=2 from the builder so the server resolves
        Safe balance instead of EOA (otherwise always 0 for sig_type=2 wallets).
        """
        if not self._clob_client:
            return None
        try:
            result = self._clob_client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            raw = int(result.get("balance", 0))
            return raw / 1e6
        except Exception as e:
            logger.debug(f"[{self.name}] Balance query failed: {e}")
            return None

    async def run_claim_cycle(self) -> List[ClaimResult]:
        """Run a single claim cycle for this account using batch processing."""
        results = []
        self.stats.cycles += 1

        if time.time() < self.rate_limit_until:
            remaining = int(self.rate_limit_until - time.time())
            logger.info(
                f"[{self.name}] Still in rate limit backoff, {remaining}s remaining"
            )
            return results

        logger.info(f"[{self.name}] Starting claim detection...")

        claimable: List[ClaimablePosition] = []
        try:
            claimable = await self.detector.detect_claimable()
        except Exception as e:
            logger.error(f"[{self.name}] Detection error: {e}")

        if not claimable:
            logger.info(f"[{self.name}] No claimable positions")
        else:
            results.extend(await self._process_claimable(claimable))

        # Always wrap pending USDC.e regardless of claim outcome (skipped in dry-run).
        if not self.dry_run:
            await self._wrap_pending_usdc_e()

        return results

    async def _process_claimable(
        self, claimable: List[ClaimablePosition]
    ) -> List[ClaimResult]:
        """Run the batch claim loop for a list of claimable positions."""
        results: List[ClaimResult] = []
        try:
            total_amount = sum(p.payout_amount for p in claimable)
            logger.info(
                f"[{self.name}] Found {len(claimable)} claimable, "
                f"total=${total_amount:.2f}"
            )

            # Balance before claim
            balance_before = self._get_usdc_balance()
            if balance_before is not None:
                logger.info(f"[{self.name}] collateral before claim: ${balance_before:.2f}")

            if self.dry_run:
                for pos in claimable:
                    logger.info(
                        f"[{self.name}] Would claim: {pos.asset} "
                        f"(${pos.payout_amount:.2f})"
                    )
                return results

            # Build position map for lookups
            position_map: Dict[str, ClaimablePosition] = {}
            for pos in claimable:
                cid = (
                    pos.condition_id
                    if pos.condition_id.startswith("0x")
                    else "0x" + pos.condition_id
                )
                position_map[cid] = pos

            # Process in batches with fallback to smaller sizes.
            # pending items carry per-position collateral (auto-resolved).
            pending_items: List[Tuple[str, str]] = [
                (cid, pos.collateral) for cid, pos in position_map.items()
            ]
            batch_size_idx = 0

            while pending_items and batch_size_idx < len(BATCH_SIZES):
                batch_size = BATCH_SIZES[batch_size_idx]
                batch_items = pending_items[:batch_size]
                batch_ids = [c for c, _ in batch_items]

                logger.info(
                    f"[{self.name}] Attempting batch claim: {len(batch_items)} "
                    f"positions (batch_size={batch_size})"
                )

                batch_result = await self.executor.claim_batch(batch_items)

                if batch_result.success:
                    for cid in batch_ids:
                        pos = position_map.get(cid)
                        if pos:
                            self.stats.total_claimed += 1
                            self.stats.total_amount += pos.payout_amount
                            self.detector.mark_claimed(cid)
                            results.append(
                                ClaimResult(
                                    condition_id=cid,
                                    success=True,
                                    tx_hash=batch_result.tx_hash,
                                    transaction_id=batch_result.transaction_id,
                                )
                            )

                    logger.info(
                        f"[{self.name}] BATCH CLAIMED: {len(batch_items)} positions, "
                        f"tx={batch_result.tx_hash}"
                    )

                    pending_items = pending_items[batch_size:]
                    self.consecutive_rate_limits = 0
                    batch_size_idx = 0

                    if pending_items:
                        await asyncio.sleep(BATCH_DELAY_SECONDS)
                else:
                    error_msg = batch_result.error or ""
                    logger.warning(
                        f"[{self.name}] Batch failed (size={batch_size}): "
                        f"{error_msg[:100]}"
                    )

                    if self._is_rate_limited(error_msg):
                        key_mgr = self.executor.key_manager
                        available_creds = key_mgr.get_current_credentials()

                        if available_creds and time.time() >= key_mgr.rate_limited_until.get(
                            key_mgr.current_index, 0
                        ):
                            logger.info(
                                f"[{self.name}] Key rotated to "
                                f"#{available_creds.index}, retrying batch..."
                            )
                            await asyncio.sleep(1)
                            continue
                        else:
                            self._handle_rate_limit()
                            logger.info(
                                f"[{self.name}] All keys rate limited, stopping cycle"
                            )
                            for cid, _ in pending_items:
                                self.stats.total_failed += 1
                                results.append(
                                    ClaimResult(
                                        condition_id=cid,
                                        success=False,
                                        error="All keys rate limited",
                                    )
                                )
                            break

                    batch_size_idx += 1
                    if batch_size_idx < len(BATCH_SIZES):
                        next_size = BATCH_SIZES[batch_size_idx]
                        logger.info(
                            f"[{self.name}] Reducing batch size: "
                            f"{batch_size} -> {next_size}"
                        )
                    else:
                        logger.error(
                            f"[{self.name}] All batch sizes exhausted, "
                            f"marking {len(batch_items)} as failed"
                        )
                        for cid in batch_ids:
                            self.stats.total_failed += 1
                            results.append(
                                ClaimResult(
                                    condition_id=cid,
                                    success=False,
                                    error=error_msg,
                                )
                            )
                        pending_items = pending_items[len(batch_items):]
                        batch_size_idx = 0

            claimed = sum(1 for r in results if r.success)
            failed = sum(1 for r in results if not r.success)

            # Balance after claim — verify collateral increased.
            # NOTE: SDK-reported balance is V2 COLLATERAL (= pUSD post-cutover);
            # USDC.e-collateralized markets pay out USDC.e on-chain instead and
            # won't move the pUSD balance. So we sanity-check only against the
            # pUSD-collateralized portion of the batch.
            pusd_expected = sum(
                pos.payout_amount
                for pos in claimable
                if pos.collateral.lower() == PUSD_ADDRESS.lower()
            )
            balance_after = None
            if claimed > 0 and balance_before is not None:
                await asyncio.sleep(2)  # brief wait for settlement
                balance_after = self._get_usdc_balance()
                if balance_after is not None:
                    diff = balance_after - balance_before
                    logger.info(
                        f"[{self.name}] collateral after claim: ${balance_after:.2f} "
                        f"(+${diff:.2f}, pUSD-expected=${pusd_expected:.2f})"
                    )
                    if pusd_expected > 0 and diff < pusd_expected * 0.5:
                        logger.warning(
                            f"[{self.name}] pUSD balance increase ${diff:.2f} < "
                            f"expected ${pusd_expected:.2f} — verify on-chain"
                        )

            logger.info(
                f"[{self.name}] Cycle complete: {claimed} claimed, {failed} failed"
            )

            # Telegram notification on successful claims
            if claimed > 0:
                msg = f"CLAIM: {claimed} position(s) claimed"
                if balance_before is not None and balance_after is not None:
                    msg += (
                        f"\nCollateral: ${balance_before:.2f} → ${balance_after:.2f} "
                        f"(+${balance_after - balance_before:.2f})"
                    )
                await tg_send(msg)

        except Exception as e:
            logger.error(f"[{self.name}] Cycle error: {e}")

        return results

    async def _wrap_pending_usdc_e(self) -> None:
        """Wrap any USDC.e sitting in the Safe to pUSD via Onramp.wrap.

        V1-collateralized redeems pay out USDC.e on-chain; until wrapped, the
        Polymarket UI shows "Activate Funds" and the funds aren't tradable as
        V2 collateral. Always wrap the full balance to keep the Safe in pUSD.
        """
        try:
            usdc_e_raw = await self.detector.resolver.get_erc20_balance(
                USDC_E_ADDRESS, self.config.proxy_address
            )
        except Exception as e:
            logger.warning(f"[{self.name}] USDC.e balance check failed: {e}")
            return

        if usdc_e_raw <= 0:
            return

        usdc_e_human = usdc_e_raw / 1e6
        logger.info(
            f"[{self.name}] Wrapping ${usdc_e_human:.2f} USDC.e -> pUSD..."
        )
        wrap_result = await self.executor.wrap_usdc_to_pusd(
            self.config.proxy_address, usdc_e_raw
        )
        if wrap_result.get("success"):
            logger.info(
                f"[{self.name}] Wrap SUCCESS: ${usdc_e_human:.2f} -> pUSD, "
                f"tx={wrap_result.get('tx_hash')}"
            )
            await tg_send(f"WRAP: ${usdc_e_human:.2f} USDC.e → pUSD")
        else:
            logger.warning(
                f"[{self.name}] Wrap FAILED: {wrap_result.get('error')}"
            )

    async def close(self):
        await self.detector.close()


# =============================================================================
# Multi-Account Claim Service
# =============================================================================


class MultiAccountClaimService:
    """Auto-claim service supporting multiple Polymarket accounts."""

    def __init__(
        self,
        accounts: List[ClaimAccountConfig],
        claim_interval: int = 300,
        dry_run: bool = False,
        parallel: bool = True,
    ):
        self.claim_interval = claim_interval
        self.dry_run = dry_run
        self.parallel = parallel
        self._running = False

        self.claimers: List[AccountClaimer] = [
            AccountClaimer(config, dry_run=dry_run) for config in accounts
        ]

        self.total_cycles = 0

        logger.info(
            f"MultiAccountClaimService initialized with "
            f"{len(self.claimers)} accounts"
        )

    async def run(self):
        """Main service loop."""
        self._running = True
        logger.info("MultiAccountClaimService started")

        try:
            while self._running:
                try:
                    await self._run_claim_cycle()
                except Exception as e:
                    logger.error(f"Multi-account claim cycle error: {e}")

                await asyncio.sleep(self.claim_interval)
        finally:
            await self.stop()

    async def stop(self):
        self._running = False
        for claimer in self.claimers:
            await claimer.close()
        logger.info("MultiAccountClaimService stopped")

    async def _run_claim_cycle(self):
        self.total_cycles += 1
        logger.info(
            f"[Cycle {self.total_cycles}] Running claims for "
            f"{len(self.claimers)} accounts..."
        )

        if self.parallel:
            tasks = [claimer.run_claim_cycle() for claimer in self.claimers]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"[{self.claimers[i].name}] Exception: {result}")
        else:
            for claimer in self.claimers:
                try:
                    await claimer.run_claim_cycle()
                except Exception as e:
                    logger.error(f"[{claimer.name}] Error: {e}")

        self._log_aggregate_stats()

    def _log_aggregate_stats(self):
        total_claimed = sum(c.stats.total_claimed for c in self.claimers)
        total_failed = sum(c.stats.total_failed for c in self.claimers)
        total_amount = sum(c.stats.total_amount for c in self.claimers)

        logger.info(f"=== Aggregate Stats (Cycle {self.total_cycles}) ===")
        logger.info(
            f"Total Claimed: {total_claimed}, Failed: {total_failed}, "
            f"Amount: ${total_amount:.2f}"
        )

        for claimer in self.claimers:
            s = claimer.stats
            logger.info(
                f"  {s.name}: claimed={s.total_claimed}, "
                f"failed={s.total_failed}, amount=${s.total_amount:.2f}"
            )


def load_claim_account_configs(max_accounts: int = 20) -> List[ClaimAccountConfig]:
    """Load account configurations from environment variables."""
    accounts = []

    builder_api_key = os.environ.get("BUILDER_API_KEY_1", "")
    builder_secret = os.environ.get("BUILDER_SECRET_1", "")
    builder_pass_phrase = os.environ.get("BUILDER_PASS_PHRASE_1", "")

    if not all([builder_api_key, builder_secret, builder_pass_phrase]):
        logger.error(
            "BUILDER_API_KEY_1, BUILDER_SECRET_1, BUILDER_PASS_PHRASE_1 are required"
        )
        return accounts

    for i in range(1, max_accounts + 1):
        prefix = f"ACCOUNT_{i}_"

        private_key = os.environ.get(f"{prefix}PRIVATE_KEY", "")
        if not private_key:
            continue

        proxy_address = os.environ.get(f"{prefix}PROXY_ADDRESS", "")
        if not proxy_address:
            logger.warning(f"Account {i} missing PROXY_ADDRESS, skipping")
            continue

        config = ClaimAccountConfig(
            name=os.environ.get(f"{prefix}NAME", f"account_{i}"),
            private_key=private_key,
            proxy_address=proxy_address,
            builder_api_key=builder_api_key,
            builder_secret=builder_secret,
            builder_pass_phrase=builder_pass_phrase,
        )

        accounts.append(config)
        logger.info(f"Loaded claim account: {config.name}")

    return accounts


# =============================================================================
# Main
# =============================================================================


def main():
    """Main entry point."""
    accounts = load_claim_account_configs()

    if not accounts:
        logger.error(
            "No accounts configured. Set ACCOUNT_{N}_PRIVATE_KEY and "
            "ACCOUNT_{N}_PROXY_ADDRESS env vars."
        )
        return

    claim_interval = int(os.environ.get("CLAIM_INTERVAL", "300"))
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    parallel = os.environ.get("PARALLEL_CLAIMS", "true").lower() == "true"

    logger.info("=" * 60)
    if len(accounts) > 1:
        logger.info("POLYMARKET MULTI-ACCOUNT AUTO-CLAIM SERVICE")
    else:
        logger.info("POLYMARKET AUTO-CLAIM SERVICE")
    logger.info("=" * 60)
    logger.info(f"Accounts: {len(accounts)}")
    for acc in accounts:
        logger.info(f"  - {acc.name}: {acc.proxy_address[:20]}...")
    logger.info(f"Claim Interval: {claim_interval}s")
    logger.info(f"Parallel Claims: {parallel}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info("=" * 60)

    service = MultiAccountClaimService(
        accounts=accounts,
        claim_interval=claim_interval,
        dry_run=dry_run,
        parallel=parallel,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        service._running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        loop.run_until_complete(service.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
