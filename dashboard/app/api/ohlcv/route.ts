import { NextRequest, NextResponse } from 'next/server';
import pool from '@/lib/db';
import { isValidCoin, isValidTimeframe, invalidCoinResponse, invalidTimeframeResponse } from '@/lib/validation';

export const dynamic = 'force-dynamic';

// ET timezone offset (UTC-5 for EST)
const ET_OFFSET_MINUTES = -5 * 60;

// 현재 캔들 윈도우의 시작 시간 계산 (ET 기준)
function getCandleStartTime(timeframe: string): Date {
  const now = new Date();

  // UTC를 ET로 변환
  const etTime = new Date(now.getTime() + ET_OFFSET_MINUTES * 60 * 1000);

  const hour = etTime.getUTCHours();
  const minute = etTime.getUTCMinutes();

  let startHour = hour;
  let startMinute = 0;

  if (timeframe === '5m') {
    startMinute = Math.floor(minute / 5) * 5;
  } else if (timeframe === '15m') {
    startMinute = Math.floor(minute / 15) * 15;
  } else if (timeframe === '1h') {
    startMinute = 0;
  } else if (timeframe === '4h') {
    startHour = Math.floor(hour / 4) * 4;
    startMinute = 0;
  }

  etTime.setUTCHours(startHour, startMinute, 0, 0);

  // ET를 UTC로 다시 변환
  return new Date(etTime.getTime() - ET_OFFSET_MINUTES * 60 * 1000);
}

export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const coin = searchParams.get('coin') || 'btc';
    const timeframe = searchParams.get('timeframe') || '15m';

    if (!isValidCoin(coin)) return invalidCoinResponse(coin);
    if (!isValidTimeframe(timeframe)) return invalidTimeframeResponse(timeframe);

    const startTime = getCandleStartTime(timeframe);

    const query = `
      SELECT
        time,
        open,
        high,
        low,
        close,
        volume,
        num_trades
      FROM binance_ohlcv_1m
      WHERE coin = $1
        AND time >= $2
      ORDER BY time ASC
    `;

    const result = await pool.query(query, [coin, startTime.toISOString()]);

    return NextResponse.json({
      coin,
      timeframe,
      startTime: startTime.toISOString(),
      candleCount: result.rows.length,
      data: result.rows.map(row => ({
        time: row.time,
        open: parseFloat(row.open),
        high: parseFloat(row.high),
        low: parseFloat(row.low),
        close: parseFloat(row.close),
        volume: parseFloat(row.volume),
        numTrades: row.num_trades,
      })),
    });
  } catch (error) {
    console.error('Error fetching OHLCV data:', error);
    return NextResponse.json(
      { error: 'Failed to fetch OHLCV data' },
      { status: 500 }
    );
  }
}
