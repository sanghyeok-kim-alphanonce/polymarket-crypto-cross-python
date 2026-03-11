import { NextResponse } from 'next/server';
import pool from '@/lib/db';
import { isValidCoin, invalidCoinResponse } from '@/lib/validation';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const coin = searchParams.get('coin') || 'btc';
    const offset = Math.max(-100, Math.min(1, parseInt(searchParams.get('offset') || '0') || 0));

    if (!isValidCoin(coin)) return invalidCoinResponse(coin);

    const client = await pool.connect();

    try {
      // Calculate candle start time (offset: 0 = previous completed, 1 = current, -1 = 2 ago)
      const offsetMinutes = 15 * (1 - offset);
      const candleStartQuery = `
        SELECT date_trunc('hour', NOW() - ($1 || ' minutes')::interval)
          + INTERVAL '15 min' * FLOOR(EXTRACT(MINUTE FROM NOW() - ($1 || ' minutes')::interval) / 15) AS start_time
      `;
      const candleResult = await client.query(candleStartQuery, [offsetMinutes]);
      const candleStart = candleResult.rows[0].start_time;
      const candleEnd = new Date(new Date(candleStart).getTime() + 15 * 60 * 1000);

      // 1. Binance prices from binance_ticks (1-second data)
      const binanceQuery = `
        SELECT
          EXTRACT(EPOCH FROM time)::bigint AS ts,
          price
        FROM binance_ticks
        WHERE coin = $1
          AND time >= $2
          AND time < $3
        ORDER BY time ASC
      `;
      const binanceResult = await client.query(binanceQuery, [coin, candleStart, candleEnd]);

      // 2. Chainlink prices from coin_prices
      let chainlinkResult: { rows: Array<{ ts: string; price: string }> } = { rows: [] };
      try {
        const chainlinkQuery = `
          SELECT
            EXTRACT(EPOCH FROM time)::bigint AS ts,
            chainlink_price AS price
          FROM coin_prices
          WHERE coin = $1
            AND time >= $2
            AND time < $3
            AND chainlink_price IS NOT NULL
          ORDER BY time ASC
        `;
        chainlinkResult = await client.query(chainlinkQuery, [coin, candleStart, candleEnd]);
      } catch (e) {
        console.log('Chainlink query failed, skipping:', e);
      }

      // 3. Orderbook data via replay (books + changes merged chronologically)
      const candleStartEpoch = Math.floor(new Date(candleStart).getTime() / 1000);
      const marketSlug = `${coin}-updown-15m-${candleStartEpoch}`;

      const orderbookUpMap = new Map<number, { ask: number; bid: number }>();
      const orderbookDownMap = new Map<number, { ask: number; bid: number }>();
      let obSnapshotCount = 0;

      try {
        const booksRes = await client.query(`
          SELECT time::text, side, bids, asks, token_id
          FROM orderbook_books WHERE market_slug = $1 ORDER BY time ASC
        `, [marketSlug]);

        if (booksRes.rows.length > 0) {
          const tokenToSide: Record<string, string> = {};
          const tokenIds: string[] = [];
          for (const row of booksRes.rows) {
            tokenToSide[row.token_id] = row.side;
            if (!tokenIds.includes(row.token_id)) tokenIds.push(row.token_id);
          }

          const changesRes = await client.query(`
            SELECT time::text, token_id, price::float, size::int, book_side
            FROM orderbook_changes
            WHERE token_id = ANY($1) AND time >= $2 AND time < $3
            ORDER BY time ASC
          `, [tokenIds, candleStart, candleEnd]);

          // Merge books + changes chronologically
          type Ev = { time: string; type: 'book'; side: string; bids: [number,number][]; asks: [number,number][] }
                  | { time: string; type: 'change'; side: string; price: number; size: number; book_side: string };
          const events: Ev[] = [];
          for (const r of booksRes.rows) {
            events.push({ time: r.time, type: 'book', side: r.side, bids: r.bids ?? [], asks: r.asks ?? [] });
          }
          for (const r of changesRes.rows) {
            const side = tokenToSide[r.token_id];
            if (side) events.push({ time: r.time, type: 'change', side, price: r.price, size: r.size, book_side: r.book_side });
          }
          events.sort((a, b) => a.time.localeCompare(b.time));

          const bookState: Record<string, { bids: Map<number, number>; asks: Map<number, number> }> = {};
          const lastBbo: Record<string, { bid: number; ask: number }> = {};

          for (const ev of events) {
            if (ev.type === 'book') {
              const bids = new Map<number, number>();
              const asks = new Map<number, number>();
              for (const [p, s] of ev.bids) { bids.set(Number(p), Number(s)); }
              for (const [p, s] of ev.asks) { asks.set(Number(p), Number(s)); }
              bookState[ev.side] = { bids, asks };
            } else {
              const state = bookState[ev.side];
              if (!state) continue;
              const bookMap = ev.book_side === 'BUY' ? state.bids : state.asks;
              if (ev.size === 0) bookMap.delete(ev.price);
              else bookMap.set(ev.price, ev.size);
            }
            // Emit BBO if changed
            const state = bookState[ev.side];
            if (!state || state.bids.size === 0 || state.asks.size === 0) continue;
            const bestBid = Math.max(...state.bids.keys());
            const bestAsk = Math.min(...state.asks.keys());
            const prev = lastBbo[ev.side];
            if (!prev || bestBid !== prev.bid || bestAsk !== prev.ask) {
              const ts = Math.floor(new Date(ev.time).getTime() / 1000);
              const target = ev.side === 'up' ? orderbookUpMap : orderbookDownMap;
              target.set(ts, { ask: bestAsk, bid: bestBid });
              lastBbo[ev.side] = { bid: bestBid, ask: bestAsk };
            }
          }

          obSnapshotCount = orderbookUpMap.size + orderbookDownMap.size;
        }
      } catch (e) {
        console.log('Orderbook replay failed, skipping:', e);
      }

      // Collect all timestamps and sort
      const allTimestamps = new Set<number>();
      binanceResult.rows.forEach(r => allTimestamps.add(Number(r.ts)));
      chainlinkResult.rows.forEach(r => allTimestamps.add(Number(r.ts)));
      orderbookUpMap.forEach((_, ts) => allTimestamps.add(ts));
      orderbookDownMap.forEach((_, ts) => allTimestamps.add(ts));

      const sortedTimestamps = Array.from(allTimestamps).sort((a, b) => a - b);

      // Build maps for fast lookup
      const binanceMap = new Map<number, number>();
      binanceResult.rows.forEach(r => binanceMap.set(Number(r.ts), parseFloat(r.price)));

      const chainlinkMap = new Map<number, number>();
      chainlinkResult.rows.forEach(r => chainlinkMap.set(Number(r.ts), parseFloat(r.price)));

      // Forward-fill merge
      let lastBinance: number | null = null;
      let lastChainlink: number | null = null;
      let lastUpAsk: number | null = null;
      let lastDownAsk: number | null = null;

      const unifiedData = sortedTimestamps.map(ts => {
        if (binanceMap.has(ts)) lastBinance = binanceMap.get(ts)!;
        if (chainlinkMap.has(ts)) lastChainlink = chainlinkMap.get(ts)!;
        if (orderbookUpMap.has(ts)) lastUpAsk = orderbookUpMap.get(ts)!.ask;
        if (orderbookDownMap.has(ts)) lastDownAsk = orderbookDownMap.get(ts)!.ask;

        return {
          ts,
          binance: lastBinance,
          chainlink: lastChainlink,
          upAsk: lastUpAsk,
          downAsk: lastDownAsk,
        };
      });

      const candleStartPrice = binanceResult.rows.length > 0
        ? parseFloat(binanceResult.rows[0].price)
        : null;

      const chainlinkStartPrice = chainlinkResult.rows.length > 0
        ? parseFloat(chainlinkResult.rows[0].price)
        : null;

      return NextResponse.json({
        coin,
        candleStart,
        candleEnd,
        marketSlug,
        obSnapshotCount,
        candleStartPrice,
        chainlinkStartPrice,
        dataPoints: unifiedData.length,
        data: unifiedData,
        timestamp: Date.now(),
      });
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Failed to fetch unified data:', error);
    return NextResponse.json(
      { error: 'Failed to load unified data' },
      { status: 500 }
    );
  }
}
