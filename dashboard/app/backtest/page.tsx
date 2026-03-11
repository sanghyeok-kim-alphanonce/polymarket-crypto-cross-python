'use client';

import { useState, useCallback, useEffect } from 'react';

interface BtTrade {
  time: string;
  elapsed_min: number;
  side: 'UP' | 'DOWN';
  band: string;
  entry_price: number;
  contracts: number;
  cost: number;
  outcome: 'WIN' | 'LOSS' | 'FLAT' | null;
  pnl: number | null;
}

interface PaperTrade {
  time: string;
  side: string;
  entry_price: number;
  contracts: number;
  cost: number;
  outcome: string;
  pnl: number;
}

interface PaperStats {
  total_trades: number;
  up_trades: number;
  down_trades: number;
  wins: number;
  losses: number;
  pnl: number;
  trades: PaperTrade[];
}

interface CandleResult {
  candle_start: string;
  candle_end: string;
  coin: string;
  market_result: string | null;
  trades: BtTrade[];
  total_trades: number;
  up_trades: number;
  down_trades: number;
  wins: number;
  losses: number;
  pnl: number;
  band_moves: number;
  paper: PaperStats | null;
}

interface CandleEntry {
  market_slug: string;
  candle_start: string;
  candle_end: string;
  result: CandleResult | null;
  loading: boolean;
  error: string | null;
}

const COINS = ['btc', 'eth', 'sol', 'xrp'];
const STRATEGIES = [
  { value: 'v12_5', label: 'V12-5 (Delta)' },
  { value: 'v12_6', label: 'V12-6 (Momentum)' },
  { value: 'crossing', label: 'Crossing (5x limit)' },
  { value: 'crossing_v2', label: 'Crossing V2 (unlimited)' },
];

function formatTime(iso: string) {
  const d = new Date(iso);
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function formatDateTime(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

export default function BacktestPage() {
  const [coin, setCoin] = useState('btc');
  const [strategy, setStrategy] = useState('crossing_v2');
  const [limit, setLimit] = useState(20);
  const [candles, setCandles] = useState<CandleEntry[]>([]);
  const [loadingCandles, setLoadingCandles] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedCandle, setExpandedCandle] = useState<string | null>(null);

  const loadCandles = useCallback(async () => {
    setLoadingCandles(true);
    setError(null);
    setCandles([]);
    setExpandedCandle(null);
    try {
      const res = await fetch(`/api/backtest/candles?coin=${coin}&limit=${limit}&strategy=${strategy}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setCandles(json.candles.map((c: { market_slug: string; candle_start: string; candle_end: string }) => ({
        market_slug: c.market_slug,
        candle_start: c.candle_start,
        candle_end: c.candle_end,
        result: null,
        loading: false,
        error: null,
      })));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingCandles(false);
    }
  }, [coin, limit, strategy]);

  useEffect(() => {
    loadCandles();
  }, [loadCandles]);

  const runCandle = useCallback(async (slug: string) => {
    setCandles(prev => prev.map(c =>
      c.market_slug === slug ? { ...c, loading: true, error: null } : c
    ));
    try {
      const res = await fetch(`/api/backtest?coin=${coin}&market_slug=${encodeURIComponent(slug)}&strategy=${strategy}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setCandles(prev => prev.map(c =>
        c.market_slug === slug ? { ...c, result: json, loading: false } : c
      ));
    } catch (e) {
      setCandles(prev => prev.map(c =>
        c.market_slug === slug ? { ...c, error: String(e), loading: false } : c
      ));
    }
  }, [coin, strategy]);

  const runAll = useCallback(async () => {
    for (const c of candles) {
      if (!c.result && !c.loading) {
        await runCandle(c.market_slug);
      }
    }
  }, [candles, runCandle]);

  const toggleCandle = (key: string) => {
    setExpandedCandle(prev => prev === key ? null : key);
  };

  // Summary from completed candles
  const completed = candles.filter(c => c.result);
  const summary = completed.length > 0 ? {
    candles: completed.length,
    total_trades: completed.reduce((s, c) => s + (c.result?.total_trades ?? 0), 0),
    wins: completed.reduce((s, c) => s + (c.result?.wins ?? 0), 0),
    losses: completed.reduce((s, c) => s + (c.result?.losses ?? 0), 0),
    total_pnl: completed.reduce((s, c) => s + (c.result?.pnl ?? 0), 0),
    paper_trades: completed.reduce((s, c) => s + (c.result?.paper?.total_trades ?? 0), 0),
    paper_pnl: completed.reduce((s, c) => s + (c.result?.paper?.pnl ?? 0), 0),
  } : null;

  const winRate = summary && (summary.wins + summary.losses) > 0
    ? Math.round(100 * summary.wins / (summary.wins + summary.losses) * 100) / 100
    : 0;

  const strategyLabel = STRATEGIES.find(s => s.value === strategy)?.label ?? strategy;

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold">Strategy Backtest</h1>

      {/* Strategy Selector */}
      <div className="flex items-center gap-2 flex-wrap">
        {STRATEGIES.map(s => (
          <button
            key={s.value}
            onClick={() => setStrategy(s.value)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              strategy === s.value
                ? s.value.startsWith('crossing') ? 'bg-orange-600 text-white' : 'bg-blue-600 text-white'
                : 'bg-secondary text-muted-foreground hover:text-foreground'
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Controls */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex gap-1">
          {COINS.map(c => (
            <button
              key={c}
              onClick={() => setCoin(c)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                coin === c
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-secondary text-muted-foreground hover:text-foreground'
              }`}
            >
              {c.toUpperCase()}
            </button>
          ))}
        </div>
        <select
          value={limit}
          onChange={e => setLimit(Number(e.target.value))}
          className="bg-secondary border border-border rounded px-2 py-1.5 text-sm"
        >
          <option value={10}>10 candles</option>
          <option value={20}>20 candles</option>
          <option value={50}>50 candles</option>
          <option value={100}>100 candles</option>
        </select>
        <button
          onClick={loadCandles}
          disabled={loadingCandles}
          className="px-3 py-1.5 bg-primary text-primary-foreground rounded text-sm font-medium disabled:opacity-50"
        >
          {loadingCandles ? 'Loading...' : 'Load Candles'}
        </button>
        {candles.length > 0 && (
          <button
            onClick={runAll}
            className="px-3 py-1.5 bg-green-600 text-white rounded text-sm font-medium hover:bg-green-700"
          >
            Run All
          </button>
        )}
      </div>

      {error && (
        <div className="bg-destructive/10 text-destructive border border-destructive/20 rounded p-3 text-sm">
          {error}
        </div>
      )}

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
          <SummaryCard label="Candles" value={`${summary.candles} / ${candles.length}`} />
          <SummaryCard label="BT Trades" value={summary.total_trades} />
          <SummaryCard
            label="Win Rate"
            value={`${winRate.toFixed(1)}%`}
            color={winRate >= 50 ? 'text-green-400' : 'text-red-400'}
          />
          <SummaryCard
            label="BT PnL"
            value={`$${summary.total_pnl.toFixed(2)}`}
            color={summary.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}
          />
          <SummaryCard label="Paper Trades" value={summary.paper_trades} />
          <SummaryCard
            label="Paper PnL"
            value={`$${summary.paper_pnl.toFixed(2)}`}
            color={summary.paper_pnl >= 0 ? 'text-green-400' : 'text-red-400'}
          />
        </div>
      )}

      {/* Candle List */}
      {candles.length > 0 && (
        <div className="border border-border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-secondary text-muted-foreground">
                <th className="text-left px-3 py-2">Candle Start</th>
                <th className="text-center px-3 py-2">Result</th>
                <th className="text-center px-3 py-2">BT Trades</th>
                <th className="text-center px-3 py-2">W/L</th>
                <th className="text-right px-3 py-2">BT PnL</th>
                <th className="text-center px-3 py-2 border-l border-border">Paper Trades</th>
                <th className="text-center px-3 py-2">W/L</th>
                <th className="text-right px-3 py-2">Paper PnL</th>
                <th className="text-right px-3 py-2 border-l border-border">Diff</th>
                <th className="text-center px-3 py-2 w-20"></th>
              </tr>
            </thead>
            <tbody>
              {candles.map((c) => (
                <CandleRow
                  key={c.market_slug}
                  entry={c}
                  isExpanded={expandedCandle === c.market_slug}
                  onToggle={() => toggleCandle(c.market_slug)}
                  onRun={() => runCandle(c.market_slug)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {candles.length === 0 && !loadingCandles && !error && (
        <div className="text-center text-muted-foreground py-12 border border-dashed border-border rounded-lg">
          Select a coin and click &quot;Load Candles&quot; to start
        </div>
      )}
    </div>
  );
}

function SummaryCard({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="bg-secondary/50 border border-border rounded-lg p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-lg font-semibold mt-0.5 ${color ?? ''}`}>{value}</div>
    </div>
  );
}

function CandleRow({ entry, isExpanded, onToggle, onRun }: {
  entry: CandleEntry;
  isExpanded: boolean;
  onToggle: () => void;
  onRun: () => void;
}) {
  const r = entry.result;
  const p = r?.paper ?? null;

  const resultColor = r
    ? (r.market_result === 'UP' ? 'text-green-400' : r.market_result === 'DOWN' ? 'text-red-400' : 'text-muted-foreground')
    : '';
  const btPnlColor = r ? (r.pnl > 0 ? 'text-green-400' : r.pnl < 0 ? 'text-red-400' : '') : '';
  const paperPnlColor = p ? (p.pnl > 0 ? 'text-green-400' : p.pnl < 0 ? 'text-red-400' : '') : '';
  const diff = r && p ? Math.round((r.pnl - p.pnl) * 100) / 100 : null;
  const diffColor = diff !== null ? (diff > 0 ? 'text-green-400' : diff < 0 ? 'text-red-400' : '') : '';

  return (
    <>
      <tr className="border-t border-border hover:bg-secondary/30 transition-colors">
        <td
          className="px-3 py-2 font-mono cursor-pointer"
          onClick={r ? onToggle : undefined}
        >
          {formatDateTime(entry.candle_start)}
        </td>
        <td className={`text-center px-3 py-2 font-medium ${resultColor}`}>
          {r ? (r.market_result ?? '?') : '-'}
        </td>
        <td className="text-center px-3 py-2">{r ? r.total_trades : '-'}</td>
        <td className="text-center px-3 py-2">{r ? `${r.wins}/${r.losses}` : '-'}</td>
        <td className={`text-right px-3 py-2 font-mono ${btPnlColor}`}>
          {r ? `$${(r.pnl ?? 0).toFixed(2)}` : '-'}
        </td>
        <td className="text-center px-3 py-2 border-l border-border">{p ? p.total_trades : '-'}</td>
        <td className="text-center px-3 py-2">{p ? `${p.wins}/${p.losses}` : '-'}</td>
        <td className={`text-right px-3 py-2 font-mono ${paperPnlColor}`}>
          {p ? `$${p.pnl.toFixed(2)}` : '-'}
        </td>
        <td className={`text-right px-3 py-2 font-mono border-l border-border ${diffColor}`}>
          {diff !== null ? `$${diff.toFixed(2)}` : '-'}
        </td>
        <td className="text-center px-3 py-2">
          {entry.loading ? (
            <span className="text-xs text-muted-foreground">Running...</span>
          ) : r ? (
            <span className="text-xs text-green-400">Done</span>
          ) : (
            <button
              onClick={(e) => { e.stopPropagation(); onRun(); }}
              className="px-2 py-0.5 bg-primary text-primary-foreground rounded text-xs font-medium hover:opacity-80"
            >
              Run
            </button>
          )}
        </td>
      </tr>
      {isExpanded && r && (
        <tr>
          <td colSpan={10} className="p-0">
            <div className="bg-secondary/20 border-t border-border">
              {/* Backtest Trades */}
              <div className="px-3 py-2 text-xs font-semibold text-muted-foreground border-b border-border/50">
                Backtest Trades ({r.trades.length})
              </div>
              {r.trades.length > 0 ? (
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground">
                      <th className="text-left px-3 py-1.5">Time</th>
                      <th className="text-center px-3 py-1.5">Min</th>
                      <th className="text-center px-3 py-1.5">Side</th>
                      <th className="text-center px-3 py-1.5">Band</th>
                      <th className="text-right px-3 py-1.5">Entry</th>
                      <th className="text-right px-3 py-1.5">Contracts</th>
                      <th className="text-right px-3 py-1.5">Cost</th>
                      <th className="text-center px-3 py-1.5">Result</th>
                      <th className="text-right px-3 py-1.5">PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.trades.map((t, i) => {
                      const tPnlColor = (t.pnl ?? 0) > 0 ? 'text-green-400' : (t.pnl ?? 0) < 0 ? 'text-red-400' : '';
                      const sideColor = t.side === 'UP' ? 'text-green-400' : 'text-red-400';
                      const outcomeColor = t.outcome === 'WIN' ? 'text-green-400' : t.outcome === 'LOSS' ? 'text-red-400' : '';
                      return (
                        <tr key={i} className="border-t border-border/50">
                          <td className="px-3 py-1 font-mono">{formatTime(t.time)}</td>
                          <td className="text-center px-3 py-1">{t.elapsed_min}</td>
                          <td className={`text-center px-3 py-1 font-medium ${sideColor}`}>{t.side}</td>
                          <td className="text-center px-3 py-1 font-mono">{t.band}</td>
                          <td className="text-right px-3 py-1 font-mono">{(t.entry_price ?? 0).toFixed(2)}</td>
                          <td className="text-right px-3 py-1">{t.contracts}</td>
                          <td className="text-right px-3 py-1 font-mono">${(t.cost ?? 0).toFixed(2)}</td>
                          <td className={`text-center px-3 py-1 font-medium ${outcomeColor}`}>{t.outcome ?? '-'}</td>
                          <td className={`text-right px-3 py-1 font-mono ${tPnlColor}`}>
                            {t.pnl != null ? `$${t.pnl.toFixed(2)}` : '-'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <div className="px-3 py-3 text-center text-muted-foreground text-xs">
                  No backtest trades in this candle
                </div>
              )}

              {/* Paper Trades */}
              <div className="px-3 py-2 text-xs font-semibold text-muted-foreground border-b border-t border-border/50">
                Paper Trades ({p?.trades?.length ?? 0})
              </div>
              {p && p.trades.length > 0 ? (
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground">
                      <th className="text-left px-3 py-1.5">Time</th>
                      <th className="text-center px-3 py-1.5">Side</th>
                      <th className="text-right px-3 py-1.5">Entry</th>
                      <th className="text-right px-3 py-1.5">Contracts</th>
                      <th className="text-right px-3 py-1.5">Cost</th>
                      <th className="text-center px-3 py-1.5">Result</th>
                      <th className="text-right px-3 py-1.5">PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.trades.map((t, i) => {
                      const tPnlColor = t.pnl > 0 ? 'text-green-400' : t.pnl < 0 ? 'text-red-400' : '';
                      const sideColor = t.side === 'UP' ? 'text-green-400' : 'text-red-400';
                      const outcomeColor = t.outcome === 'WIN' ? 'text-green-400' : t.outcome === 'LOSS' ? 'text-red-400' : '';
                      return (
                        <tr key={i} className="border-t border-border/50">
                          <td className="px-3 py-1 font-mono">{formatTime(t.time)}</td>
                          <td className={`text-center px-3 py-1 font-medium ${sideColor}`}>{t.side}</td>
                          <td className="text-right px-3 py-1 font-mono">{(t.entry_price ?? 0).toFixed(2)}</td>
                          <td className="text-right px-3 py-1">{t.contracts}</td>
                          <td className="text-right px-3 py-1 font-mono">${(t.cost ?? 0).toFixed(2)}</td>
                          <td className={`text-center px-3 py-1 font-medium ${outcomeColor}`}>{t.outcome ?? '-'}</td>
                          <td className={`text-right px-3 py-1 font-mono ${tPnlColor}`}>
                            ${t.pnl.toFixed(2)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <div className="px-3 py-3 text-center text-muted-foreground text-xs">
                  No paper trades for this candle
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
