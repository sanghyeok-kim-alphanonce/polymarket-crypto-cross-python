import { NextResponse } from 'next/server';
import { getOrderbookFromRedis, getAllOrderbooksFromRedis } from '@/lib/redis';
import { isValidCoin, isValidTimeframe, invalidCoinResponse, invalidTimeframeResponse } from '@/lib/validation';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const coin = searchParams.get('coin');
  const timeframe = searchParams.get('timeframe');

  try {
    // If coin and timeframe provided, get both up and down for that market
    if (coin && timeframe) {
      if (!isValidCoin(coin)) return invalidCoinResponse(coin);
      if (!isValidTimeframe(timeframe)) return invalidTimeframeResponse(timeframe);
      const [upData, downData] = await Promise.all([
        getOrderbookFromRedis(coin, timeframe, 'up'),
        getOrderbookFromRedis(coin, timeframe, 'down'),
      ]);

      return NextResponse.json({
        up: upData
          ? {
              timestamp: upData.timestamp,
              market_slug: upData.market_slug,
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
              market_slug: downData.market_slug,
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

    // Otherwise return all orderbooks
    const allOrderbooks = await getAllOrderbooksFromRedis();
    return NextResponse.json(allOrderbooks);
  } catch (error) {
    console.error('Failed to fetch orderbook from Redis:', error);
    return NextResponse.json({ error: 'Failed to fetch orderbook' }, { status: 500 });
  }
}
