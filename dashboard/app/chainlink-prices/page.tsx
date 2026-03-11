'use client';

import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Activity } from 'lucide-react';

interface HistoricalPoint {
  time: string;
  price: number;
  source: string | null;
}

interface ChainlinkData {
  latest: {
    [coin: string]: {
      price: number;
      time: string;
    };
  };
  historical: {
    [coin: string]: HistoricalPoint[];
  };
  timestamp: number;
  source: string;
}

const COINS = ['btc', 'eth', 'sol', 'xrp'];
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

const formatPrice = (price: number) => {
  if (price >= 1000) {
    return `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  return `$${price.toFixed(4)}`;
};

export default function ChainlinkPricesPage() {
  const [data, setData] = useState<ChainlinkData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState('24h');

  const fetchData = async () => {
    try {
      const response = await fetch(`/api/chainlink-prices?timeRange=${timeRange}`);
      if (!response.ok) {
        throw new Error('Failed to fetch data');
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
    fetchData();
    const interval = setInterval(fetchData, 30000); // 30초마다
    return () => clearInterval(interval);
  }, [timeRange]);

  if (loading) {
    return (
      <div className="min-h-screen bg-background p-8">
        <div className="max-w-7xl mx-auto">
          <div className="animate-pulse">
            <div className="h-8 bg-muted rounded w-1/4 mb-8"></div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="h-96 bg-muted rounded"></div>
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
        <div className="max-w-7xl mx-auto">
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
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Chainlink Price Feeds (DB)</h1>
          <div className="flex gap-2">
            {['24h', 'all'].map((range) => (
              <button
                key={range}
                onClick={() => setTimeRange(range)}
                className={`px-3 py-1 text-sm rounded ${
                  timeRange === range
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-muted-foreground hover:bg-muted/80'
                }`}
              >
                {range}
              </button>
            ))}
            <button
              onClick={fetchData}
              disabled={loading}
              className="bg-primary text-primary-foreground px-4 py-1 rounded text-sm hover:opacity-90 disabled:opacity-50 ml-2"
            >
              Refresh
            </button>
          </div>
        </div>

        {/* Latest Prices */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {COINS.map((coin) => {
            const latest = data?.latest[coin];
            return (
              <Card key={coin}>
                <CardContent className="pt-4 pb-4">
                  <div className="flex items-center gap-2 mb-2">
                    <div
                      className="w-3 h-3 rounded-full"
                      style={{ backgroundColor: COIN_COLORS[coin] }}
                    />
                    <span className="uppercase font-bold">{coin}</span>
                  </div>
                  <div className="font-mono text-xl font-bold text-orange-400">
                    {latest ? formatPrice(latest.price) : '-'}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {latest ? new Date(latest.time).toLocaleTimeString() : '-'}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>

        {/* Historical Data Tables */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {COINS.map((coin) => {
            const history = data?.historical[coin] || [];
            const latest = history[history.length - 1];

            return (
              <Card key={coin}>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <div
                      className="w-4 h-4 rounded-full"
                      style={{ backgroundColor: COIN_COLORS[coin] }}
                    />
                    <span className="uppercase">{coin}</span>
                    <span className="text-sm font-normal text-muted-foreground">
                      {COIN_NAMES[coin]}
                    </span>
                    {latest && (
                      <span className="ml-auto text-lg font-bold">
                        {formatPrice(latest.price)}
                      </span>
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="text-xs text-muted-foreground mb-2 text-center">
                    Historical prices (last {timeRange})
                  </div>
                  <div className="max-h-72 overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-card border-b">
                        <tr>
                          <th className="text-left p-2">Time</th>
                          <th className="text-right p-2">Price</th>
                          <th className="text-right p-2">Source</th>
                        </tr>
                      </thead>
                      <tbody className="font-mono">
                        {history.slice().reverse().slice(0, 50).map((point, idx) => {
                          const prevPrice = history[history.length - 1 - idx - 1]?.price;
                          const change = prevPrice ? point.price - prevPrice : 0;
                          const changeColor = change > 0 ? 'text-green-500' : change < 0 ? 'text-red-500' : '';

                          return (
                            <tr
                              key={idx}
                              className="border-b hover:bg-muted/50 transition-colors"
                            >
                              <td className="p-2">
                                {new Date(point.time).toLocaleString('en-US', {
                                  month: 'short',
                                  day: 'numeric',
                                  hour: '2-digit',
                                  minute: '2-digit',
                                })}
                              </td>
                              <td className={`p-2 text-right ${changeColor}`}>
                                {formatPrice(point.price)}
                              </td>
                              <td className="p-2 text-right text-xs text-muted-foreground">
                                {point.source ?? '--'}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground text-center">
                    {history.length} records
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>

        {/* Info Footer */}
        <div className="mt-8 text-center text-sm text-muted-foreground border-t pt-4">
          <p className="flex items-center justify-center gap-2">
            <Activity className="h-4 w-4" />
            Source: {data?.source} | Last updated:{' '}
            {data?.timestamp ? new Date(data.timestamp).toLocaleString() : 'N/A'}
          </p>
          <p className="mt-1 text-xs">Data from coin_prices table (Chainlink collector)</p>
        </div>
      </div>
    </div>
  );
}
