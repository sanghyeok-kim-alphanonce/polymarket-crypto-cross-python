'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface OHLCVData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  numTrades: number;
}

interface OHLCVChartProps {
  coin: string;
  timeframe?: string;
}

export function OHLCVChart({ coin, timeframe = '15m' }: OHLCVChartProps) {
  const [data, setData] = useState<OHLCVData[]>([]);
  const [loading, setLoading] = useState(true);

  // Fetch data at the start of each minute (1 second after the minute)
  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await fetch(`/api/ohlcv?coin=${coin}&timeframe=${timeframe}`, {
          cache: 'no-store',
        });
        const result = await response.json();
        setData(result.data || []);
        setLoading(false);
      } catch (error) {
        console.error('Error fetching OHLCV data:', error);
        setLoading(false);
      }
    };

    // Calculate milliseconds until next minute + 1 second
    const getMillisUntilNextMinute = () => {
      const now = new Date();
      const seconds = now.getSeconds();
      const millis = now.getMilliseconds();
      // Wait until 1 second after the next minute starts
      const msUntilNextMinute = (60 - seconds) * 1000 - millis;
      return msUntilNextMinute + 1000; // +1 second after minute start
    };

    // Initial fetch
    fetchData();

    // Schedule next fetch at minute boundary + 1 second
    let timeoutId: NodeJS.Timeout;
    const scheduleNextFetch = () => {
      const msToWait = getMillisUntilNextMinute();
      timeoutId = setTimeout(() => {
        fetchData();
        scheduleNextFetch(); // Schedule next one
      }, msToWait);
    };

    scheduleNextFetch();

    return () => {
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [coin, timeframe]);

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{coin.toUpperCase()} - {timeframe}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center h-[400px]">
            <p className="text-muted-foreground">Loading data...</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (data.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{coin.toUpperCase()} - {timeframe}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center h-[400px]">
            <p className="text-muted-foreground">No data available</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const latestData = data[data.length - 1];
  const firstData = data[0];
  const priceChange = (latestData.close ?? 0) - (firstData.open ?? 0);
  const priceChangePercent = firstData.open ? (priceChange / firstData.open) * 100 : 0;

  return (
    <Card>
      <CardHeader>
        <div className="flex justify-between items-start">
          <div>
            <CardTitle>{coin.toUpperCase()} - {timeframe}</CardTitle>
            <div className="flex items-baseline gap-2 mt-2">
              <span className="text-2xl font-bold">
                ${(latestData.close ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
              <span className={`text-sm font-medium ${priceChange >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(2)} ({priceChangePercent >= 0 ? '+' : ''}{priceChangePercent.toFixed(2)}%)
              </span>
            </div>
          </div>
          <div className="text-right text-sm text-muted-foreground">
            <div>Total Vol: {data.reduce((sum, d) => sum + (d.volume ?? 0), 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</div>
            <div>{data.length} candles</div>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-auto max-h-[600px] border rounded-lg">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-secondary border-b">
              <tr>
                <th className="text-left p-2 font-semibold">Time</th>
                <th className="text-right p-2 font-semibold">Open</th>
                <th className="text-right p-2 font-semibold">High</th>
                <th className="text-right p-2 font-semibold">Low</th>
                <th className="text-right p-2 font-semibold">Close</th>
                <th className="text-right p-2 font-semibold">Volume</th>
                <th className="text-right p-2 font-semibold">Trades</th>
              </tr>
            </thead>
            <tbody>
              {data.slice().reverse().map((candle, idx) => {
                const isGreen = (candle.close ?? 0) >= (candle.open ?? 0);
                return (
                  <tr key={idx} className="border-b hover:bg-secondary/50">
                    <td className="p-2 font-mono text-xs text-muted-foreground">
                      {new Date(candle.time).toLocaleString('en-US', {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                        hour12: false,
                      })}
                    </td>
                    <td className="p-2 text-right font-mono">
                      ${(candle.open ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="p-2 text-right font-mono text-green-400">
                      ${(candle.high ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="p-2 text-right font-mono text-red-400">
                      ${(candle.low ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className={`p-2 text-right font-mono font-semibold ${isGreen ? 'text-green-400' : 'text-red-400'}`}>
                      ${(candle.close ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="p-2 text-right font-mono text-muted-foreground">
                      {(candle.volume ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                    <td className="p-2 text-right font-mono text-muted-foreground">
                      {(candle.numTrades ?? 0).toLocaleString()}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
