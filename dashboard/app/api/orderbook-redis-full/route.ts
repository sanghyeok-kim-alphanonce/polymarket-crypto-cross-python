import { NextResponse } from 'next/server';
import { getOrderbookFullFromRedis } from '@/lib/redis';
import { isValidCoin, isValidTimeframe, invalidCoinResponse, invalidTimeframeResponse } from '@/lib/validation';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const coin = searchParams.get('coin');
  const timeframe = searchParams.get('timeframe');

  try {
    if (coin && timeframe) {
      if (!isValidCoin(coin)) return invalidCoinResponse(coin);
      if (!isValidTimeframe(timeframe)) return invalidTimeframeResponse(timeframe);
      const [upData, downData] = await Promise.all([
        getOrderbookFullFromRedis(coin, timeframe, 'up'),
        getOrderbookFullFromRedis(coin, timeframe, 'down'),
      ]);

      return NextResponse.json({
        up: upData
          ? {
              timestamp: upData.timestamp,
              best_bid: upData.best_bid,
              best_ask: upData.best_ask,
              bids: upData.bids.map((b) => ({
                price: String(b.price),
                size: String(b.size),
              })),
              asks: upData.asks.map((a) => ({
                price: String(a.price),
                size: String(a.size),
              })),
            }
          : null,
        down: downData
          ? {
              timestamp: downData.timestamp,
              best_bid: downData.best_bid,
              best_ask: downData.best_ask,
              bids: downData.bids.map((b) => ({
                price: String(b.price),
                size: String(b.size),
              })),
              asks: downData.asks.map((a) => ({
                price: String(a.price),
                size: String(a.size),
              })),
            }
          : null,
      });
    }

    return NextResponse.json({ error: 'coin and timeframe required' }, { status: 400 });
  } catch (error) {
    console.error('Failed to fetch full orderbook from Redis:', error);
    return NextResponse.json({ error: 'Failed to fetch orderbook' }, { status: 500 });
  }
}
