import { NextResponse } from 'next/server';

export const VALID_COINS = ['btc', 'eth', 'sol', 'xrp'] as const;
export const VALID_TIMEFRAMES = ['5m', '15m', '1h', '4h'] as const;

export type Coin = (typeof VALID_COINS)[number];
export type Timeframe = (typeof VALID_TIMEFRAMES)[number];

export function isValidCoin(coin: string): coin is Coin {
  return (VALID_COINS as readonly string[]).includes(coin);
}

export function isValidTimeframe(tf: string): tf is Timeframe {
  return (VALID_TIMEFRAMES as readonly string[]).includes(tf);
}

export function invalidCoinResponse(coin: string): NextResponse {
  return NextResponse.json(
    { error: `Invalid coin '${coin}'. Must be one of: ${VALID_COINS.join(', ')}` },
    { status: 400 },
  );
}

export function invalidTimeframeResponse(tf: string): NextResponse {
  return NextResponse.json(
    { error: `Invalid timeframe '${tf}'. Must be one of: ${VALID_TIMEFRAMES.join(', ')}` },
    { status: 400 },
  );
}
