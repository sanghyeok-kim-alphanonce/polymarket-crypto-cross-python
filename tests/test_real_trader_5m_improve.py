"""Standalone unit tests for the new logic in real_trader_5m_improve.

Covers:
  CrossingStrategy5MFront
    - max_count=3 produces 10/20/10 sizes
    - hedge_cutoff=270 enforces no entry past 270s (handled in process_crossing_event)
    - rollback_last_signal restores count + hedged
    - fire_pending_direct refuses when failed/hedged/maxed/past-cutoff
  RealTrader5MCrossFrontService._check_book_depth
    - depth_ok / ask_above_limit / depth_short / no_orderbook / no_best_ask / bad_best_ask
  PendingBookSignal dataclass
    - construction + override semantics

Run:
  python3 -m pytest tests/test_real_trader_5m_improve.py -v
or
  python3 tests/test_real_trader_5m_improve.py
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Make the service importable. main.py uses `from config import ...` so the
# service directory must be on sys.path BEFORE the package import.
SERVICE_DIR = Path(__file__).resolve().parents[1] / "services" / "real_trader_5m_improve"
PACKAGES_DIR = Path(__file__).resolve().parents[1] / "packages"
sys.path.insert(0, str(SERVICE_DIR))
sys.path.insert(0, str(PACKAGES_DIR))

# Avoid relying on real env values in tests
os.environ.setdefault("POLYMARKET_PRIVATE_KEY", "")
os.environ.setdefault("POLYMARKET_PROXY_ADDRESS", "")
os.environ.setdefault("SIGNALS_LOG_PATH", "")  # disable file IO in tests

import main as svc_main  # noqa: E402
from main import (  # noqa: E402
    CrossingStrategy5MFront,
    PendingBookSignal,
    RealTrader5MCrossFrontService,
)
import config as svc_config  # noqa: E402


# ---------------------------------------------------------------------------
# CrossingStrategy5MFront — pure logic tests (no I/O)
# ---------------------------------------------------------------------------
class TestStrategySizes(unittest.TestCase):
    """Verify max_count=3 produces 10/20/10 contract sizes with correct hedge labelling."""

    def setUp(self):
        self.s = CrossingStrategy5MFront()
        self.key = "btc_5m_2026-04-21T00:00:00+00:00"

    def test_first_entry_is_10_contracts_normal(self):
        sig = self.s.process_crossing_event(elapsed_seconds=10, crossing_direction="up", candle_key=self.key)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.contracts, svc_config.CROSSING_BET_CONTRACT_UNIT)  # 10
        self.assertIn("CROSS5M_UP", sig.reason)
        self.assertIn("#1", sig.reason)
        # State checks
        st = self.s._get_candle_state(self.key)
        self.assertEqual(st["count"], 1)
        self.assertFalse(st["hedged"])

    def test_second_entry_is_20_contracts_normal(self):
        # First fire, then start_cooltime so next call sees a different last_trade_direction
        self.s.process_crossing_event(elapsed_seconds=10, crossing_direction="up", candle_key=self.key)
        self.s.start_cooltime(self.key, "up")
        # Wait past cooltime artificially: bypass cooltime by directly calling _create_trade_signal
        # via process_crossing_event after manipulating timing — easier: clear in_cooltime.
        st = self.s._get_candle_state(self.key)
        st["in_cooltime"] = False
        sig = self.s.process_crossing_event(elapsed_seconds=20, crossing_direction="down", candle_key=self.key)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.contracts, svc_config.CROSSING_BET_CONTRACT_UNIT * 2)  # 20
        self.assertIn("CROSS5M_DOWN", sig.reason)
        self.assertIn("#2", sig.reason)
        self.assertEqual(self.s._get_candle_state(self.key)["count"], 2)

    def test_third_entry_is_hedge_max_10_contracts(self):
        # Manually advance state to count=2 (no cooltime)
        st = self.s._get_candle_state(self.key)
        st["count"] = 2
        st["last_trade_direction"] = "down"
        st["in_cooltime"] = False
        sig = self.s.process_crossing_event(elapsed_seconds=30, crossing_direction="up", candle_key=self.key)
        self.assertIsNotNone(sig)
        # 3rd entry must be HEDGE_MAX with 10 contracts
        self.assertEqual(sig.contracts, svc_config.CROSSING_BET_CONTRACT_UNIT)  # 10 (hedge)
        self.assertIn("HEDGE_MAX", sig.reason)
        self.assertIn("#3", sig.reason)
        st_after = self.s._get_candle_state(self.key)
        self.assertEqual(st_after["count"], 3)
        self.assertTrue(st_after["hedged"])

    def test_fourth_entry_blocked_by_hedged_flag(self):
        st = self.s._get_candle_state(self.key)
        st["count"] = 3
        st["hedged"] = True
        st["last_trade_direction"] = "up"
        st["in_cooltime"] = False
        sig = self.s.process_crossing_event(elapsed_seconds=40, crossing_direction="down", candle_key=self.key)
        self.assertIsNone(sig)


class TestHedgeCutoff(unittest.TestCase):
    """hedge_cutoff=280 bans entries past 280s; 250~279s fires as HEDGE_TIME."""

    def setUp(self):
        self.s = CrossingStrategy5MFront()
        self.key = "btc_5m_2026-04-21T00:00:00+00:00"

    def test_entry_at_270s_passes_as_hedge_time(self):
        # 270s is now within the hedge zone (250 <= elapsed < 280)
        st = self.s._get_candle_state(self.key)
        st["in_cooltime"] = False
        sig = self.s.process_crossing_event(elapsed_seconds=270, crossing_direction="up", candle_key=self.key)
        self.assertIsNotNone(sig)
        self.assertIn("HEDGE_TIME", sig.reason)
        self.assertEqual(sig.contracts, svc_config.CROSSING_BET_CONTRACT_UNIT)

    def test_entry_at_279s_passes_as_hedge_time(self):
        # last second of hedge window
        st = self.s._get_candle_state(self.key)
        st["in_cooltime"] = False
        sig = self.s.process_crossing_event(elapsed_seconds=279, crossing_direction="up", candle_key=self.key)
        self.assertIsNotNone(sig)
        self.assertIn("HEDGE_TIME", sig.reason)

    def test_entry_at_280s_blocked(self):
        # exactly at hedge_cutoff → blocked
        st = self.s._get_candle_state(self.key)
        st["in_cooltime"] = False
        sig = self.s.process_crossing_event(elapsed_seconds=280, crossing_direction="up", candle_key=self.key)
        self.assertIsNone(sig)

    def test_entry_at_295s_blocked(self):
        st = self.s._get_candle_state(self.key)
        st["in_cooltime"] = False
        sig = self.s.process_crossing_event(elapsed_seconds=295, crossing_direction="up", candle_key=self.key)
        self.assertIsNone(sig)


class TestRollback(unittest.TestCase):
    """rollback_last_signal restores count + hedged correctly."""

    def setUp(self):
        self.s = CrossingStrategy5MFront()
        self.key = "btc_5m_2026-04-21T00:00:00+00:00"

    def test_rollback_normal_signal_decrements_count(self):
        sig = self.s.process_crossing_event(elapsed_seconds=10, crossing_direction="up", candle_key=self.key)
        self.assertIsNotNone(sig)
        self.assertEqual(self.s._get_candle_state(self.key)["count"], 1)
        was_hedge = "HEDGE" in sig.reason
        self.assertFalse(was_hedge)
        self.s.rollback_last_signal(self.key, was_hedge)
        st = self.s._get_candle_state(self.key)
        self.assertEqual(st["count"], 0)
        self.assertFalse(st["hedged"])

    def test_rollback_hedge_signal_restores_hedged_false(self):
        st = self.s._get_candle_state(self.key)
        st["count"] = 2
        st["last_trade_direction"] = "down"
        st["in_cooltime"] = False
        sig = self.s.process_crossing_event(elapsed_seconds=30, crossing_direction="up", candle_key=self.key)
        self.assertIn("HEDGE_MAX", sig.reason)
        self.assertEqual(st["count"], 3)
        self.assertTrue(st["hedged"])
        self.s.rollback_last_signal(self.key, was_hedge=True)
        self.assertEqual(st["count"], 2)
        self.assertFalse(st["hedged"])

    def test_rollback_does_not_underflow(self):
        st = self.s._get_candle_state(self.key)
        st["count"] = 0
        self.s.rollback_last_signal(self.key, was_hedge=False)
        self.assertEqual(st["count"], 0)  # not negative


class TestFirePendingDirect(unittest.TestCase):
    """fire_pending_direct enforces safety guards independent of cooltime / parity."""

    def setUp(self):
        self.s = CrossingStrategy5MFront()
        self.key = "btc_5m_2026-04-21T00:00:00+00:00"

    def test_fires_normally_when_state_clean(self):
        sig = self.s.fire_pending_direct(direction="up", elapsed_seconds=15, candle_key=self.key)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.contracts, svc_config.CROSSING_BET_CONTRACT_UNIT)  # 1st entry
        self.assertEqual(self.s._get_candle_state(self.key)["count"], 1)

    def test_refuses_when_failed(self):
        st = self.s._get_candle_state(self.key)
        st["failed"] = True
        sig = self.s.fire_pending_direct(direction="up", elapsed_seconds=15, candle_key=self.key)
        self.assertIsNone(sig)

    def test_refuses_when_hedged(self):
        st = self.s._get_candle_state(self.key)
        st["hedged"] = True
        sig = self.s.fire_pending_direct(direction="up", elapsed_seconds=15, candle_key=self.key)
        self.assertIsNone(sig)

    def test_refuses_when_maxed(self):
        st = self.s._get_candle_state(self.key)
        st["count"] = svc_config.CROSSING_MAX_COUNT  # 3
        sig = self.s.fire_pending_direct(direction="up", elapsed_seconds=15, candle_key=self.key)
        self.assertIsNone(sig)

    def test_refuses_past_hedge_cutoff(self):
        sig = self.s.fire_pending_direct(direction="up",
                                         elapsed_seconds=svc_config.CROSSING_HEDGE_CUTOFF_SECONDS,
                                         candle_key=self.key)
        self.assertIsNone(sig)

    def test_fires_at_hedge_window_with_hedge_time_reason(self):
        # 250s ~ 269s → HEDGE_TIME, 10 contracts
        sig = self.s.fire_pending_direct(direction="down", elapsed_seconds=255, candle_key=self.key)
        self.assertIsNotNone(sig)
        self.assertIn("HEDGE_TIME", sig.reason)
        self.assertEqual(sig.contracts, svc_config.CROSSING_BET_CONTRACT_UNIT)


# ---------------------------------------------------------------------------
# RealTrader5MCrossFrontService._check_book_depth — staticmethod
# ---------------------------------------------------------------------------
class TestCheckBookDepth(unittest.TestCase):
    """Top-of-book guard: passes only when ask <= gtc AND ask_size * SAFETY >= intended."""

    def test_pass_when_depth_ample(self):
        ob = {"best_ask": 0.55, "best_ask_size": 100.0}
        chk = RealTrader5MCrossFrontService._check_book_depth(ob, gtc_price=0.80, intended_contracts=10)
        self.assertTrue(chk["passed"])
        self.assertEqual(chk["reason"], "depth_ok")
        self.assertEqual(chk["best_ask"], 0.55)
        self.assertAlmostEqual(chk["required"], 10 / svc_config.BOOK_DEPTH_SAFETY)

    def test_fail_when_ask_above_limit(self):
        ob = {"best_ask": 0.85, "best_ask_size": 1000.0}
        chk = RealTrader5MCrossFrontService._check_book_depth(ob, gtc_price=0.80, intended_contracts=10)
        self.assertFalse(chk["passed"])
        self.assertEqual(chk["reason"], "ask_above_limit")
        self.assertEqual(chk["best_ask"], 0.85)
        self.assertEqual(chk["available"], 0)

    def test_fail_when_depth_short(self):
        # intended=20, safety=0.7 → required ≈ 28.6. ask_size=20 < 28.6 → fail.
        ob = {"best_ask": 0.50, "best_ask_size": 20.0}
        chk = RealTrader5MCrossFrontService._check_book_depth(ob, gtc_price=0.80, intended_contracts=20)
        self.assertFalse(chk["passed"])
        self.assertEqual(chk["reason"], "depth_short")
        self.assertEqual(chk["best_ask"], 0.50)
        self.assertEqual(chk["best_ask_size"], 20.0)

    def test_pass_at_exact_required_threshold(self):
        # Edge: ask_size exactly equals required.
        required = 10 / svc_config.BOOK_DEPTH_SAFETY  # ≈ 14.286
        ob = {"best_ask": 0.50, "best_ask_size": required}
        chk = RealTrader5MCrossFrontService._check_book_depth(ob, gtc_price=0.80, intended_contracts=10)
        self.assertTrue(chk["passed"])

    def test_fail_when_no_orderbook(self):
        chk = RealTrader5MCrossFrontService._check_book_depth(None, gtc_price=0.80, intended_contracts=10)
        self.assertFalse(chk["passed"])
        self.assertEqual(chk["reason"], "no_orderbook")

    def test_fail_when_no_best_ask(self):
        ob = {"best_ask": None, "best_ask_size": 100.0}
        chk = RealTrader5MCrossFrontService._check_book_depth(ob, gtc_price=0.80, intended_contracts=10)
        self.assertFalse(chk["passed"])
        self.assertEqual(chk["reason"], "no_best_ask")

    def test_fail_when_best_ask_unparseable(self):
        ob = {"best_ask": "abc", "best_ask_size": 100.0}
        chk = RealTrader5MCrossFrontService._check_book_depth(ob, gtc_price=0.80, intended_contracts=10)
        self.assertFalse(chk["passed"])
        self.assertEqual(chk["reason"], "bad_best_ask")

    def test_safety_zero_falls_back_to_one(self):
        with patch.object(svc_main, "BOOK_DEPTH_SAFETY", 0.0):
            ob = {"best_ask": 0.50, "best_ask_size": 10.0}
            chk = RealTrader5MCrossFrontService._check_book_depth(ob, gtc_price=0.80, intended_contracts=10)
            # safety=0 → fallback safety=1 → required=10, available=10 → pass
            self.assertTrue(chk["passed"])


# ---------------------------------------------------------------------------
# PendingBookSignal dataclass
# ---------------------------------------------------------------------------
class TestPendingBookSignal(unittest.TestCase):
    def test_construction(self):
        p = PendingBookSignal(
            direction="up", side="up",
            intended_contracts=10, gtc_price=0.80,
            armed_at_elapsed_s=42, armed_at_ts=1234567.0,
            crossing_info={"foo": "bar"},
        )
        self.assertEqual(p.direction, "up")
        self.assertEqual(p.side, "up")
        self.assertEqual(p.intended_contracts, 10)
        self.assertEqual(p.crossing_info["foo"], "bar")


# ---------------------------------------------------------------------------
# Config sanity — the actual deployed values
# ---------------------------------------------------------------------------
class TestConfigSanity(unittest.TestCase):
    def test_max_count_is_3(self):
        self.assertEqual(svc_config.CROSSING_MAX_COUNT, 3)

    def test_hedge_cutoff_is_280(self):
        self.assertEqual(svc_config.CROSSING_HEDGE_CUTOFF_SECONDS, 280)

    def test_entry_cutoff_unchanged_250(self):
        self.assertEqual(svc_config.CROSSING_ENTRY_CUTOFF_SECONDS, 250)

    def test_book_depth_required_default_true(self):
        self.assertTrue(svc_config.BOOK_DEPTH_REQUIRED)

    def test_book_depth_safety_in_sane_range(self):
        self.assertGreater(svc_config.BOOK_DEPTH_SAFETY, 0)
        self.assertLessEqual(svc_config.BOOK_DEPTH_SAFETY, 1.0)


# ---------------------------------------------------------------------------
# Integration tests — _handle_crossing_event + _try_book_recovery flows
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402


def _make_trader_with_orderbook(best_ask: float, best_ask_size: float) -> RealTrader5MCrossFrontService:
    """Construct a trader with a pre-populated orderbook cache (no DB/Redis I/O)."""
    t = RealTrader5MCrossFrontService()
    t.ob_cache["btc_5m_up"] = {
        "best_bid": 0.45,
        "best_ask": best_ask,
        "best_bid_size": 100.0,
        "best_ask_size": best_ask_size,
        "mid_price": 0.50,
        "market_slug": "btc-5m",
        "token_id": "TOKEN_UP_X",
        "timestamp": time.time(),
    }
    t.ob_cache["btc_5m_down"] = {
        "best_bid": 0.45,
        "best_ask": best_ask,
        "best_bid_size": 100.0,
        "best_ask_size": best_ask_size,
        "mid_price": 0.50,
        "market_slug": "btc-5m",
        "token_id": "TOKEN_DOWN_X",
        "timestamp": time.time(),
    }
    # Patch slug check to always match
    return t


def _crossing_data(direction: str, candle_start: datetime, elapsed_seconds: int) -> Dict:
    return {
        "coin": "btc",
        "timeframe": "5m",
        "direction": direction,
        "candle_start": candle_start.isoformat().replace("+00:00", "Z"),
        "candle_end": (candle_start + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "elapsed_ms": elapsed_seconds * 1000,
        "candle_open": 70000.0,
        "prev_price": 69999.0,
        "current_price": 70001.0,
    }


class TestHandleCrossingBookGuard(unittest.IsolatedAsyncioTestCase):
    """Integration: _handle_crossing_event interaction with book guard + pending."""

    def setUp(self):
        # Patch generate_slug to return matching slug for the test orderbook
        self._slug_patch = patch("main.generate_slug", return_value="btc-5m")
        self._slug_patch.start()
        # No-op execute_order to avoid CLOB calls
        self._exec_patch = patch.object(RealTrader5MCrossFrontService, "execute_order")
        self._exec_mock = self._exec_patch.start()
        async def _noop(*a, **kw): return
        self._exec_mock.side_effect = _noop
        # No-op telegram queue
        self._tg_patch = patch.object(RealTrader5MCrossFrontService, "notify", lambda self, msg: None)
        self._tg_patch.start()

    def tearDown(self):
        self._slug_patch.stop()
        self._exec_patch.stop()
        self._tg_patch.stop()

    async def test_book_short_arms_pending_no_execute(self):
        # ask=0.50 OK price, but only 10 size and intended=10 → required ≈ 14.3 → fail
        t = _make_trader_with_orderbook(best_ask=0.50, best_ask_size=10.0)
        # candle_start must be in the current real-time window (now ≤ candle_end)
        candle_start = datetime.now(timezone.utc) - timedelta(seconds=15)
        await t._handle_crossing_event(_crossing_data("up", candle_start, elapsed_seconds=15))
        # Pending should be armed
        candle_key = f"btc_5m_{candle_start.isoformat()}"
        st = t.strategies["btc"]._get_candle_state(candle_key)
        self.assertIsNotNone(st["pending_book_signal"])
        self.assertEqual(st["pending_book_signal"].direction, "up")
        self.assertEqual(st["pending_book_signal"].intended_contracts, svc_config.CROSSING_BET_CONTRACT_UNIT)
        # State should be rolled back (count back to 0, hedged False)
        self.assertEqual(st["count"], 0)
        self.assertFalse(st["hedged"])
        # execute_order NOT called
        self._exec_mock.assert_not_called()
        # stats incremented
        self.assertEqual(t.stats["book_depth_skips"], 1)
        self.assertEqual(t.stats["book_depth_passes"], 0)

    async def test_book_ample_executes_normally(self):
        t = _make_trader_with_orderbook(best_ask=0.50, best_ask_size=200.0)
        candle_start = datetime.now(timezone.utc) - timedelta(seconds=15)
        await t._handle_crossing_event(_crossing_data("up", candle_start, elapsed_seconds=15))
        candle_key = f"btc_5m_{candle_start.isoformat()}"
        st = t.strategies["btc"]._get_candle_state(candle_key)
        # No pending; signal fired
        self.assertIsNone(st["pending_book_signal"])
        self.assertEqual(st["count"], 1)
        self._exec_mock.assert_called_once()
        self.assertEqual(t.stats["book_depth_passes"], 1)
        self.assertEqual(t.stats["book_depth_skips"], 0)

    async def test_opposite_crossing_overrides_pending(self):
        t = _make_trader_with_orderbook(best_ask=0.50, best_ask_size=10.0)
        candle_start = datetime.now(timezone.utc) - timedelta(seconds=20)
        # 1st: arms pending UP (book short)
        await t._handle_crossing_event(_crossing_data("up", candle_start, elapsed_seconds=10))
        candle_key = f"btc_5m_{candle_start.isoformat()}"
        st = t.strategies["btc"]._get_candle_state(candle_key)
        self.assertIsNotNone(st["pending_book_signal"])
        self.assertEqual(st["pending_book_signal"].direction, "up")
        # 2nd: opposite DOWN crossing → should clear UP pending and arm DOWN (still book short)
        await t._handle_crossing_event(_crossing_data("down", candle_start, elapsed_seconds=20))
        st_after = t.strategies["btc"]._get_candle_state(candle_key)
        self.assertIsNotNone(st_after["pending_book_signal"])
        self.assertEqual(st_after["pending_book_signal"].direction, "down")

    async def test_same_direction_dup_ignored_when_pending(self):
        t = _make_trader_with_orderbook(best_ask=0.50, best_ask_size=10.0)
        candle_start = datetime.now(timezone.utc) - timedelta(seconds=25)
        await t._handle_crossing_event(_crossing_data("up", candle_start, elapsed_seconds=10))
        await t._handle_crossing_event(_crossing_data("up", candle_start, elapsed_seconds=20))
        candle_key = f"btc_5m_{candle_start.isoformat()}"
        st = t.strategies["btc"]._get_candle_state(candle_key)
        # Pending still UP, armed at original elapsed (10), not 20
        self.assertIsNotNone(st["pending_book_signal"])
        self.assertEqual(st["pending_book_signal"].armed_at_elapsed_s, 10)
        # No execute
        self._exec_mock.assert_not_called()
        # Both crossings recorded for stats
        self.assertEqual(len(st["crossing_times"]), 2)


class TestBookRecovery(unittest.IsolatedAsyncioTestCase):
    """Integration: _try_book_recovery fires pending when book returns AND clears properly."""

    def setUp(self):
        self._slug_patch = patch("main.generate_slug", return_value="btc-5m")
        self._slug_patch.start()
        self._exec_patch = patch.object(RealTrader5MCrossFrontService, "execute_order")
        self._exec_mock = self._exec_patch.start()
        async def _noop(*a, **kw): return
        self._exec_mock.side_effect = _noop
        self._tg_patch = patch.object(RealTrader5MCrossFrontService, "notify", lambda self, msg: None)
        self._tg_patch.start()

    def tearDown(self):
        self._slug_patch.stop()
        self._exec_patch.stop()
        self._tg_patch.stop()

    async def _arm(self, t: RealTrader5MCrossFrontService, candle_start: datetime, elapsed: int) -> str:
        """Helper: trigger a book-short crossing to arm pending."""
        await t._handle_crossing_event(_crossing_data("up", candle_start, elapsed_seconds=elapsed))
        return f"btc_5m_{candle_start.isoformat()}"

    async def test_recovery_fires_when_book_recovers(self):
        # candle_start = "now − 15s" so elapsed_now ≈ 15s when we recover
        candle_start = datetime.now(timezone.utc) - timedelta(seconds=15)
        # Initially book short; arm pending
        t = _make_trader_with_orderbook(best_ask=0.50, best_ask_size=10.0)
        candle_key = await self._arm(t, candle_start, elapsed=5)
        st = t.strategies["btc"]._get_candle_state(candle_key)
        self.assertIsNotNone(st["pending_book_signal"])
        # Now book recovers → simulate orderbook message that updates cache
        t.ob_cache["btc_5m_up"]["best_ask_size"] = 200.0
        t.ob_cache["btc_5m_up"]["timestamp"] = time.time()
        await t._try_book_recovery("btc", "5m", "up", "TOKEN_UP_X")
        # Pending cleared, execute called
        self.assertIsNone(st["pending_book_signal"])
        self._exec_mock.assert_called_once()
        self.assertEqual(t.stats["book_depth_recoveries"], 1)

    async def test_recovery_skipped_when_book_still_short(self):
        candle_start = datetime.now(timezone.utc) - timedelta(seconds=15)
        t = _make_trader_with_orderbook(best_ask=0.50, best_ask_size=10.0)
        candle_key = await self._arm(t, candle_start, elapsed=5)
        # Book still short
        await t._try_book_recovery("btc", "5m", "up", "TOKEN_UP_X")
        st = t.strategies["btc"]._get_candle_state(candle_key)
        self.assertIsNotNone(st["pending_book_signal"])  # still armed
        self._exec_mock.assert_not_called()
        self.assertEqual(t.stats["book_depth_recoveries"], 0)

    async def test_recovery_expires_past_hedge_cutoff(self):
        # candle_start "long ago" so elapsed_now > hedge_cutoff
        candle_start = datetime.now(timezone.utc) - timedelta(seconds=svc_config.CROSSING_HEDGE_CUTOFF_SECONDS + 5)
        t = _make_trader_with_orderbook(best_ask=0.50, best_ask_size=10.0)
        candle_key = await self._arm(t, candle_start, elapsed=5)
        # However, _arm_at the past elapsed wouldn't pass the cutoff check inside process_crossing_event
        # since candle_open_ts is computed from candle_start. So the original arming may not happen.
        st = t.strategies["btc"]._get_candle_state(candle_key)
        # Either pending is None (never armed because past cutoff) or expires on recovery.
        if st["pending_book_signal"] is not None:
            t.ob_cache["btc_5m_up"]["best_ask_size"] = 200.0
            await t._try_book_recovery("btc", "5m", "up", "TOKEN_UP_X")
            self.assertIsNone(st["pending_book_signal"])

    async def test_cooltime_timer_book_short_arms_pending_no_execute(self):
        """cooltime_timer 경로도 동일하게 책 가드 적용되어야 함 — 'always before order'."""
        # Set up: a candle that has had a successful 1st fire and is now in cooltime,
        # with a cooltime-pending DOWN. Then cooltime_timer fires DOWN but book is short.
        candle_start = datetime.now(timezone.utc) - timedelta(seconds=20)
        candle_end = candle_start + timedelta(minutes=5)
        t = _make_trader_with_orderbook(best_ask=0.50, best_ask_size=10.0)  # short book
        # patch COOLTIME so timer doesn't actually wait 1s
        with patch.object(svc_main, "COOLTIME_SECONDS", 0.0):
            strategy = t.strategies["btc"]
            candle_key = f"btc_5m_{candle_start.isoformat()}"
            # Manually set up state as if 1st UP fire happened, cooltime active, DOWN pending.
            st = strategy._get_candle_state(candle_key)
            st["count"] = 1
            st["last_trade_direction"] = "up"
            st["in_cooltime"] = True
            st["cooltime_start"] = time.time()
            st["pending_direction"] = "down"
            st["pending_elapsed"] = 18
            await t._cooltime_timer("btc", "5m", candle_key, candle_start, candle_end)
        # Expect: cooltime_timer produced a signal via check_pending_after_cooltime, then
        # book guard rolled it back and armed pending_book_signal for DOWN.
        st = t.strategies["btc"]._get_candle_state(candle_key)
        self.assertIsNotNone(st["pending_book_signal"])
        self.assertEqual(st["pending_book_signal"].direction, "down")
        # state was rolled back (count back to 1)
        self.assertEqual(st["count"], 1)
        self._exec_mock.assert_not_called()
        self.assertEqual(t.stats["book_depth_skips"], 1)

    async def test_cooltime_timer_book_ample_executes(self):
        candle_start = datetime.now(timezone.utc) - timedelta(seconds=20)
        candle_end = candle_start + timedelta(minutes=5)
        t = _make_trader_with_orderbook(best_ask=0.50, best_ask_size=200.0)
        with patch.object(svc_main, "COOLTIME_SECONDS", 0.0):
            strategy = t.strategies["btc"]
            candle_key = f"btc_5m_{candle_start.isoformat()}"
            st = strategy._get_candle_state(candle_key)
            st["count"] = 1
            st["last_trade_direction"] = "up"
            st["in_cooltime"] = True
            st["cooltime_start"] = time.time()
            st["pending_direction"] = "down"
            st["pending_elapsed"] = 18
            await t._cooltime_timer("btc", "5m", candle_key, candle_start, candle_end)
        st = t.strategies["btc"]._get_candle_state(candle_key)
        self.assertIsNone(st["pending_book_signal"])  # not armed
        self._exec_mock.assert_called_once()
        self.assertEqual(t.stats["book_depth_passes"], 1)

    async def test_recovery_drops_when_strategy_hedged(self):
        candle_start = datetime.now(timezone.utc) - timedelta(seconds=15)
        t = _make_trader_with_orderbook(best_ask=0.50, best_ask_size=10.0)
        candle_key = await self._arm(t, candle_start, elapsed=5)
        st = t.strategies["btc"]._get_candle_state(candle_key)
        self.assertIsNotNone(st["pending_book_signal"])
        # Manually set hedged True (simulate other entry path that hedged the candle)
        st["hedged"] = True
        # Book recovers
        t.ob_cache["btc_5m_up"]["best_ask_size"] = 200.0
        await t._try_book_recovery("btc", "5m", "up", "TOKEN_UP_X")
        # Pending dropped, no execute
        self.assertIsNone(st["pending_book_signal"])
        self._exec_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
