import { NextResponse } from 'next/server';
import pool from '@/lib/db';

export const dynamic = 'force-dynamic';

// 전략별 표시 이름 (real trading 전략만)
const STRATEGY_DISPLAY_NAMES: Record<string, string> = {
  'real_crossing': 'BTC 15m Crossing',
  'real_crossing_5m': 'BTC 5m Crossing',
  'real_crossing_v2': '13m30s~14m30s 첫 Crossing',
  'real_trade_cross_14m_10limit': '0~14m 10회+밸런싱',
  'real_trade_cross_limit_hedge': '15m 5분~14분 10회',
  'real_trade_5m_cross_front': '5m 0~3분 Front',
};

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const candle = searchParams.get('candle');
  const strategyParam = searchParams.get('strategy'); // 'all' | 특정 전략명

  // 전략 필터링은 DB 조회 후에 처리

  const client = await pool.connect();
  try {
    // 0. 사용 가능한 전략 목록 조회 (real_ 로 시작하는 전략만)
    const availableStrategiesQuery = `
      SELECT DISTINCT strategy_name, COUNT(*) as trade_count
      FROM test_paper_trades
      WHERE strategy_name IS NOT NULL
        AND strategy_name LIKE 'real_%'
      GROUP BY strategy_name
      ORDER BY trade_count DESC, strategy_name
    `;
    const availableStrategiesResult = await client.query(availableStrategiesQuery);

    // 조회된 real trading 전략 목록
    const allStrategies = availableStrategiesResult.rows.map(r => r.strategy_name);

    // 전략 필터링: 'all'이면 모든 전략, 아니면 특정 전략만
    const strategies = strategyParam && strategyParam !== 'all'
      ? [strategyParam]
      : allStrategies;

    // 1. Recent trades (또는 특정 캔들의 trades)
    let tradesQuery = `
      SELECT id, time, strategy_name, coin, timeframe,
             mid_price, up_mid_price, down_mid_price,
             side, order_price, orderbook_snapshot,
             market_slug, token_id, candle_start_time, candle_end_time,
             fill_time, fill_price,
             contracts, cost, filled_contracts, filled_cost,
             exit_price, pnl, outcome, market_result,
             status, closed_at, reason
      FROM test_paper_trades
      WHERE strategy_name = ANY($1)
    `;
    const tradesParams: (string[] | string | Date)[] = [strategies];

    if (candle) {
      tradesParams.push(candle);
      tradesQuery += ` AND candle_start_time = $${tradesParams.length}`;
      tradesQuery += ` ORDER BY time ASC`;
    } else {
      tradesQuery += ` ORDER BY time DESC LIMIT 100`;
    }

    const tradesResult = await client.query(tradesQuery, tradesParams);

    // 2. Overall stats
    const overallQuery = `
      SELECT
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE status = 'PENDING') as pending,
        COUNT(*) FILTER (WHERE status = 'FILLED') as filled,
        COUNT(*) FILTER (WHERE status = 'CLOSED') as closed,
        COUNT(*) FILTER (WHERE status = 'FAILED') as failed,
        COUNT(*) FILTER (WHERE outcome = 'WIN') as wins,
        COUNT(*) FILTER (WHERE outcome = 'LOSS') as losses,
        COALESCE(SUM(pnl), 0) as real_pnl,
        COALESCE(SUM(filled_cost), 0) as total_cost,
        ROUND(100.0 * COUNT(*) FILTER (WHERE outcome = 'WIN') / NULLIF(COUNT(*) FILTER (WHERE outcome IS NOT NULL), 0), 2) as win_rate
      FROM test_paper_trades
      WHERE strategy_name = ANY($1)
    `;
    const overallResult = await client.query(overallQuery, [strategies]);

    // 3. Per-candle stats (봉별 UP/DOWN 평단가, 수량, PnL)
    const candleStatsQuery = `
      SELECT
        candle_start_time,
        side,
        COUNT(*) as count,
        COALESCE(SUM(filled_contracts), 0) as total_contracts,
        COALESCE(SUM(filled_cost), 0) as total_cost,
        CASE WHEN SUM(filled_contracts) > 0
          THEN ROUND((SUM(filled_cost) / SUM(filled_contracts))::numeric, 4)
          ELSE 0
        END as avg_entry_price,
        COALESCE(SUM(pnl), 0) as pnl,
        STRING_AGG(DISTINCT outcome, ',') as outcomes,
        STRING_AGG(DISTINCT status, ',') as statuses
      FROM test_paper_trades
      WHERE strategy_name = ANY($1)
        AND candle_start_time IS NOT NULL
      GROUP BY candle_start_time, side
      ORDER BY candle_start_time DESC, side
    `;
    const candleStatsResult = await client.query(candleStatsQuery, [strategies]);

    // 4. Today's stats
    const todayQuery = `
      SELECT
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE status = 'FILLED') as filled,
        COUNT(*) FILTER (WHERE status = 'CLOSED') as closed,
        COUNT(*) FILTER (WHERE outcome = 'WIN') as wins,
        COUNT(*) FILTER (WHERE outcome = 'LOSS') as losses,
        COALESCE(SUM(pnl), 0) as real_pnl,
        COALESCE(SUM(filled_cost), 0) as total_cost
      FROM test_paper_trades
      WHERE strategy_name = ANY($1)
        AND time >= CURRENT_DATE
    `;
    const todayResult = await client.query(todayQuery, [strategies]);

    // 전략 목록 응답에 추가
    const availableStrategies = availableStrategiesResult.rows.map(row => ({
      name: row.strategy_name,
      displayName: STRATEGY_DISPLAY_NAMES[row.strategy_name] || row.strategy_name,
      tradeCount: Number(row.trade_count),
    }));

    return NextResponse.json({
      trades: tradesResult.rows,
      overall: overallResult.rows[0] || {
        total: 0, pending: 0, filled: 0, closed: 0, failed: 0,
        wins: 0, losses: 0, real_pnl: 0, total_cost: 0, win_rate: 0
      },
      today: todayResult.rows[0] || {
        total: 0, filled: 0, closed: 0, wins: 0, losses: 0, real_pnl: 0, total_cost: 0
      },
      candleStats: candleStatsResult.rows,
      availableStrategies,
      currentStrategy: strategyParam || 'all',
    });
  } catch (error) {
    console.error('Real trading API error:', error);
    return NextResponse.json(
      { error: 'Failed to fetch real trading data', details: String(error) },
      { status: 500 }
    );
  } finally {
    client.release();
  }
}
