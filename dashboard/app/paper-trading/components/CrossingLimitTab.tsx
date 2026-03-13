'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';

interface CrossingInfo {
  direction: string;
  candle_open: number;
  prev_price: number;
  current_price: number;
  elapsed_seconds: number;
}

interface CrossingTrade {
  id: number;
  time: string;
  coin: string;
  mid_price: number;
  up_mid_price: number | null;
  down_mid_price: number | null;
  side: string;
  order_price: number;
  fill_price: number | null;
  pnl: number | null;
  outcome: string | null;
  market_result: string | null;
  status: string;
  candle_start_time: string | null;
  contracts: number | null;
  cost: number | null;
  reason?: string | null;
  orderbook_snapshot?: {
    crossing?: CrossingInfo;
  };
}

interface CandleStat {
  candle_start_time: string;
  side: string;
  count: number;
  total_contracts: number;
  total_cost: number;
  avg_entry_price: number;
  pnl: number;
  outcomes: string | null;
}

interface CrossingData {
  trades: CrossingTrade[];
  overall: {
    total: number;
    pending: number;
    filled: number;
    closed: number;
    wins: number;
    losses: number;
    paper_pnl: number;
    win_rate: number;
  };
  candleStats: CandleStat[];
}

interface Props {
  strategyName: string;
  timeframe: '5m' | '15m';
  candleMinutes: number;
  maxCount: number;
}

export default function CrossingLimitTab({ strategyName, timeframe, candleMinutes, maxCount }: Props) {
  const [data, setData] = useState<CrossingData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedCandle, setSelectedCandle] = useState<string | null>(null);
  const [candleTrades, setCandleTrades] = useState<CrossingTrade[]>([]);
  const [candleTradesLoading, setCandleTradesLoading] = useState(false);

  const coinFilter = 'btc'; // BTC only

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`/api/paper-trading-hft?coin=${coinFilter}&strategy=${strategyName}`);
      if (!res.ok) throw new Error('Failed to fetch');
      const json = await res.json();
      setData(json);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [strategyName]);

  const fetchCandleTrades = useCallback(async (candleTime: string) => {
    setCandleTradesLoading(true);
    try {
      const res = await fetch(
        `/api/paper-trading-hft?coin=${coinFilter}&strategy=${strategyName}&candle=${encodeURIComponent(candleTime)}`
      );
      if (!res.ok) throw new Error('Failed to fetch candle trades');
      const json = await res.json();
      setCandleTrades(json.trades || []);
    } catch (err) {
      console.error(err);
      setCandleTrades([]);
    } finally {
      setCandleTradesLoading(false);
    }
  }, [strategyName]);

  useEffect(() => {
    setLoading(true);
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  useEffect(() => {
    if (selectedCandle) {
      fetchCandleTrades(selectedCandle);
    } else {
      setCandleTrades([]);
    }
  }, [selectedCandle, fetchCandleTrades]);

  const currentCandleTime = useMemo(() => {
    const now = new Date();
    const totalMinutes = now.getUTCHours() * 60 + now.getUTCMinutes();
    const candleIndex = Math.floor(totalMinutes / candleMinutes);
    const candleStartMinute = candleIndex * candleMinutes;
    const candleStart = new Date(now);
    candleStart.setUTCHours(Math.floor(candleStartMinute / 60), candleStartMinute % 60, 0, 0);
    return candleStart.toISOString();
  }, [data, candleMinutes]);

  const candleGroups = useMemo(() => {
    const stats = data?.candleStats || [];
    const groups = stats.reduce((acc, stat) => {
      const key = stat.candle_start_time;
      if (!acc[key]) {
        acc[key] = { up: null, down: null };
      }
      if (stat.side === 'UP') {
        acc[key].up = stat;
      } else if (stat.side === 'DOWN') {
        acc[key].down = stat;
      }
      return acc;
    }, {} as Record<string, { up: CandleStat | null; down: CandleStat | null }>);

    if (currentCandleTime && !groups[currentCandleTime]) {
      groups[currentCandleTime] = { up: null, down: null };
    }

    return groups;
  }, [data?.candleStats, currentCandleTime]);

  useEffect(() => {
    if (currentCandleTime && selectedCandle !== currentCandleTime) {
      if (!selectedCandle || !candleGroups[selectedCandle]) {
        setSelectedCandle(currentCandleTime);
      }
    }
  }, [currentCandleTime, candleGroups]);

  const chartData = useMemo(() => {
    return Object.entries(candleGroups)
      .map(([candleTime, { up, down }]) => {
        const upPnl = Number(up?.pnl ?? 0);
        const downPnl = Number(down?.pnl ?? 0);
        const totalPnl = upPnl + downPnl;
        const crossingCount = Number(up?.count ?? 0) + Number(down?.count ?? 0);
        return {
          time: candleTime,
          label: new Date(candleTime).toLocaleString('en-US', {
            month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false
          }),
          pnl: totalPnl,
          upPnl,
          downPnl,
          crossingCount,
        };
      })
      .sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());
  }, [candleGroups]);

  const cumulativeData = useMemo(() => {
    let cumulative = 0;
    return chartData.map(d => {
      cumulative += d.pnl;
      return { ...d, cumulative };
    });
  }, [chartData]);

  const candlePnlStats = useMemo(() => {
    const entries = Object.entries(candleGroups);
    let profit = 0;
    let loss = 0;
    let breakeven = 0;
    entries.forEach(([, { up, down }]) => {
      const totalPnl = Number(up?.pnl ?? 0) + Number(down?.pnl ?? 0);
      if (totalPnl > 0) profit++;
      else if (totalPnl < 0) loss++;
      else breakeven++;
    });
    const total = profit + loss;
    const winRate = total > 0 ? (profit / total) * 100 : 0;
    return { profit, loss, breakeven, total: entries.length, winRate };
  }, [candleGroups]);

  if (loading && !data) {
    return <div className="text-center py-10">Loading data...</div>;
  }

  if (!data) {
    return <div className="text-center py-10 text-red-500">Failed to load data</div>;
  }

  const { overall } = data;
  const colorClass = timeframe === '5m' ? 'text-purple-500' : 'text-cyan-500';
  const bgColorClass = timeframe === '5m' ? 'bg-purple-600' : 'bg-cyan-600';
  const chartColor = timeframe === '5m' ? '#a855f7' : '#06b6d4';

  return (
    <div className="space-y-6">
      {/* Strategy Info */}
      <div className="bg-card rounded-lg border border-border p-4">
        <h3 className="text-lg font-semibold mb-2">
          BTC - Crossing Limit ({timeframe.toUpperCase()})
          <span className={`ml-2 px-2 py-0.5 text-xs ${timeframe === '5m' ? 'bg-purple-500/20 text-purple-400' : 'bg-cyan-500/20 text-cyan-400'} rounded`}>
            MAX {maxCount}x
          </span>
        </h3>
        <div className="text-sm text-muted-foreground">
          {timeframe === '5m' ? '5' : '15'}분봉 시작가(open) crossing 시 방향 베팅.
          1회차 x10, 2-9회차 x20, 10회차 x10. <strong>최대 {maxCount}회</strong>, {timeframe === '5m' ? '4분 50초' : '14분 30초'} 이후 final round.
        </div>
      </div>

      {/* Coin Info */}
      <div className="flex items-center gap-2">
        <span className={`px-4 py-2 rounded-lg text-sm font-bold ${bgColorClass} text-white`}>
          BTC
        </span>
        <span className="text-sm text-muted-foreground ml-4">Auto-refresh 5s</span>
      </div>

      {/* Overall Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div className="p-4 bg-card rounded-lg border border-border">
          <div className="text-sm text-muted-foreground">Total Trades</div>
          <div className="text-2xl font-bold">{overall.total}</div>
          <div className="text-xs text-muted-foreground mt-1">
            Closed: {overall.closed}
          </div>
        </div>
        <div className="p-4 bg-card rounded-lg border border-border">
          <div className="text-sm text-muted-foreground">Candle Win Rate</div>
          <div className={`text-2xl font-bold ${candlePnlStats.winRate >= 50 ? 'text-green-500' : 'text-red-500'}`}>
            {candlePnlStats.winRate.toFixed(1)}%
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            +:{candlePnlStats.profit} -:{candlePnlStats.loss}
          </div>
        </div>
        <div className="p-4 bg-card rounded-lg border border-border">
          <div className="text-sm text-muted-foreground">Paper PnL</div>
          <div className={`text-2xl font-bold ${Number(overall.paper_pnl ?? 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            ${Number(overall.paper_pnl ?? 0).toFixed(2)}
          </div>
        </div>
        <div className="p-4 bg-card rounded-lg border border-border">
          <div className="text-sm text-muted-foreground">Avg PnL/Candle</div>
          <div className={`text-2xl font-bold ${Number(overall.paper_pnl ?? 0) / Math.max(candlePnlStats.total, 1) >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            ${(Number(overall.paper_pnl ?? 0) / Math.max(candlePnlStats.total, 1)).toFixed(2)}
          </div>
        </div>
        <div className="p-4 bg-card rounded-lg border border-border">
          <div className="text-sm text-muted-foreground">Live Crossings</div>
          <div className={`text-2xl font-bold ${colorClass}`}>
            {(() => {
              const liveCandle = candleGroups[currentCandleTime];
              if (!liveCandle) return 0;
              return Number(liveCandle.up?.count ?? 0) + Number(liveCandle.down?.count ?? 0);
            })()}
            <span className="text-sm text-muted-foreground font-normal"> / {maxCount}</span>
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            UP: {Number(candleGroups[currentCandleTime]?.up?.count ?? 0)} | DOWN: {Number(candleGroups[currentCandleTime]?.down?.count ?? 0)}
          </div>
        </div>
      </div>

      {/* Cumulative PnL Chart */}
      {cumulativeData.length > 0 && (
        <div className="bg-card rounded-lg border border-border p-4">
          <h3 className="text-lg font-semibold mb-3 flex items-center justify-between">
            <span>Cumulative PnL ({timeframe} candles)</span>
            <span className={`text-xl font-bold ${(cumulativeData[cumulativeData.length - 1]?.cumulative ?? 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>
              ${(cumulativeData[cumulativeData.length - 1]?.cumulative ?? 0).toFixed(2)}
            </span>
          </h3>
          <div className="h-52">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={cumulativeData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 10 }}
                  interval="preserveStartEnd"
                />
                <YAxis
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v) => `$${v}`}
                  width={50}
                />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '8px' }}
                  labelStyle={{ color: '#9ca3af' }}
                  formatter={(value: number, name: string) => {
                    if (name === 'cumulative') return [`$${value.toFixed(2)}`, 'Cumulative PnL'];
                    if (name === 'crossingCount') return [value, 'Crossings'];
                    return [`$${value.toFixed(2)}`, name];
                  }}
                />
                <ReferenceLine y={0} stroke="#6b7280" strokeDasharray="3 3" />
                <Line
                  type="monotone"
                  dataKey="cumulative"
                  stroke={chartColor}
                  strokeWidth={2}
                  dot={{ r: 3, fill: chartColor }}
                  activeDot={{ r: 5 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Per-Candle Stats */}
      <div className="bg-card rounded-lg border border-border p-4">
        <h3 className="text-lg font-semibold mb-3">Per-Candle Crossing Stats</h3>
        <div className="overflow-x-auto max-h-80">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-card">
              <tr className="border-b border-border">
                <th className="text-left py-2 px-2">Candle Start</th>
                <th className="text-center py-2 px-2 bg-green-500/10" colSpan={4}>UP (Cross Up)</th>
                <th className="text-center py-2 px-2 bg-red-500/10" colSpan={4}>DOWN (Cross Down)</th>
                <th className="text-right py-2 px-2">Total PnL</th>
              </tr>
              <tr className="border-b border-border text-muted-foreground">
                <th className="text-left py-1 px-2"></th>
                <th className="text-right py-1 px-2 bg-green-500/5">#</th>
                <th className="text-right py-1 px-2 bg-green-500/5">Avg$</th>
                <th className="text-right py-1 px-2 bg-green-500/5">Cost</th>
                <th className="text-right py-1 px-2 bg-green-500/5">PnL</th>
                <th className="text-right py-1 px-2 bg-red-500/5">#</th>
                <th className="text-right py-1 px-2 bg-red-500/5">Avg$</th>
                <th className="text-right py-1 px-2 bg-red-500/5">Cost</th>
                <th className="text-right py-1 px-2 bg-red-500/5">PnL</th>
                <th className="text-right py-1 px-2"></th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(candleGroups)
                .sort(([a], [b]) => new Date(b).getTime() - new Date(a).getTime())
                .map(([candleTime, { up, down }]) => {
                  const upPnl = Number(up?.pnl ?? 0);
                  const downPnl = Number(down?.pnl ?? 0);
                  const totalPnl = upPnl + downPnl;
                  const totalCrossings = Number(up?.count ?? 0) + Number(down?.count ?? 0);
                  return (
                    <tr
                      key={candleTime}
                      className={`border-b border-border/50 hover:bg-secondary/30 cursor-pointer ${
                        selectedCandle === candleTime ? 'bg-primary/20' : ''
                      } ${candleTime === currentCandleTime ? `border-l-2 ${timeframe === '5m' ? 'border-l-purple-500' : 'border-l-cyan-500'}` : ''}`}
                      onClick={() => setSelectedCandle(selectedCandle === candleTime ? null : candleTime)}
                    >
                      <td className="py-1.5 px-2 font-mono">
                        {new Date(candleTime).toLocaleString('en-US', {
                          month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                        })}
                        {candleTime === currentCandleTime && (
                          <span className={`ml-2 px-1.5 py-0.5 text-[10px] ${timeframe === '5m' ? 'bg-purple-500/20 text-purple-400' : 'bg-cyan-500/20 text-cyan-400'} rounded animate-pulse`}>
                            LIVE
                          </span>
                        )}
                        {totalCrossings > 0 && (
                          <span className="ml-2 px-1.5 py-0.5 text-[10px] bg-blue-500/20 text-blue-400 rounded">
                            x{totalCrossings}/{maxCount}
                          </span>
                        )}
                      </td>
                      {/* UP */}
                      <td className="text-right py-1.5 px-2 font-mono bg-green-500/5">
                        {up ? up.count : '-'}
                      </td>
                      <td className="text-right py-1.5 px-2 font-mono bg-green-500/5">
                        {up ? Number(up.avg_entry_price).toFixed(3) : '-'}
                      </td>
                      <td className="text-right py-1.5 px-2 font-mono bg-green-500/5">
                        {up ? `$${Number(up.total_cost).toFixed(2)}` : '-'}
                      </td>
                      <td className={`text-right py-1.5 px-2 font-mono bg-green-500/5 ${
                        upPnl >= 0 ? 'text-green-500' : 'text-red-500'
                      }`}>
                        {up ? `$${upPnl.toFixed(2)}` : '-'}
                      </td>
                      {/* DOWN */}
                      <td className="text-right py-1.5 px-2 font-mono bg-red-500/5">
                        {down ? down.count : '-'}
                      </td>
                      <td className="text-right py-1.5 px-2 font-mono bg-red-500/5">
                        {down ? Number(down.avg_entry_price).toFixed(3) : '-'}
                      </td>
                      <td className="text-right py-1.5 px-2 font-mono bg-red-500/5">
                        {down ? `$${Number(down.total_cost).toFixed(2)}` : '-'}
                      </td>
                      <td className={`text-right py-1.5 px-2 font-mono bg-red-500/5 ${
                        downPnl >= 0 ? 'text-green-500' : 'text-red-500'
                      }`}>
                        {down ? `$${downPnl.toFixed(2)}` : '-'}
                      </td>
                      {/* Total */}
                      <td className={`text-right py-1.5 px-2 font-mono font-bold ${
                        totalPnl >= 0 ? 'text-green-500' : 'text-red-500'
                      }`}>
                        ${totalPnl.toFixed(2)}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Selected Candle Trades */}
      {selectedCandle && (
        <div className="bg-card rounded-lg border border-border p-4">
          <h3 className="text-lg font-semibold mb-3 flex items-center justify-between">
            <span>
              {new Date(selectedCandle).toLocaleString('en-US', {
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
              })} Crossing Detail ({candleTrades.length})
            </span>
            <button
              onClick={() => setSelectedCandle(null)}
              className="text-sm px-3 py-1 bg-secondary rounded hover:opacity-90"
            >
              Close
            </button>
          </h3>
          {candleTradesLoading ? (
            <div className="text-center py-4 text-muted-foreground">Loading...</div>
          ) : candleTrades.length === 0 ? (
            <div className="text-center py-4 text-muted-foreground">No crossings for this candle</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-secondary/50">
                  <tr className="border-b border-border">
                    <th className="text-left py-2 px-2">Time</th>
                    <th className="text-left py-2 px-2">#</th>
                    <th className="text-left py-2 px-2">Direction</th>
                    <th className="text-right py-2 px-2 text-yellow-400">Open</th>
                    <th className="text-right py-2 px-2 text-muted-foreground">Prev</th>
                    <th className="text-right py-2 px-2 text-blue-400">Curr</th>
                    <th className="text-right py-2 px-2">Entry$</th>
                    <th className="text-right py-2 px-2">Qty</th>
                    <th className="text-right py-2 px-2">Cost</th>
                    <th className="text-left py-2 px-2">Status</th>
                    <th className="text-left py-2 px-2">Result</th>
                    <th className="text-right py-2 px-2">PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {candleTrades.map((trade, idx) => {
                    const crossing = trade.orderbook_snapshot?.crossing;
                    // Extract entry number from reason like "CROSS5M_UP #3 @120s"
                    const entryMatch = trade.reason?.match(/#(\d+)/);
                    const entryNum = entryMatch ? entryMatch[1] : (idx + 1).toString();
                    return (
                      <tr key={trade.id} className="border-b border-border/50 hover:bg-secondary/30">
                        <td className="py-1.5 px-2 font-mono">{new Date(trade.time).toLocaleTimeString()}</td>
                        <td className="py-1.5 px-2 font-mono text-muted-foreground">#{entryNum}</td>
                        <td className={`py-1.5 px-2 font-bold ${trade.side === 'UP' ? 'text-green-500' : 'text-red-500'}`}>
                          {trade.side === 'UP' ? '↑ UP' : '↓ DN'}
                        </td>
                        <td className="text-right py-1.5 px-2 font-mono text-yellow-400">
                          {crossing?.candle_open != null ? Number(crossing.candle_open).toLocaleString(undefined, {maximumFractionDigits: 2}) : '-'}
                        </td>
                        <td className="text-right py-1.5 px-2 font-mono text-muted-foreground">
                          {crossing?.prev_price != null ? Number(crossing.prev_price).toLocaleString(undefined, {maximumFractionDigits: 2}) : '-'}
                        </td>
                        <td className="text-right py-1.5 px-2 font-mono text-blue-400">
                          {crossing?.current_price != null ? Number(crossing.current_price).toLocaleString(undefined, {maximumFractionDigits: 2}) : '-'}
                        </td>
                        <td className="text-right py-1.5 px-2 font-mono">{Number(trade.order_price).toFixed(3)}</td>
                        <td className="text-right py-1.5 px-2 font-mono">{Number(trade.contracts ?? 0).toLocaleString()}</td>
                        <td className="text-right py-1.5 px-2 font-mono">${Number(trade.cost ?? 0).toFixed(2)}</td>
                        <td className="py-1.5 px-2">
                          <span className={`px-1.5 py-0.5 rounded text-xs ${
                            trade.status === 'CLOSED' ? 'bg-blue-500/20 text-blue-400' :
                            trade.status === 'FILLED' ? 'bg-green-500/20 text-green-400' :
                            trade.status === 'PENDING' ? 'bg-yellow-500/20 text-yellow-400' :
                            'bg-gray-500/20 text-gray-400'
                          }`}>
                            {trade.status}
                          </span>
                        </td>
                        <td className="py-1.5 px-2">
                          {trade.outcome ? (
                            <span className={`px-1.5 py-0.5 rounded text-xs ${
                              trade.outcome === 'WIN' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                            }`}>
                              {trade.outcome}
                            </span>
                          ) : '-'}
                        </td>
                        <td className={`text-right py-1.5 px-2 font-mono ${
                          trade.pnl === null ? '' :
                          Number(trade.pnl) >= 0 ? 'text-green-500' : 'text-red-500'
                        }`}>
                          {trade.pnl !== null ? `$${Number(trade.pnl).toFixed(2)}` : '-'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
