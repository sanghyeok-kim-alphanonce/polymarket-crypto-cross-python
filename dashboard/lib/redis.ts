import Redis from 'ioredis';

const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6380';

let redis: Redis | null = null;

export function getRedis(): Redis {
  if (!redis) {
    redis = new Redis(REDIS_URL, {
      maxRetriesPerRequest: 3,
      lazyConnect: true,
    });
  }
  return redis;
}

export interface OrderbookLevel {
  price: number;
  size: number;
}

export interface OrderbookSnapshot {
  timestamp: number;
  market_key: string;
  market_slug: string;
  token_id: string;
  best_bid: number;
  best_ask: number;
  asks: OrderbookLevel[];
  bids: OrderbookLevel[];
}

export async function getOrderbookFromRedis(
  coin: string,
  timeframe: string,
  side: string
): Promise<OrderbookSnapshot | null> {
  const redis = getRedis();
  const key = `orderbook:${coin}_${timeframe}_${side}`;

  try {
    const data = await redis.get(key);
    if (!data) return null;
    return JSON.parse(data) as OrderbookSnapshot;
  } catch (error) {
    console.error(`Failed to get orderbook from Redis: ${key}`, error);
    return null;
  }
}

export async function getAllOrderbooksFromRedis(): Promise<Record<string, OrderbookSnapshot>> {
  const redis = getRedis();
  const keys = await redis.keys('orderbook:*');

  const result: Record<string, OrderbookSnapshot> = {};

  for (const key of keys) {
    // orderbook_full: 키는 제외
    if (key.startsWith('orderbook_full:')) continue;
    const data = await redis.get(key);
    if (data) {
      const marketKey = key.replace('orderbook:', '');
      result[marketKey] = JSON.parse(data) as OrderbookSnapshot;
    }
  }

  return result;
}

export async function getOrderbookFullFromRedis(
  coin: string,
  timeframe: string,
  side: string
): Promise<OrderbookSnapshot | null> {
  const redis = getRedis();
  const key = `orderbook_full:${coin}_${timeframe}_${side}`;

  try {
    const data = await redis.get(key);
    if (!data) return null;
    return JSON.parse(data) as OrderbookSnapshot;
  } catch (error) {
    console.error(`Failed to get full orderbook from Redis: ${key}`, error);
    return null;
  }
}

// Current Candle types (OHLCV)
export interface CurrentCandle {
  coin: string;
  timeframe: string;
  direction: 'up' | 'down' | 'flat';
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  price_change_pct: number;
  candle_start: string;
  candle_end: string;
  updated_at: string;
}

export async function getCurrentCandleFromRedis(
  coin: string,
  timeframe: string
): Promise<CurrentCandle | null> {
  const redis = getRedis();
  const key = `current_candle:${coin}_${timeframe}`;

  try {
    const data = await redis.get(key);
    if (!data) return null;
    return JSON.parse(data) as CurrentCandle;
  } catch (error) {
    console.error(`Failed to get current candle from Redis: ${key}`, error);
    return null;
  }
}

export async function getAllCurrentCandlesFromRedis(): Promise<Record<string, CurrentCandle>> {
  const redis = getRedis();
  const keys = await redis.keys('current_candle:*');

  const result: Record<string, CurrentCandle> = {};

  for (const key of keys) {
    const data = await redis.get(key);
    if (data) {
      const marketKey = key.replace('current_candle:', '');
      result[marketKey] = JSON.parse(data) as CurrentCandle;
    }
  }

  return result;
}

// Chainlink Candle types
export interface ChainlinkCandle {
  coin: string;
  timeframe: string;
  direction: 'up' | 'down' | 'flat';
  open: number;
  high: number;
  low: number;
  close: number;
  price_change_pct: number;
  candle_start: string;
  candle_end: string;
  updated_at: string;
}

export async function getChainlinkCandleFromRedis(
  coin: string,
  timeframe: string = '15m'
): Promise<ChainlinkCandle | null> {
  const redis = getRedis();
  const key = `chainlink_candle:${coin}_${timeframe}`;

  try {
    const data = await redis.get(key);
    if (!data) return null;
    return JSON.parse(data) as ChainlinkCandle;
  } catch (error) {
    console.error(`Failed to get chainlink candle from Redis: ${key}`, error);
    return null;
  }
}

export async function getAllChainlinkCandlesFromRedis(): Promise<Record<string, ChainlinkCandle>> {
  const redis = getRedis();
  const keys = await redis.keys('chainlink_candle:*');

  const result: Record<string, ChainlinkCandle> = {};

  for (const key of keys) {
    const data = await redis.get(key);
    if (data) {
      const marketKey = key.replace('chainlink_candle:', '');
      result[marketKey] = JSON.parse(data) as ChainlinkCandle;
    }
  }

  return result;
}

// Exchange Price types
export interface ExchangePrice {
  exchange: string;
  symbol: string;
  coin: string;
  price: number;
  bid: number;
  ask: number;
  timestamp: number;
  datetime: string;
}

export async function getExchangePriceFromRedis(
  exchange: string,
  coin: string
): Promise<ExchangePrice | null> {
  const redis = getRedis();
  const key = `exchange_price:${exchange}:${coin}`;

  try {
    const data = await redis.get(key);
    if (!data) return null;
    return JSON.parse(data) as ExchangePrice;
  } catch (error) {
    console.error(`Failed to get exchange price from Redis: ${key}`, error);
    return null;
  }
}

export async function getAllExchangePricesFromRedis(): Promise<Record<string, ExchangePrice>> {
  const redis = getRedis();
  const keys = await redis.keys('exchange_price:*');

  const result: Record<string, ExchangePrice> = {};

  for (const key of keys) {
    const data = await redis.get(key);
    if (data) {
      // key: exchange_price:binance:btc -> binance:btc
      const marketKey = key.replace('exchange_price:', '');
      result[marketKey] = JSON.parse(data) as ExchangePrice;
    }
  }

  return result;
}
