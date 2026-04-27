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
from typing import Dict, List, Optional, Set

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
# Collateral 주소: V1 = USDC.e, V2 = pUSD (2026-04-28 cutover).
# `redeemPositions(collateralToken, ...)` 의 collateralToken은 마켓 생성 시점의
# collateral과 일치해야 함. cutover 이전 마켓은 USDC.e, 이후 마켓은 pUSD.
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
# 기본값은 환경변수로 오버라이드 가능 (cutover 이후 새 마켓 redeem 시 PUSD로 변경).
# 더 정교하게는 마켓별로 collateral을 조회해야 하지만, 단일 collateral 가정 유지.
USDC_ADDRESS = os.environ.get("CLAIM_COLLATERAL_ADDRESS", USDC_E_ADDRESS)

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
# Position Detector
# =============================================================================


class PositionDetector:
    """Detects claimable positions using Polymarket Data API."""

    def __init__(self, proxy_address: str):
        self.proxy_address = proxy_address
        self.data_api_url = "https://data-api.polymarket.com"
        self.http_client = httpx.AsyncClient(timeout=30.0)
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

            claimable.append(
                ClaimablePosition(
                    condition_id=condition_id,
                    token_id=pos.get("tokenId", ""),
                    asset=pos.get("asset", ""),
                    quantity=quantity,
                    payout_amount=quantity,
                    redeemable=True,
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

        logger.info(
            "ClaimExecutor initialized (gasless via Builder Relayer with key rotation)"
        )

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

    def _build_redeem_calldata(self, condition_id: str) -> str:
        """Build calldata for redeemPositions function."""
        if not condition_id.startswith("0x"):
            condition_id = "0x" + condition_id

        condition_bytes = bytes.fromhex(condition_id[2:])

        calldata = self.ctf_contract.encode_abi(
            abi_element_identifier="redeemPositions",
            args=[
                self.web3.to_checksum_address(USDC_ADDRESS),
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

    async def claim(self, condition_id: str) -> ClaimResult:
        """Claim a winning position (GASLESS)."""
        if not condition_id.startswith("0x"):
            condition_id = "0x" + condition_id

        if len(condition_id) != 66:
            return ClaimResult(
                condition_id=condition_id,
                success=False,
                error=f"Invalid condition_id length: {len(condition_id)}",
            )

        calldata = self._build_redeem_calldata(condition_id)
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

    async def claim_batch(self, condition_ids: List[str]) -> BatchClaimResult:
        """Claim multiple winning positions in a single transaction (GASLESS)."""
        if not condition_ids:
            return BatchClaimResult(
                condition_ids=[], success=False, error="No condition_ids provided"
            )

        valid_condition_ids = []
        for cid in condition_ids:
            if not cid.startswith("0x"):
                cid = "0x" + cid
            if len(cid) == 66:
                valid_condition_ids.append(cid)
            else:
                logger.warning(
                    f"Skipping invalid condition_id: {cid[:20]}... (len={len(cid)})"
                )

        if not valid_condition_ids:
            return BatchClaimResult(
                condition_ids=condition_ids,
                success=False,
                error="No valid condition_ids after validation",
            )

        logger.info(
            f"Batch claiming {len(valid_condition_ids)} positions (gasless)..."
        )

        try:
            result = await asyncio.to_thread(
                self._execute_batch_via_relayer, valid_condition_ids
            )
            return BatchClaimResult(
                condition_ids=valid_condition_ids,
                success=result["success"],
                tx_hash=result.get("tx_hash"),
                transaction_id=result.get("transaction_id"),
                error=result.get("error"),
            )
        except Exception as e:
            logger.error(f"Batch claim error: {e}")
            return BatchClaimResult(
                condition_ids=valid_condition_ids,
                success=False,
                error=str(e),
            )

    def _execute_batch_via_relayer(self, condition_ids: List[str]) -> dict:
        """Execute batch transaction via Builder Relayer (GASLESS)."""
        from py_builder_relayer_client.models import SafeTransaction, OperationType

        creds = self.key_manager.get_current_credentials()
        if not creds:
            return {"success": False, "error": "No Builder API credentials available"}

        logger.info(
            f"Using Builder Key #{creds.index} for batch of {len(condition_ids)}"
        )

        transactions = []
        for condition_id in condition_ids:
            calldata = self._build_redeem_calldata(condition_id)
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
                    f"Batch claim SUCCESS: {len(condition_ids)} positions, "
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

        try:
            claimable = await self.detector.detect_claimable()

            if not claimable:
                logger.info(f"[{self.name}] No claimable positions")
                return results

            total_amount = sum(p.payout_amount for p in claimable)
            logger.info(
                f"[{self.name}] Found {len(claimable)} claimable, "
                f"total=${total_amount:.2f}"
            )

            # Balance before claim
            balance_before = self._get_usdc_balance()
            if balance_before is not None:
                logger.info(f"[{self.name}] USDC.e before claim: ${balance_before:.2f}")

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

            # Process in batches with fallback to smaller sizes
            pending_ids = list(position_map.keys())
            batch_size_idx = 0

            while pending_ids and batch_size_idx < len(BATCH_SIZES):
                batch_size = BATCH_SIZES[batch_size_idx]
                batch_ids = pending_ids[:batch_size]

                logger.info(
                    f"[{self.name}] Attempting batch claim: {len(batch_ids)} "
                    f"positions (batch_size={batch_size})"
                )

                batch_result = await self.executor.claim_batch(batch_ids)

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
                        f"[{self.name}] BATCH CLAIMED: {len(batch_ids)} positions, "
                        f"tx={batch_result.tx_hash}"
                    )

                    pending_ids = pending_ids[batch_size:]
                    self.consecutive_rate_limits = 0
                    batch_size_idx = 0

                    if pending_ids:
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
                            for cid in pending_ids:
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
                            f"marking {len(batch_ids)} as failed"
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
                        pending_ids = pending_ids[len(batch_ids) :]
                        batch_size_idx = 0

            claimed = sum(1 for r in results if r.success)
            failed = sum(1 for r in results if not r.success)

            # Balance after claim — verify USDC.e increased
            balance_after = None
            if claimed > 0 and balance_before is not None:
                await asyncio.sleep(2)  # brief wait for settlement
                balance_after = self._get_usdc_balance()
                if balance_after is not None:
                    diff = balance_after - balance_before
                    logger.info(
                        f"[{self.name}] USDC.e after claim: ${balance_after:.2f} "
                        f"(+${diff:.2f})"
                    )
                    if diff < total_amount * 0.5:
                        logger.warning(
                            f"[{self.name}] Balance increase ${diff:.2f} < "
                            f"expected ${total_amount:.2f} — verify on-chain"
                        )

            logger.info(
                f"[{self.name}] Cycle complete: {claimed} claimed, {failed} failed"
            )

            # Telegram notification on successful claims
            if claimed > 0:
                msg = f"CLAIM: {claimed} position(s) claimed"
                if balance_before is not None and balance_after is not None:
                    msg += (
                        f"\nUSDC.e: ${balance_before:.2f} → ${balance_after:.2f} "
                        f"(+${balance_after - balance_before:.2f})"
                    )
                await tg_send(msg)

        except Exception as e:
            logger.error(f"[{self.name}] Cycle error: {e}")

        return results

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
