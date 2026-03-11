import { NextResponse } from 'next/server';
import {
  getCurrentCandleFromRedis,
  getAllCurrentCandlesFromRedis,
  getAllChainlinkCandlesFromRedis,
  getAllExchangePricesFromRedis,
} from '@/lib/redis';
import { isValidCoin, isValidTimeframe, invalidCoinResponse, invalidTimeframeResponse } from '@/lib/validation';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const coin = searchParams.get('coin');
  const timeframe = searchParams.get('timeframe');

  try {
    // If coin and timeframe provided, get specific current candle
    if (coin && timeframe) {
      if (!isValidCoin(coin)) return invalidCoinResponse(coin);
      if (!isValidTimeframe(timeframe)) return invalidTimeframeResponse(timeframe);
      const data = await getCurrentCandleFromRedis(coin, timeframe);
      return NextResponse.json(data || { error: 'No data found' });
    }

    // Otherwise return all current candles (all exchanges + chainlink + exchange prices)
    const [allCandles, chainlinkCandles, exchangePrices] = await Promise.all([
      getAllCurrentCandlesFromRedis(),
      getAllChainlinkCandlesFromRedis(),
      getAllExchangePricesFromRedis(),
    ]);

    // Separate candles by exchange
    // Keys: "btc_15m" (binance), "bybit:btc_15m" (other exchanges)
    const binanceCandles: Record<string, typeof allCandles[string]> = {};
    const bybitCandles: Record<string, typeof allCandles[string]> = {};
    const gateCandles: Record<string, typeof allCandles[string]> = {};
    const bitgetCandles: Record<string, typeof allCandles[string]> = {};

    for (const [key, value] of Object.entries(allCandles)) {
      if (key.startsWith('bybit:')) {
        bybitCandles[key.replace('bybit:', '')] = value;
      } else if (key.startsWith('gate:')) {
        gateCandles[key.replace('gate:', '')] = value;
      } else if (key.startsWith('bitget:')) {
        bitgetCandles[key.replace('bitget:', '')] = value;
      } else if (key.startsWith('binance:')) {
        binanceCandles[key.replace('binance:', '')] = value;
      } else {
        // No prefix = binance (legacy format)
        binanceCandles[key] = value;
      }
    }

    return NextResponse.json({
      binance: binanceCandles,
      bybit: bybitCandles,
      gate: gateCandles,
      bitget: bitgetCandles,
      chainlink: chainlinkCandles,
      exchangePrices: exchangePrices,
    });
  } catch (error) {
    console.error('Failed to fetch current candle from Redis:', error);
    return NextResponse.json({ error: 'Failed to fetch current candle' }, { status: 500 });
  }
}
