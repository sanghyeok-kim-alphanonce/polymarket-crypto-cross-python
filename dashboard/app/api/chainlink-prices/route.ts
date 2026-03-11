import { NextResponse } from 'next/server';
import pool from '@/lib/db';
import { isValidCoin, invalidCoinResponse } from '@/lib/validation';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const timeRange = searchParams.get('timeRange') || '24h';
  const coin = searchParams.get('coin');

  if (coin && !isValidCoin(coin)) return invalidCoinResponse(coin);

  try {
    // Get latest prices
    const latestQuery = coin
      ? `SELECT DISTINCT ON (coin) time, coin, chainlink_price as price
         FROM coin_prices
         WHERE coin = $1 AND chainlink_price IS NOT NULL
         ORDER BY coin, time DESC`
      : `SELECT DISTINCT ON (coin) time, coin, chainlink_price as price
         FROM coin_prices
         WHERE chainlink_price IS NOT NULL
         ORDER BY coin, time DESC`;

    const latestResult = await pool.query(latestQuery, coin ? [coin] : []);

    const latest: Record<string, { price: number; time: string }> = {};
    for (const row of latestResult.rows) {
      latest[row.coin] = {
        price: parseFloat(row.price),
        time: row.time,
      };
    }

    // 15-minute bucketed data (like reference code)
    let duration: string | null;
    switch (timeRange) {
      case '24h': duration = '24 hours'; break;
      case 'all': duration = null; break;
      default: duration = '24 hours';
    }

    let bucketQuery: string;
    let queryParams: (string | null)[];

    // Exact 15-minute boundary: second=0, minute in (0,15,30,45)
    const exactFilter = `EXTRACT(SECOND FROM time) = 0 AND EXTRACT(MINUTE FROM time)::int % 15 = 0`;

    if (duration) {
      bucketQuery = coin
        ? `SELECT time AS bucket, coin, chainlink_price AS price, chainlink_source AS source, time
           FROM coin_prices
           WHERE time > NOW() - $1::interval
             AND coin = $2
             AND chainlink_price IS NOT NULL
             AND ${exactFilter}
           ORDER BY time ASC`
        : `SELECT time AS bucket, coin, chainlink_price AS price, chainlink_source AS source, time
           FROM coin_prices
           WHERE time > NOW() - $1::interval
             AND chainlink_price IS NOT NULL
             AND ${exactFilter}
           ORDER BY time ASC`;
      queryParams = coin ? [duration, coin] : [duration];
    } else {
      bucketQuery = coin
        ? `SELECT time AS bucket, coin, chainlink_price AS price, chainlink_source AS source, time
           FROM coin_prices
           WHERE coin = $1
             AND chainlink_price IS NOT NULL
             AND ${exactFilter}
           ORDER BY time ASC`
        : `SELECT time AS bucket, coin, chainlink_price AS price, chainlink_source AS source, time
           FROM coin_prices
           WHERE chainlink_price IS NOT NULL
             AND ${exactFilter}
           ORDER BY time ASC`;
      queryParams = coin ? [coin] : [];
    }

    const historyResult = await pool.query(bucketQuery, queryParams);

    const historical: Record<string, Array<{ time: string; price: number; source: string | null }>> = {};
    for (const row of historyResult.rows) {
      if (!historical[row.coin]) {
        historical[row.coin] = [];
      }
      historical[row.coin].push({
        time: row.bucket,
        price: parseFloat(row.price),
        source: row.source ?? null,
      });
    }

    return NextResponse.json({
      latest,
      historical,
      timeRange,
      timestamp: Date.now(),
      source: 'chainlink-15m-bucket',
    });
  } catch (error) {
    console.error('Failed to fetch Chainlink prices:', error);
    return NextResponse.json(
      { error: 'Failed to load Chainlink prices' },
      { status: 500 }
    );
  }
}
