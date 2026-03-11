'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

const COINS = ['btc', 'eth', 'sol', 'xrp'] as const;

interface CandleStat {
  candle_start: string;
  coin: string;
  up: number;
  down: number;
  total: number;
  up_avg_elapsed: number | null;
  down_avg_elapsed: number | null;
}

interface CoinStat {
  coin: string;
  up_total: number;
  down_total: number;
  total: number;
  candle_count: number;
  avg_per_candle: number;
}

interface CrossingData {
  coin: string;
  days: number;
  candleStats: CandleStat[];
  coinStats: CoinStat[];
}

export default function CrossingAnalysisPage() {
  const [days, setDays] = useState<number>(7);
  const [data, setData] = useState<CrossingData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/crossing-analysis?coin=all&days=${days}`);
      if (!res.ok) throw new Error('Failed to fetch data');
      const result = await res.json();
      setData(result);
    } catch (err) {
      setError('Failed to load data');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // 캔들별로 그룹핑 (각 코인 컬럼으로)
  const candleTable = useMemo(() => {
    if (!data?.candleStats) return [];

    const candleMap = new Map<string, { btc: CandleStat | null; eth: CandleStat | null; sol: CandleStat | null; xrp: CandleStat | null }>();

    for (const stat of data.candleStats) {
      if (!candleMap.has(stat.candle_start)) {
        candleMap.set(stat.candle_start, { btc: null, eth: null, sol: null, xrp: null });
      }
      const entry = candleMap.get(stat.candle_start)!;
      if (stat.coin === 'btc') entry.btc = stat;
      else if (stat.coin === 'eth') entry.eth = stat;
      else if (stat.coin === 'sol') entry.sol = stat;
      else if (stat.coin === 'xrp') entry.xrp = stat;
    }

    return Array.from(candleMap.entries())
      .map(([candle_start, coins]) => ({ candle_start, ...coins }))
      .sort((a, b) => new Date(b.candle_start).getTime() - new Date(a.candle_start).getTime());
  }, [data?.candleStats]);

  // 전체 통계 계산
  const totalStats = useMemo(() => {
    if (!data?.coinStats) return { total: 0, up: 0, down: 0, candles: 0 };
    return data.coinStats.reduce(
      (acc, s) => ({
        total: acc.total + s.total,
        up: acc.up + s.up_total,
        down: acc.down + s.down_total,
        candles: Math.max(acc.candles, s.candle_count),
      }),
      { total: 0, up: 0, down: 0, candles: 0 }
    );
  }, [data?.coinStats]);

  const renderCoinCell = (stat: CandleStat | null) => {
    if (!stat || stat.total === 0) return <span className="text-muted-foreground">-</span>;
    return (
      <span>
        <span className="text-green-500">{stat.up}</span>
        <span className="text-muted-foreground">/</span>
        <span className="text-red-500">{stat.down}</span>
        <span className="text-muted-foreground ml-1">({stat.total})</span>
      </span>
    );
  };

  return (
    <div className="min-h-screen p-4">
      <div className="max-w-6xl mx-auto">
        <h1 className="text-2xl font-bold mb-6">Crossing Analysis</h1>

        {/* Controls */}
        <Card className="mb-4">
          <CardContent className="pt-4 pb-4">
            <div className="flex flex-wrap gap-6 items-center">
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground font-medium">Days:</span>
                <ToggleGroup type="single" value={String(days)} onValueChange={(v) => v && setDays(parseInt(v))}>
                  {[1, 3, 7, 14, 30].map((d) => (
                    <ToggleGroupItem key={d} value={String(d)} className="text-xs">
                      {d}d
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              </div>

              {/* 전체 통계 */}
              <div className="flex items-center gap-4 text-sm">
                <span>
                  Total: <span className="font-bold">{totalStats.total}</span>
                </span>
                <span>
                  <span className="text-green-500">UP: {totalStats.up}</span>
                  {' / '}
                  <span className="text-red-500">DN: {totalStats.down}</span>
                </span>
                <span className="text-muted-foreground">
                  {totalStats.candles} candles
                </span>
              </div>

              <span className="text-sm text-muted-foreground ml-auto">Auto-refresh 30s</span>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="text-destructive mb-4 p-4 bg-destructive/10 rounded">{error}</div>
        )}

        {loading && !data && (
          <div className="text-center py-8 text-muted-foreground">Loading...</div>
        )}

        {data && (
          <>
            {/* 코인별 전체 통계 */}
            <div className="grid grid-cols-4 gap-4 mb-4">
              {COINS.map((coin) => {
                const stat = data.coinStats.find((s) => s.coin === coin);
                return (
                  <Card key={coin}>
                    <CardContent className="pt-4 pb-4">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-lg">{coin.toUpperCase()}</span>
                        <span className="text-2xl font-bold">{stat?.total ?? 0}</span>
                      </div>
                      <div className="text-xs text-muted-foreground mt-1">
                        <span className="text-green-500">↑{stat?.up_total ?? 0}</span>
                        {' / '}
                        <span className="text-red-500">↓{stat?.down_total ?? 0}</span>
                        <span className="ml-2">avg {(stat?.avg_per_candle ?? 0).toFixed(1)}/candle</span>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>

            {/* 캔들별 코인별 crossing 테이블 */}
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-base">캔들별 Crossing 횟수 (UP/DOWN)</CardTitle>
              </CardHeader>
              <CardContent className="pb-2">
                <div className="overflow-x-auto max-h-[60vh]">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-card">
                      <tr className="border-b border-border">
                        <th className="text-left py-2 px-3">Candle Start</th>
                        <th className="text-center py-2 px-3">BTC</th>
                        <th className="text-center py-2 px-3">ETH</th>
                        <th className="text-center py-2 px-3">SOL</th>
                        <th className="text-center py-2 px-3">XRP</th>
                      </tr>
                    </thead>
                    <tbody>
                      {candleTable.map((row) => (
                        <tr key={row.candle_start} className="border-b border-border/50 hover:bg-secondary/30">
                          <td className="py-2 px-3 font-mono text-xs">
                            {new Date(row.candle_start).toLocaleString('en-US', {
                              month: 'short',
                              day: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </td>
                          <td className="text-center py-2 px-3 font-mono">{renderCoinCell(row.btc)}</td>
                          <td className="text-center py-2 px-3 font-mono">{renderCoinCell(row.eth)}</td>
                          <td className="text-center py-2 px-3 font-mono">{renderCoinCell(row.sol)}</td>
                          <td className="text-center py-2 px-3 font-mono">{renderCoinCell(row.xrp)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
