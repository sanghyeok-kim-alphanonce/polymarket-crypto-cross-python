import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const BACKTEST_API = process.env.BACKTEST_API_URL || 'http://localhost:3842';
const VALID_STRATEGIES = ['v12_5', 'v12_6', 'crossing'];

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const coin = searchParams.get('coin') || 'btc';
  const slug = searchParams.get('market_slug');
  const strategy = searchParams.get('strategy') || 'v12_5';

  if (!slug) {
    return NextResponse.json(
      { error: 'market_slug parameter is required' },
      { status: 400 }
    );
  }

  if (!VALID_STRATEGIES.includes(strategy)) {
    return NextResponse.json(
      { error: `Invalid strategy: ${strategy}. Valid: ${VALID_STRATEGIES.join(', ')}` },
      { status: 400 }
    );
  }

  try {
    const res = await fetch(
      `${BACKTEST_API}/run?coin=${encodeURIComponent(coin)}&market_slug=${encodeURIComponent(slug)}&strategy=${encodeURIComponent(strategy)}`,
      { cache: 'no-store' }
    );

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json(
        { error: `Backtest API error: ${res.status}`, details: text },
        { status: res.status }
      );
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error('Backtest proxy error:', error);
    return NextResponse.json(
      { error: 'Backtest API unavailable', details: String(error) },
      { status: 502 }
    );
  }
}
