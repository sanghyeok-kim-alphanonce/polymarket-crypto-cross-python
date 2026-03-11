import { NextResponse } from 'next/server';
import pool from '@/lib/db';
import { isValidCoin, invalidCoinResponse } from '@/lib/validation';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const coin = searchParams.get('coin') || 'all';
    const days = Math.min(30, Math.max(1, parseInt(searchParams.get('days') || '7') || 7));

    if (coin !== 'all' && !isValidCoin(coin)) return invalidCoinResponse(coin);

    const client = await pool.connect();

    try {
      // 1. 캔들별 crossing 통계
      const candleStatsQuery = `
        SELECT
          coin,
          candle_start,
          direction,
          COUNT(*) as count,
          AVG(candle_elapsed_sec) as avg_elapsed_sec,
          MIN(candle_elapsed_sec) as min_elapsed_sec,
          MAX(candle_elapsed_sec) as max_elapsed_sec
        FROM crossing_events
        WHERE time >= NOW() - ($1 || ' days')::interval
          ${coin !== 'all' ? 'AND coin = $2' : ''}
        GROUP BY coin, candle_start, direction
        ORDER BY candle_start DESC, coin, direction
      `;
      const candleStatsResult = await client.query(
        candleStatsQuery,
        coin !== 'all' ? [days, coin] : [days]
      );

      // 2. 코인별 전체 통계
      const overallStatsQuery = `
        SELECT
          coin,
          direction,
          COUNT(*) as total_count,
          AVG(candle_elapsed_sec) as avg_elapsed_sec,
          COUNT(DISTINCT candle_start) as candle_count
        FROM crossing_events
        WHERE time >= NOW() - ($1 || ' days')::interval
          ${coin !== 'all' ? 'AND coin = $2' : ''}
        GROUP BY coin, direction
        ORDER BY coin, direction
      `;
      const overallStatsResult = await client.query(
        overallStatsQuery,
        coin !== 'all' ? [days, coin] : [days]
      );

      // 3. 시간대별 분포 (캔들 내 경과 시간 구간별)
      const timeDistQuery = `
        SELECT
          coin,
          direction,
          CASE
            WHEN candle_elapsed_sec < 60 THEN '0-1m'
            WHEN candle_elapsed_sec < 180 THEN '1-3m'
            WHEN candle_elapsed_sec < 300 THEN '3-5m'
            WHEN candle_elapsed_sec < 600 THEN '5-10m'
            ELSE '10-15m'
          END as time_bucket,
          COUNT(*) as count
        FROM crossing_events
        WHERE time >= NOW() - ($1 || ' days')::interval
          ${coin !== 'all' ? 'AND coin = $2' : ''}
        GROUP BY coin, direction, time_bucket
        ORDER BY coin, direction, time_bucket
      `;
      const timeDistResult = await client.query(
        timeDistQuery,
        coin !== 'all' ? [days, coin] : [days]
      );

      // 4. 최근 crossing 이벤트 (최신 50개)
      const recentEventsQuery = `
        SELECT
          time,
          coin,
          timeframe,
          direction,
          candle_start,
          candle_open,
          prev_price,
          current_price,
          candle_elapsed_sec
        FROM crossing_events
        WHERE time >= NOW() - ($1 || ' days')::interval
          ${coin !== 'all' ? 'AND coin = $2' : ''}
        ORDER BY time DESC
        LIMIT 50
      `;
      const recentEventsResult = await client.query(
        recentEventsQuery,
        coin !== 'all' ? [days, coin] : [days]
      );

      // 캔들별 통계를 캔들 단위로 그룹핑
      const candleMap = new Map<string, {
        candle_start: string;
        coin: string;
        up: number;
        down: number;
        total: number;
        up_avg_elapsed: number | null;
        down_avg_elapsed: number | null;
      }>();

      for (const row of candleStatsResult.rows) {
        const key = `${row.coin}_${row.candle_start}`;
        if (!candleMap.has(key)) {
          candleMap.set(key, {
            candle_start: row.candle_start,
            coin: row.coin,
            up: 0,
            down: 0,
            total: 0,
            up_avg_elapsed: null,
            down_avg_elapsed: null,
          });
        }
        const entry = candleMap.get(key)!;
        if (row.direction === 'up') {
          entry.up = parseInt(row.count);
          entry.up_avg_elapsed = parseFloat(row.avg_elapsed_sec);
        } else {
          entry.down = parseInt(row.count);
          entry.down_avg_elapsed = parseFloat(row.avg_elapsed_sec);
        }
        entry.total = entry.up + entry.down;
      }

      const candleStats = Array.from(candleMap.values())
        .sort((a, b) => new Date(b.candle_start).getTime() - new Date(a.candle_start).getTime());

      // 코인별 통계 그룹핑
      const coinMap = new Map<string, {
        coin: string;
        up_total: number;
        down_total: number;
        total: number;
        up_avg_elapsed: number | null;
        down_avg_elapsed: number | null;
        candle_count: number;
        avg_per_candle: number;
      }>();

      for (const row of overallStatsResult.rows) {
        if (!coinMap.has(row.coin)) {
          coinMap.set(row.coin, {
            coin: row.coin,
            up_total: 0,
            down_total: 0,
            total: 0,
            up_avg_elapsed: null,
            down_avg_elapsed: null,
            candle_count: 0,
            avg_per_candle: 0,
          });
        }
        const entry = coinMap.get(row.coin)!;
        if (row.direction === 'up') {
          entry.up_total = parseInt(row.total_count);
          entry.up_avg_elapsed = parseFloat(row.avg_elapsed_sec);
        } else {
          entry.down_total = parseInt(row.total_count);
          entry.down_avg_elapsed = parseFloat(row.avg_elapsed_sec);
        }
        entry.total = entry.up_total + entry.down_total;
        entry.candle_count = Math.max(entry.candle_count, parseInt(row.candle_count));
      }

      // 캔들당 평균 계산
      for (const entry of coinMap.values()) {
        entry.avg_per_candle = entry.candle_count > 0
          ? entry.total / entry.candle_count
          : 0;
      }

      const coinStats = Array.from(coinMap.values())
        .sort((a, b) => b.total - a.total);

      return NextResponse.json({
        coin,
        days,
        candleStats,
        coinStats,
        timeDist: timeDistResult.rows,
        recentEvents: recentEventsResult.rows,
        timestamp: Date.now(),
      });
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Failed to fetch crossing analysis:', error);
    return NextResponse.json(
      { error: 'Failed to load crossing analysis' },
      { status: 500 }
    );
  }
}
