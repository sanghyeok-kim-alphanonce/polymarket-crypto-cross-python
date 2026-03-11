'use client';

import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Activity, TrendingUp, TrendingDown } from 'lucide-react';

interface CoinPrice {
  binance: number | null;
  chainlink: number | null;
  spread: number | null;
  spreadPercent: number | null;
}

interface PricesData {
  prices: {
    btc: CoinPrice;
    eth: CoinPrice;
    sol: CoinPrice;
    xrp: CoinPrice;
  };
  timestamp: number;
  source: string;
}

const COINS = ['btc', 'eth', 'sol', 'xrp'] as const;
const COIN_NAMES: { [key: string]: string } = {
  btc: 'Bitcoin',
  eth: 'Ethereum',
  sol: 'Solana',
  xrp: 'Ripple',
};

const COIN_COLORS: { [key: string]: string } = {
  btc: '#F7931A',
  eth: '#627EEA',
  sol: '#14F195',
  xrp: '#23292F',
};

const formatPrice = (price: number | null) => {
  if (price === null) return '-';
  if (price >= 1000) {
    return `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  return `$${price.toFixed(4)}`;
};

const formatSpread = (spread: number | null) => {
  if (spread === null) return '-';
  const prefix = spread > 0 ? '+' : '';
  return `${prefix}$${spread.toFixed(2)}`;
};

const formatSpreadPercent = (percent: number | null) => {
  if (percent === null) return '-';
  const prefix = percent > 0 ? '+' : '';
  return `${prefix}${percent.toFixed(4)}%`;
};

export default function PricesPage() {
  const [data, setData] = useState<PricesData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPrices = async () => {
    try {
      const response = await fetch('/api/prices');
      if (!response.ok) {
        throw new Error('Failed to fetch prices');
      }
      const result = await response.json();
      setData(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPrices();
    const interval = setInterval(fetchPrices, 5000); // 5초마다 갱신
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-background p-8">
        <div className="max-w-4xl mx-auto">
          <div className="animate-pulse">
            <div className="h-8 bg-muted rounded w-1/4 mb-8"></div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="h-40 bg-muted rounded"></div>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-background p-8">
        <div className="max-w-4xl mx-auto">
          <div className="text-center text-destructive">
            <p className="text-xl font-semibold">Error loading prices</p>
            <p className="text-sm mt-2">{error}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background p-4">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Real-time Prices</h1>
          <button
            onClick={fetchPrices}
            disabled={loading}
            className="bg-primary text-primary-foreground px-4 py-1.5 rounded text-sm hover:opacity-90 disabled:opacity-50"
          >
            Refresh
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {COINS.map((coin) => {
            const coinData = data?.prices[coin];
            if (!coinData) return null;

            const spreadColor =
              coinData.spreadPercent === null ? 'text-muted-foreground' :
              coinData.spreadPercent > 0 ? 'text-green-500' :
              coinData.spreadPercent < 0 ? 'text-red-500' : 'text-muted-foreground';

            return (
              <Card key={coin}>
                <CardHeader className="py-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <div
                      className="w-3 h-3 rounded-full"
                      style={{ backgroundColor: COIN_COLORS[coin] }}
                    />
                    <span className="uppercase font-bold">{coin}</span>
                    <span className="text-sm font-normal text-muted-foreground">
                      {COIN_NAMES[coin]}
                    </span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-0 pb-4">
                  <div className="space-y-2">
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-muted-foreground">Binance</span>
                      <span className="font-mono font-bold text-blue-400">
                        {formatPrice(coinData.binance)}
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-muted-foreground">Chainlink</span>
                      <span className="font-mono font-bold text-orange-400">
                        {formatPrice(coinData.chainlink)}
                      </span>
                    </div>
                    <div className="border-t pt-2 mt-2">
                      <div className="flex justify-between items-center">
                        <span className="text-sm text-muted-foreground">Spread</span>
                        <div className={`flex items-center gap-1 font-mono ${spreadColor}`}>
                          {coinData.spreadPercent && coinData.spreadPercent > 0 ? (
                            <TrendingUp className="h-3 w-3" />
                          ) : coinData.spreadPercent && coinData.spreadPercent < 0 ? (
                            <TrendingDown className="h-3 w-3" />
                          ) : null}
                          <span>{formatSpread(coinData.spread)}</span>
                          <span className="text-xs">({formatSpreadPercent(coinData.spreadPercent)})</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>

        {/* Info Footer */}
        <div className="mt-6 text-center text-sm text-muted-foreground border-t pt-4">
          <p className="flex items-center justify-center gap-2">
            <Activity className="h-4 w-4" />
            Source: {data?.source} | Last updated:{' '}
            {data?.timestamp ? new Date(data.timestamp).toLocaleString() : 'N/A'}
          </p>
          <p className="mt-1 text-xs">Spread = Chainlink - Binance (양수면 Chainlink가 더 높음)</p>
        </div>
      </div>
    </div>
  );
}
