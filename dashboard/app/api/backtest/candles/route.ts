import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const BACKTEST_API = process.env.BACKTEST_API_URL || 'http://localhost:3842';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const coin = searchParams.get('coin') || 'btc';
  const limit = Math.min(parseInt(searchParams.get('limit') || '20'), 100);
  const strategy = searchParams.get('strategy') || 'v12_5';

  try {
    const res = await fetch(
      `${BACKTEST_API}/candles?coin=${encodeURIComponent(coin)}&limit=${limit}&strategy=${encodeURIComponent(strategy)}`,
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
    console.error('Candles proxy error:', error);
    return NextResponse.json(
      { error: 'Backtest API unavailable', details: String(error) },
      { status: 502 }
    );
  }
}
