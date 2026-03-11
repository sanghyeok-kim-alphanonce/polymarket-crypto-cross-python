'use client';

import { useEffect, useState, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Brush,
  ReferenceLine,
} from 'recharts';

const COINS = ['btc', 'eth', 'sol', 'xrp'] as const;

interface ChartDataPoint {
  ts: number;
  elapsedSec: number;
  binance: number | null;
  chainlink: number | null;
  upAsk: number | null;
  downAsk: number | null;
  delta1s: number | null;
}

export default function AnalysisPage() {
  const [selectedCoin, setSelectedCoin] = useState<string>('btc');
  const [chartData, setChartData] = useState<ChartDataPoint[]>([]);
  const [candleStart, setCandleStart] = useState<Date | null>(null);
  const [candleEnd, setCandleEnd] = useState<Date | null>(null);
  const [marketSlug, setMarketSlug] = useState<string | null>(null);
  const [obSnapshotCount, setObSnapshotCount] = useState<number>(0);
  const [chainlinkStartPrice, setChainlinkStartPrice] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [brushRange, setBrushRange] = useState<{ startIndex: number; endIndex: number } | null>(null);
  const [deltaThreshold, setDeltaThreshold] = useState<number>(50);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/analysis/unified?coin=${selectedCoin}&offset=${offset}`);
      if (!res.ok) throw new Error('Failed to fetch data');

      const result = await res.json();

      setCandleStart(new Date(result.candleStart));
      setCandleEnd(new Date(result.candleEnd));
      setMarketSlug(result.marketSlug ?? null);
      setObSnapshotCount(result.obSnapshotCount ?? 0);
      setChainlinkStartPrice(result.chainlinkStartPrice);

      if (result.dataPoints === 0) {
        setChartData([]);
        return;
      }

      const startTime = new Date(result.candleStart).getTime();

      const rawData = result.data as Array<{ ts: number; binance: number | null; chainlink: number | null; upAsk: number | null; downAsk: number | null }>;

      const transformed: ChartDataPoint[] = rawData.map((d, i) => {
        const prevBinance = i > 0 ? rawData[i - 1].binance : null;
        const delta1s = (d.binance !== null && prevBinance !== null)
          ? d.binance - prevBinance
          : null;

        return {
          ts: d.ts,
          elapsedSec: Math.round((d.ts * 1000 - startTime) / 1000),
          binance: d.binance,
          chainlink: d.chainlink,
          upAsk: d.upAsk,
          downAsk: d.downAsk,
          delta1s,
        };
      });

      setChartData(transformed);
      setBrushRange(null);
    } catch (err) {
      setError('Failed to load data');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [selectedCoin, offset]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleBrushChange = (range: { startIndex?: number; endIndex?: number }) => {
    if (range.startIndex !== undefined && range.endIndex !== undefined) {
      setBrushRange({ startIndex: range.startIndex, endIndex: range.endIndex });
    }
  };

  const visibleData = brushRange
    ? chartData.slice(brushRange.startIndex, brushRange.endIndex + 1)
    : chartData;

  const priceValues = visibleData
    .flatMap(d => [d.binance, d.chainlink])
    .filter((v): v is number => v !== null);

  const priceMin = priceValues.length > 0 ? Math.min(...priceValues) * 0.999 : 0;
  const priceMax = priceValues.length > 0 ? Math.max(...priceValues) * 1.001 : 100;

  const hasOrderbookData = chartData.some(d => d.upAsk !== null || d.downAsk !== null);

  // Delta threshold 초과 시점 필터링
  const deltaSpikes = chartData.filter(d =>
    d.delta1s !== null && Math.abs(d.delta1s) >= deltaThreshold
  );

  const formatPrice = (value: number) => {
    if (selectedCoin === 'xrp') return `$${value.toFixed(4)}`;
    if (selectedCoin === 'sol') return `$${value.toFixed(2)}`;
    return `$${value.toLocaleString()}`;
  };

  const formatElapsed = (v: number) => `${Math.floor(v / 60)}:${String(v % 60).padStart(2, '0')}`;

  return (
    <div className="min-h-screen p-4">
      <div className="max-w-[95vw] mx-auto">
        <h1 className="text-2xl font-bold mb-6">Price Analysis (Synchronized)</h1>

        {/* Controls */}
        <Card className="mb-4">
          <CardContent className="pt-4 pb-4">
            <div className="flex flex-wrap gap-6 items-center">
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground font-medium">Coin:</span>
                <ToggleGroup type="single" value={selectedCoin} onValueChange={(v) => v && setSelectedCoin(v)}>
                  {COINS.map((coin) => (
                    <ToggleGroupItem key={coin} value={coin} className="text-xs">
                      {coin.toUpperCase()}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => setOffset(offset - 1)}
                  disabled={loading}
                  className="px-3 py-1.5 text-sm font-medium rounded-md border bg-background hover:bg-secondary disabled:opacity-50"
                >
                  Prev
                </button>
                <span className="text-sm text-muted-foreground min-w-[120px] text-center">
                  {offset === 0 ? '이전 완료 구간' : offset === 1 ? '현재 구간' : `${Math.abs(offset)}개 전 구간`}
                </span>
                <button
                  onClick={() => setOffset(offset + 1)}
                  disabled={loading || offset >= 1}
                  className="px-3 py-1.5 text-sm font-medium rounded-md border bg-background hover:bg-secondary disabled:opacity-50"
                >
                  Next
                </button>
              </div>

              {candleStart && candleEnd && (
                <div className="text-xs text-muted-foreground">
                  {candleStart.toLocaleString('ko-KR')} ~ {candleEnd.toLocaleTimeString('ko-KR')}
                </div>
              )}

              <div className="text-xs text-muted-foreground">
                {chartData.length} data points
                {brushRange && ` (viewing ${brushRange.endIndex - brushRange.startIndex + 1})`}
              </div>

              {marketSlug && (
                <div className="flex items-center gap-2">
                  <code className="text-xs bg-secondary px-2 py-1 rounded font-mono">{marketSlug}</code>
                  <span className={`text-xs px-1.5 py-0.5 rounded ${obSnapshotCount > 0 ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'}`}>
                    {obSnapshotCount > 0 ? `${obSnapshotCount.toLocaleString()} snapshots` : 'no OB data'}
                  </span>
                </div>
              )}

              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground font-medium">Delta 1s &ge;</span>
                <input
                  type="number"
                  value={deltaThreshold}
                  onChange={(e) => setDeltaThreshold(Number(e.target.value) || 0)}
                  className="w-20 px-2 py-1 text-sm rounded border bg-background"
                  step={10}
                  min={0}
                />
                <span className="text-xs text-muted-foreground">USD</span>
                {deltaSpikes.length > 0 && (
                  <span className="text-xs px-2 py-0.5 rounded bg-yellow-900/50 text-yellow-400">
                    {deltaSpikes.length} spikes
                  </span>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="text-destructive mb-4 p-4 bg-destructive/10 rounded">{error}</div>
        )}

        {loading && (
          <div className="text-center py-8 text-muted-foreground">Loading...</div>
        )}

        {!loading && chartData.length === 0 && !error && (
          <div className="text-center py-8 text-muted-foreground">
            No data for this candle window. Try a different offset.
          </div>
        )}

        {!loading && chartData.length > 0 && (
          <>
            {/* Chart 1: Price - Binance vs Chainlink */}
            <Card className="mb-4">
              <CardHeader className="py-3">
                <CardTitle className="text-base">
                  Price: Binance vs Chainlink ({selectedCoin.toUpperCase()})
                </CardTitle>
              </CardHeader>
              <CardContent className="pb-2">
                <ResponsiveContainer width="100%" height={350}>
                  <LineChart data={chartData} syncId="analysis">
                    <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                    <XAxis
                      dataKey="elapsedSec"
                      tick={{ fontSize: 10 }}
                      tickFormatter={formatElapsed}
                    />
                    <YAxis
                      tick={{ fontSize: 10 }}
                      domain={[priceMin, priceMax]}
                      tickFormatter={formatPrice}
                      width={80}
                    />
                    <Tooltip
                      contentStyle={{ backgroundColor: 'rgba(0, 0, 0, 0.9)', border: '1px solid #444' }}
                      labelStyle={{ color: '#aaa' }}
                      labelFormatter={(v) => `${formatElapsed(v as number)} elapsed`}
                      formatter={(value: number, name: string) => [formatPrice(value), name]}
                    />
                    <Legend />
                    {chainlinkStartPrice && (
                      <ReferenceLine
                        y={chainlinkStartPrice}
                        stroke="#22c55e"
                        strokeDasharray="5 5"
                        strokeWidth={2}
                        label={{ value: `CL Start: ${formatPrice(chainlinkStartPrice)}`, position: 'right', fill: '#22c55e', fontSize: 10 }}
                      />
                    )}
                    {/* Delta spikes - vertical lines */}
                    {deltaSpikes.map((spike) => (
                      <ReferenceLine
                        key={spike.ts}
                        x={spike.elapsedSec}
                        stroke={spike.delta1s! > 0 ? '#22c55e' : '#ef4444'}
                        strokeWidth={2}
                        strokeOpacity={0.7}
                      />
                    ))}
                    <Line type="monotone" dataKey="binance" stroke="#3b82f6" name="Binance" dot={false} strokeWidth={1.5} connectNulls />
                    <Line type="monotone" dataKey="chainlink" stroke="#f59e0b" name="Chainlink" dot={{ r: 3 }} strokeWidth={2} connectNulls />
                    <Brush
                      dataKey="elapsedSec"
                      height={30}
                      stroke="#666"
                      fill="#1a1a1a"
                      onChange={handleBrushChange}
                      tickFormatter={formatElapsed}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>

            {/* Chart 2: Orderbook Ask Prices (UP / DOWN) */}
            <Card className="mb-4">
              <CardHeader className="py-3">
                <CardTitle className="text-base">
                  Orderbook Ask Price: UP vs DOWN ({selectedCoin.toUpperCase()})
                </CardTitle>
              </CardHeader>
              <CardContent className="pb-2">
                {hasOrderbookData ? (
                  <ResponsiveContainer width="100%" height={250}>
                    <LineChart data={chartData} syncId="analysis">
                      <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                      <XAxis
                        dataKey="elapsedSec"
                        tick={{ fontSize: 10 }}
                        tickFormatter={formatElapsed}
                      />
                      <YAxis
                        tick={{ fontSize: 10 }}
                        domain={[0, 1]}
                        ticks={[0, 0.25, 0.5, 0.75, 1]}
                        tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                        width={40}
                      />
                      <Tooltip
                        contentStyle={{ backgroundColor: 'rgba(0, 0, 0, 0.9)', border: '1px solid #444' }}
                        labelStyle={{ color: '#aaa' }}
                        labelFormatter={(v) => `${formatElapsed(v as number)} elapsed`}
                        formatter={(value: number, name: string) => [`${(value * 100).toFixed(1)}%`, name]}
                      />
                      <Legend />
                      <ReferenceLine y={0.5} stroke="#666" strokeDasharray="5 5" />
                      <ReferenceLine y={0.45} stroke="#444" strokeDasharray="2 4" label={{ value: '0.45', position: 'left', fill: '#666', fontSize: 9 }} />
                      <ReferenceLine y={0.55} stroke="#444" strokeDasharray="2 4" label={{ value: '0.55', position: 'left', fill: '#666', fontSize: 9 }} />
                      {/* Delta spikes - vertical lines */}
                      {deltaSpikes.map((spike) => (
                        <ReferenceLine
                          key={spike.ts}
                          x={spike.elapsedSec}
                          stroke={spike.delta1s! > 0 ? '#22c55e' : '#ef4444'}
                          strokeWidth={2}
                          strokeOpacity={0.7}
                        />
                      ))}
                      <Line type="monotone" dataKey="upAsk" stroke="#22c55e" name="UP Ask" dot={false} strokeWidth={1.5} connectNulls />
                      <Line type="monotone" dataKey="downAsk" stroke="#ef4444" name="DOWN Ask" dot={false} strokeWidth={1.5} connectNulls />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="text-center py-8 text-muted-foreground text-sm">
                    Orderbook data not available for this candle. (orderbook_books DB 저장 필요)
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Delta Spikes Table */}
            {deltaSpikes.length > 0 && (
              <Card className="mb-4">
                <CardHeader className="py-3">
                  <CardTitle className="text-base">
                    Delta Spikes (|delta| &ge; ${deltaThreshold})
                  </CardTitle>
                </CardHeader>
                <CardContent className="pb-2">
                  <div className="max-h-48 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-card">
                        <tr className="border-b border-border">
                          <th className="text-left py-2 px-2">Time</th>
                          <th className="text-right py-2 px-2">Elapsed</th>
                          <th className="text-right py-2 px-2">Price</th>
                          <th className="text-right py-2 px-2">Delta</th>
                        </tr>
                      </thead>
                      <tbody>
                        {deltaSpikes.map((spike) => (
                          <tr key={spike.ts} className="border-b border-border/50 hover:bg-secondary/30">
                            <td className="py-1.5 px-2 font-mono">
                              {new Date(spike.ts * 1000).toLocaleTimeString('ko-KR')}
                            </td>
                            <td className="text-right py-1.5 px-2 font-mono">
                              {formatElapsed(spike.elapsedSec)}
                            </td>
                            <td className="text-right py-1.5 px-2 font-mono">
                              {spike.binance !== null ? formatPrice(spike.binance) : '-'}
                            </td>
                            <td className={`text-right py-1.5 px-2 font-mono font-bold ${
                              spike.delta1s! > 0 ? 'text-green-500' : 'text-red-500'
                            }`}>
                              {spike.delta1s! > 0 ? '+' : ''}{spike.delta1s!.toFixed(2)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            )}

            <div className="text-xs text-muted-foreground text-center mt-4">
              차트 하단의 브러시를 드래그하여 줌 - 모든 차트가 동기화됩니다
            </div>
          </>
        )}
      </div>
    </div>
  );
}
