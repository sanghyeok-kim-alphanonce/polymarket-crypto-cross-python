"""
Backtest API Service

FastAPI 서버. paper_trader의 strategies를 직접 사용하여
DB 오더북 데이터 기반 백테스트 실행.
"""
import logging
import sys
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Query, HTTPException

from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
from runner import (
    load_orderbook_data,
    load_binance_prices,
    replay_orderbook,
    run_candle_backtest,
    run_crossing_backtest,
    get_market_result,
    get_paper_trades,
    get_candle_list,
)

VALID_STRATEGIES = ["v12_5", "v12_6", "crossing", "crossing_v2"]

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

db_pool: asyncpg.Pool = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    logger.info("Backtest API starting...")
    for i in range(10):
        try:
            db_pool = await asyncpg.create_pool(
                host=DB_HOST, port=DB_PORT,
                database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
                min_size=2, max_size=5,
            )
            logger.info(f"DB pool created: {DB_HOST}:{DB_PORT}/{DB_NAME}")
            break
        except Exception as e:
            logger.error(f"DB connection attempt {i+1}/10 failed: {e}")
            if i < 9:
                import asyncio
                await asyncio.sleep(3)
    else:
        raise RuntimeError("Failed to connect to DB")

    yield

    if db_pool:
        await db_pool.close()
        logger.info("DB pool closed")


app = FastAPI(title="Backtest API", lifespan=lifespan)


@app.get("/candles")
async def candles_endpoint(
    coin: str = Query(default="btc"),
    limit: int = Query(default=20, le=100),
    strategy: str = Query(default="v12_5"),
):
    """백테스트 가능한 캔들 목록"""
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(400, f"Invalid strategy: {strategy}. Valid: {VALID_STRATEGIES}")

    async with db_pool.acquire() as conn:
        result = await get_candle_list(conn, coin, limit, strategy_name=strategy)
    return {"coin": coin, "strategy": strategy, "candles": result}


@app.get("/run")
async def run_endpoint(
    coin: str = Query(default="btc"),
    market_slug: str = Query(...),
    strategy: str = Query(default="v12_5"),
):
    """단일 캔들 백테스트 실행"""
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(400, f"Invalid strategy: {strategy}. Valid: {VALID_STRATEGIES}")

    # slug에서 캔들 시간 추출: btc-updown-15m-{unix_ts}
    parts = market_slug.split("-")
    try:
        slug_ts = int(parts[-1])
    except (ValueError, IndexError):
        raise HTTPException(400, "Invalid market_slug format")

    candle_start = datetime.fromtimestamp(slug_ts, tz=timezone.utc)

    # timeframe 추출
    timeframe = "15m"  # default
    for part in parts:
        if part in ("15m", "1h", "4h"):
            timeframe = part
            break

    if timeframe == "1h":
        candle_end = candle_start + timedelta(hours=1)
    else:
        candle_end = candle_start + timedelta(minutes=15)

    async with db_pool.acquire() as conn:
        # 1. Load orderbook data
        books, changes, token_to_side = await load_orderbook_data(
            conn, market_slug, candle_start, candle_end
        )

        if not books:
            return {
                "candle_start": candle_start.isoformat(),
                "candle_end": candle_end.isoformat(),
                "coin": coin,
                "strategy": strategy,
                "market_slug": market_slug,
                "market_result": None,
                "trades": [],
                "total_trades": 0,
                "up_trades": 0,
                "down_trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0,
                "paper": None,
            }

        # 2. Replay orderbook → BBO snapshots
        up_snaps, down_snaps = replay_orderbook(books, changes, token_to_side)

        # 3. Load Binance prices
        binance_prices = await load_binance_prices(
            conn, coin, candle_start, candle_end
        )

        # 4. Get market result
        market_result = await get_market_result(
            conn, coin, timeframe, candle_start, candle_end
        )

        # 5. Run strategy (crossing vs delta-based)
        if strategy in ("crossing", "crossing_v2"):
            result = run_crossing_backtest(
                coin, timeframe, candle_start, candle_end,
                up_snaps, down_snaps, binance_prices, market_result,
                strategy_name=strategy,
            )
        else:
            result = run_candle_backtest(
                coin, timeframe, candle_start, candle_end,
                up_snaps, down_snaps, binance_prices, market_result,
            )

        trades = result["trades"]
        total_pnl = round(sum(t.get("pnl") or 0 for t in trades), 4)

        # 6. Get paper trades
        paper_trades_raw = await get_paper_trades(conn, coin, market_slug, strategy_name=strategy)

        paper = None
        if paper_trades_raw:
            paper_pnl = round(sum(t["pnl"] for t in paper_trades_raw), 4)
            paper = {
                "total_trades": len(paper_trades_raw),
                "up_trades": sum(1 for t in paper_trades_raw if t["side"] == "UP"),
                "down_trades": sum(1 for t in paper_trades_raw if t["side"] == "DOWN"),
                "wins": sum(1 for t in paper_trades_raw if t["outcome"] == "WIN"),
                "losses": sum(1 for t in paper_trades_raw if t["outcome"] == "LOSS"),
                "pnl": paper_pnl,
                "trades": [{
                    "time": t["time"],
                    "side": t["side"],
                    "entry_price": t["entry_price"],
                    "contracts": t["contracts"],
                    "cost": t["cost"],
                    "outcome": t["outcome"],
                    "pnl": round(t["pnl"], 4),
                } for t in paper_trades_raw],
            }

        # 7. Response (same format as old TS route)
        trades_reversed = list(reversed(trades))
        return {
            "candle_start": candle_start.isoformat(),
            "candle_end": candle_end.isoformat(),
            "coin": coin,
            "strategy": strategy,
            "market_slug": market_slug,
            "market_result": market_result,
            "trades": trades_reversed,
            "total_trades": len(trades),
            "up_trades": sum(1 for t in trades if t["side"] == "UP"),
            "down_trades": sum(1 for t in trades if t["side"] == "DOWN"),
            "wins": sum(1 for t in trades if t.get("outcome") == "WIN"),
            "losses": sum(1 for t in trades if t.get("outcome") == "LOSS"),
            "pnl": total_pnl,
            "paper": paper,
        }
