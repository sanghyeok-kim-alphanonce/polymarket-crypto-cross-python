import { NextResponse } from 'next/server';
import pool from '@/lib/db';
import { isValidCoin, invalidCoinResponse } from '@/lib/validation';

export const dynamic = 'force-dynamic';

const VALID_STRATEGIES = ['v12_5', 'v12_6', 'crossing', 'crossing_v2', 'paper_cross_limit_5m', 'paper_cross_limit_15m'];

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const coin = searchParams.get('coin') || 'all';
  const strategy = searchParams.get('strategy') || 'v12_5';
  const candle = searchParams.get('candle'); // 특정 캔들의 trades만 가져오기

  if (coin !== 'all' && !isValidCoin(coin)) return invalidCoinResponse(coin);
  if (!VALID_STRATEGIES.includes(strategy)) {
    return NextResponse.json(
      { error: `Invalid strategy: ${strategy}. Valid: ${VALID_STRATEGIES.join(', ')}` },
      { status: 400 }
    );
  }

  const STRATEGY_NAME = strategy;

  const client = await pool.connect();
  try {
    // 1. Recent trades (또는 특정 캔들의 trades)
    let tradesQuery = `
      SELECT id, time, strategy_name, coin, timeframe,
             mid_price, up_mid_price, down_mid_price,
             side, order_price, orderbook_snapshot,
             market_slug, token_id, candle_start_time, candle_end_time,
             fill_time, fill_price, fill_orderbook,
             contracts, cost, exit_price, pnl, outcome, market_result,
             status, closed_at, reason
      FROM test_paper_trades
      WHERE strategy_name = $1
    `;
    const tradesParams: (string | Date)[] = [STRATEGY_NAME];

    if (coin !== 'all') {
      tradesParams.push(coin);
      tradesQuery += ` AND coin = $${tradesParams.length}`;
    }

    if (candle) {
      tradesParams.push(candle);
      tradesQuery += ` AND candle_start_time = $${tradesParams.length}`;
      tradesQuery += ` ORDER BY time ASC`; // 캔들 내에서는 시간순
    } else {
      tradesQuery += ` ORDER BY time DESC LIMIT 100`;
    }

    const tradesResult = await client.query(tradesQuery, tradesParams);

    // 2. Overall stats
    let overallQuery = `
      SELECT
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE status = 'PENDING') as pending,
        COUNT(*) FILTER (WHERE status = 'FILLED') as filled,
        COUNT(*) FILTER (WHERE status = 'CLOSED') as closed,
        COUNT(*) FILTER (WHERE outcome = 'WIN') as wins,
        COUNT(*) FILTER (WHERE outcome = 'LOSS') as losses,
        COALESCE(SUM(pnl), 0) as paper_pnl,
        ROUND(100.0 * COUNT(*) FILTER (WHERE outcome = 'WIN') / NULLIF(COUNT(*) FILTER (WHERE outcome IS NOT NULL), 0), 2) as win_rate
      FROM test_paper_trades
      WHERE strategy_name = $1
    `;
    const overallParams: string[] = [STRATEGY_NAME];

    if (coin !== 'all') {
      overallParams.push(coin);
      overallQuery += ` AND coin = $${overallParams.length}`;
    }

    const overallResult = await client.query(overallQuery, overallParams);

    // 3. Stats by coin
    const coinStatsQuery = `
      SELECT
        coin,
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE outcome = 'WIN') as wins,
        COUNT(*) FILTER (WHERE outcome = 'LOSS') as losses,
        COALESCE(SUM(pnl), 0) as paper_pnl,
        ROUND(100.0 * COUNT(*) FILTER (WHERE outcome = 'WIN') / NULLIF(COUNT(*) FILTER (WHERE outcome IS NOT NULL), 0), 2) as win_rate
      FROM test_paper_trades
      WHERE strategy_name = $1
      GROUP BY coin
      ORDER BY coin
    `;
    const coinStatsResult = await client.query(coinStatsQuery, [STRATEGY_NAME]);

    // 5. Per-candle stats (15분봉별 UP/DOWN 평단가, 수량, PnL)
    let candleStatsQuery = `
      SELECT
        candle_start_time,
        side,
        COUNT(*) as count,
        COALESCE(SUM(contracts), 0) as total_contracts,
        COALESCE(SUM(cost), 0) as total_cost,
        CASE WHEN SUM(contracts) > 0
          THEN ROUND((SUM(cost) / SUM(contracts))::numeric, 4)
          ELSE 0
        END as avg_entry_price,
        COALESCE(SUM(pnl), 0) as pnl,
        STRING_AGG(DISTINCT outcome, ',') as outcomes
      FROM test_paper_trades
      WHERE strategy_name = $1
        AND candle_start_time IS NOT NULL
    `;
    const candleParams: string[] = [STRATEGY_NAME];

    if (coin !== 'all') {
      candleParams.push(coin);
      candleStatsQuery += ` AND coin = $${candleParams.length}`;
    }

    candleStatsQuery += `
      GROUP BY candle_start_time, side
      ORDER BY candle_start_time DESC, side
    `;

    const candleStatsResult = await client.query(candleStatsQuery, candleParams);

    return NextResponse.json({
      trades: tradesResult.rows,
      overall: overallResult.rows[0] || {
        total: 0, pending: 0, filled: 0, closed: 0,
        wins: 0, losses: 0, paper_pnl: 0, win_rate: 0
      },
      coinStats: coinStatsResult.rows,
      candleStats: candleStatsResult.rows,
    });
  } catch (error) {
    console.error('Paper trading API error:', error);
    return NextResponse.json(
      { error: 'Failed to fetch paper trading data', details: String(error) },
      { status: 500 }
    );
  } finally {
    client.release();
  }
}
