import { NextResponse } from 'next/server';
import pool from '@/lib/db';
import { isValidCoin, isValidTimeframe } from '@/lib/validation';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const tab = searchParams.get('tab') || 'books';
  const coin = searchParams.get('coin') || 'btc';
  const timeframe = searchParams.get('timeframe') || '15m';
  const limit = Math.min(parseInt(searchParams.get('limit') || '50'), 500);
  const offset = parseInt(searchParams.get('offset') || '0');
  const order = searchParams.get('order') === 'ASC' ? 'ASC' : 'DESC';
  const rowId = searchParams.get('rowId'); // For detail view (books tab)

  if (!isValidCoin(coin) || !isValidTimeframe(timeframe)) {
    return NextResponse.json({ error: 'Invalid coin or timeframe' }, { status: 400 });
  }

  try {
    if (tab === 'changes') {
      // Changes tab: orderbook_changes
      // Need token_ids for this coin/timeframe - get from books table
      const tokenRes = await pool.query(
        `SELECT DISTINCT token_id, side FROM orderbook_books WHERE coin = $1 AND timeframe = $2`,
        [coin, timeframe]
      );
      const tokenIds = tokenRes.rows.map(r => r.token_id);
      const tokenSideMap: Record<string, string> = {};
      for (const r of tokenRes.rows) tokenSideMap[r.token_id] = r.side;

      if (tokenIds.length === 0) {
        return NextResponse.json({ rows: [], total: 0, limit, offset, tokenSideMap: {} });
      }

      const countResult = await pool.query(
        `SELECT COUNT(*)::int as total FROM orderbook_changes WHERE token_id = ANY($1)`,
        [tokenIds]
      );

      const result = await pool.query(
        `SELECT time, token_id, price::float, size::int, book_side
         FROM orderbook_changes
         WHERE token_id = ANY($1)
         ORDER BY time ${order}
         LIMIT $2 OFFSET $3`,
        [tokenIds, limit, offset]
      );

      return NextResponse.json({
        rows: result.rows,
        total: countResult.rows[0]?.total ?? 0,
        limit,
        offset,
        tokenSideMap,
      });
    }

    // Books tab (default)
    if (rowId) {
      // Detail view: include bids/asks
      const result = await pool.query(
        `SELECT time, coin, timeframe, side, bids, asks, market_slug, token_id
         FROM orderbook_books
         WHERE coin = $1 AND timeframe = $2 AND time = $3
         ORDER BY side ASC`,
        [coin, timeframe, rowId]
      );
      return NextResponse.json({ rows: result.rows });
    }

    // List view
    const countResult = await pool.query(
      `SELECT COUNT(*)::int as total FROM orderbook_books WHERE coin = $1 AND timeframe = $2`,
      [coin, timeframe]
    );

    const result = await pool.query(
      `SELECT time, coin, timeframe, side, market_slug, token_id,
              jsonb_array_length(bids) as bid_levels,
              jsonb_array_length(asks) as ask_levels
       FROM orderbook_books
       WHERE coin = $1 AND timeframe = $2
       ORDER BY time ${order}, side ASC
       LIMIT $3 OFFSET $4`,
      [coin, timeframe, limit, offset]
    );

    return NextResponse.json({
      rows: result.rows,
      total: countResult.rows[0]?.total ?? 0,
      limit,
      offset,
    });
  } catch (error) {
    console.error('Failed to fetch orderbook data:', error);
    return NextResponse.json({ error: 'Failed to load orderbook data' }, { status: 500 });
  }
}
