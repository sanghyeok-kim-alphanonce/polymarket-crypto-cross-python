'use client';

import { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

interface CurrentCandle {
  coin: string;
  timeframe: string;
  direction: 'up' | 'down' | 'flat';
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  price_change_pct: number;
  candle_start: string;
  candle_end: string;
  updated_at: string;
  // 최근 1분 변동성 지표
  high_1m?: number;
  low_1m?: number;
  wick_ratio?: number;  // |high-low| / |close-open|
  // reversal signal 지표
  body_abs?: number;
  btr?: number;  // Body To Range (0~1)
  opposite_tail?: number;
}

interface CandleDirectionDisplayProps {
  coin: string;
  timeframe: string;
}

export function CandleDirectionDisplay({ coin, timeframe }: CandleDirectionDisplayProps) {
  const [data, setData] = useState<CurrentCandle | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const res = await fetch(`/api/candle-direction?coin=${coin}&timeframe=${timeframe}`);
      if (!res.ok) throw new Error('Failed to fetch');
      const result = await res.json();
      if (!result.error) {
        setData(result);
      }
    } catch (err) {
      console.error('Failed to fetch current candle:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 1000); // 1초마다 업데이트
    return () => clearInterval(interval);
  }, [coin, timeframe]);

  if (loading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Current Candle</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Loading...</p>
        </CardContent>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Current Candle</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No data</p>
        </CardContent>
      </Card>
    );
  }

  const isUp = data.direction === 'up';
  const isDown = data.direction === 'down';
  const Icon = isUp ? TrendingUp : isDown ? TrendingDown : Minus;
  const colorClass = isUp ? 'text-green-500' : isDown ? 'text-red-500' : 'text-muted-foreground';
  const bgColorClass = isUp ? 'bg-green-500/10' : isDown ? 'bg-red-500/10' : 'bg-muted/10';

  const formatPrice = (price: number | null | undefined) => {
    if (price == null) return '--';
    if (coin === 'btc') return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (coin === 'eth') return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return price.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 });
  };

  const formatTime = (isoString: string) => {
    const date = new Date(isoString);
    if (isNaN(date.getTime())) {
      return '--:--:--';
    }
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  };

  return (
    <Card className={bgColorClass}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span>Current Candle</span>
          <Badge variant="outline" className="text-xs">
            {data.timeframe}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {/* Direction with icon */}
        <div className="flex items-center justify-center gap-2">
          <Icon className={`w-6 h-6 ${colorClass}`} />
          <span className={`text-2xl font-bold ${colorClass}`}>
            {data.direction.toUpperCase()}
          </span>
        </div>

        {/* Price change */}
        <div className="text-center">
          <span className={`text-xl font-mono font-semibold ${colorClass}`}>
            {data.price_change_pct >= 0 ? '+' : ''}{data.price_change_pct.toFixed(3)}%
          </span>
        </div>

        {/* Price details */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="text-muted-foreground">
            Open: <span className="font-mono">${formatPrice(data.open)}</span>
          </div>
          <div className="text-muted-foreground text-right">
            Close: <span className="font-mono">${formatPrice(data.close)}</span>
          </div>
        </div>

        {/* High/Low */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="text-muted-foreground">
            High: <span className="font-mono text-green-500">${formatPrice(data.high)}</span>
          </div>
          <div className="text-muted-foreground text-right">
            Low: <span className="font-mono text-red-500">${formatPrice(data.low)}</span>
          </div>
        </div>

        {/* Candle time range */}
        <p className="text-xs text-center text-muted-foreground">
          {formatTime(data.candle_start)} ~ {formatTime(data.candle_end)}
        </p>
      </CardContent>
    </Card>
  );
}

// All candle directions grid display
interface AllCandleDirectionsProps {
  timeframe?: string;
}


// Chainlink 캔들 타입
interface ChainlinkCandle {
  coin: string;
  timeframe: string;
  direction: 'up' | 'down' | 'flat';
  open: number;
  high: number;
  low: number;
  close: number;
  price_change_pct: number;
  candle_start: string;
  candle_end: string;
  updated_at: string;
}

// Exchange Price 타입
interface ExchangePrice {
  exchange: string;
  symbol: string;
  coin: string;
  price: number;
  bid: number;
  ask: number;
  timestamp: number;
  datetime: string;
}

export function AllCandleDirections({ timeframe }: AllCandleDirectionsProps) {
  const [binanceCandles, setBinanceCandles] = useState<Record<string, CurrentCandle>>({});
  const [bybitCandles, setBybitCandles] = useState<Record<string, CurrentCandle>>({});
  const [gateCandles, setGateCandles] = useState<Record<string, CurrentCandle>>({});
  const [bitgetCandles, setBitgetCandles] = useState<Record<string, CurrentCandle>>({});
  const [chainlinkCandles, setChainlinkCandles] = useState<Record<string, ChainlinkCandle>>({});
  const [exchangePrices, setExchangePrices] = useState<Record<string, ExchangePrice>>({});
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const candleRes = await fetch('/api/candle-direction');

      if (candleRes.ok) {
        const candleResult = await candleRes.json();
        // API returns { binance, bybit, gate, bitget, chainlink, exchangePrices }
        if (candleResult.binance) setBinanceCandles(candleResult.binance);
        if (candleResult.bybit) setBybitCandles(candleResult.bybit);
        if (candleResult.gate) setGateCandles(candleResult.gate);
        if (candleResult.bitget) setBitgetCandles(candleResult.bitget);
        if (candleResult.chainlink) setChainlinkCandles(candleResult.chainlink);
        if (candleResult.exchangePrices) setExchangePrices(candleResult.exchangePrices);
      }
    } catch (err) {
      console.error('Failed to fetch data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 1000);
    return () => clearInterval(interval);
  }, []);

  const coins = ['btc', 'eth', 'sol', 'xrp'];
  const timeframes = timeframe ? [timeframe] : ['5m', '15m', '1h', '4h'];

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Candle Directions</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Loading...</p>
        </CardContent>
      </Card>
    );
  }

  const formatTime = (isoString: string) => {
    const date = new Date(isoString);
    if (isNaN(date.getTime())) {
      return '--:--:--';
    }
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  };

  const getTimeRange = (tf: string) => {
    // Get any coin's data for this timeframe to get the time range
    const key = `btc_${tf}`;
    const data = binanceCandles[key];
    if (!data?.candle_start || !data?.candle_end) return '';
    return `${formatTime(data.candle_start)}~${formatTime(data.candle_end)}`;
  };

  // SVG 캔들스틱 컴포넌트
  const CandleStick = ({ data }: { data: CurrentCandle }) => {
    const { open, high, low, close } = data;
    const range = high - low;
    if (range === 0) return <div className="w-12 h-16" />;

    const height = 60;
    const width = 40;
    const bodyWidth = 16;
    const wickWidth = 2;

    // 정규화 (0~1 범위로)
    const normalize = (price: number) => 1 - (price - low) / range;

    const highY = normalize(high) * height;
    const lowY = normalize(low) * height;
    const openY = normalize(open) * height;
    const closeY = normalize(close) * height;

    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(Math.abs(closeY - openY), 2);

    const isUp = close > open;
    const color = isUp ? '#22c55e' : '#ef4444';
    const fillColor = isUp ? '#22c55e' : '#ef4444';

    return (
      <svg width={width} height={height} className="mx-auto">
        {/* Upper wick */}
        <line
          x1={width / 2}
          y1={highY}
          x2={width / 2}
          y2={bodyTop}
          stroke={color}
          strokeWidth={wickWidth}
        />
        {/* Lower wick */}
        <line
          x1={width / 2}
          y1={bodyTop + bodyHeight}
          x2={width / 2}
          y2={lowY}
          stroke={color}
          strokeWidth={wickWidth}
        />
        {/* Body */}
        <rect
          x={(width - bodyWidth) / 2}
          y={bodyTop}
          width={bodyWidth}
          height={bodyHeight}
          fill={fillColor}
          stroke={color}
          strokeWidth={1}
        />
      </svg>
    );
  };

  const getBinanceCoinData = (coin: string) => {
    const tf = timeframes[0]; // 선택된 timeframe
    const key = `${coin}_${tf}`;
    return binanceCandles[key];
  };

  const getChainlinkCoinData = (coin: string) => {
    const tf = timeframes[0];
    const key = `${coin}_${tf}`;
    return chainlinkCandles[key];
  };

  const getExchangeCoinData = (exchange: string, coin: string) => {
    const tf = timeframes[0];
    const key = `${coin}_${tf}`;
    switch (exchange) {
      case 'bybit': return bybitCandles[key];
      case 'gate': return gateCandles[key];
      case 'bitget': return bitgetCandles[key];
      default: return null;
    }
  };

  const getExchangePrice = (exchange: string, coin: string) => {
    const key = `${exchange}:${coin}`;
    return exchangePrices[key];
  };

  const allExchanges = ['binance', 'bybit', 'gate', 'bitget', 'chainlink'];
  const exchangeColors: Record<string, string> = {
    binance: 'text-yellow-500',
    bybit: 'text-orange-500',
    gate: 'text-purple-500',
    bitget: 'text-cyan-500',
    chainlink: 'text-blue-500',
  };

  const tf = timeframes[0];
  const timeRange = getTimeRange(tf);

  const formatPrice = (price: number | null | undefined, coin: string) => {
    if (price == null) return '--';
    if (coin === 'btc') return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (coin === 'eth') return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return price.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Current Candle - {tf}</span>
          <div className="flex items-center gap-2">
            <span className="text-xs font-normal text-muted-foreground">{timeRange}</span>
            <Badge variant="outline" className="text-xs font-normal">
              Real-time
            </Badge>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* All Exchanges with Candle Data */}
          {allExchanges.map(exchange => {
            const getCoinData = (coin: string) => {
              if (exchange === 'binance') return getBinanceCoinData(coin);
              if (exchange === 'chainlink') return getChainlinkCoinData(coin);
              return getExchangeCoinData(exchange, coin);
            };

            const getAnyData = () => {
              if (exchange === 'chainlink') {
                return getChainlinkCoinData('btc') || getChainlinkCoinData('eth');
              }
              return getCoinData('btc') || getCoinData('eth');
            };

            const anyData = getAnyData();

            return (
              <div key={exchange}>
                <div className="flex items-center justify-between mb-3">
                  <h3 className={`text-sm font-semibold ${exchangeColors[exchange]}`}>
                    {exchange.charAt(0).toUpperCase() + exchange.slice(1)}
                  </h3>
                  {(() => {
                    if (anyData?.updated_at) {
                      const updatedAt = new Date(anyData.updated_at);
                      const now = new Date();
                      const diffSec = Math.floor((now.getTime() - updatedAt.getTime()) / 1000);
                      const isStale = diffSec > 10;
                      return (
                        <span className={`text-xs ${isStale ? 'text-red-500 font-semibold' : 'text-muted-foreground'}`}>
                          {isStale ? `⚠️ ${diffSec}s ago` : `${diffSec}s ago`}
                        </span>
                      );
                    }
                    return <span className="text-xs text-red-500">No data</span>;
                  })()}
                </div>
                <div className="space-y-2">
                  {coins.map(coin => {
                    const data = getCoinData(coin);
                    if (!data) {
                      return (
                        <div key={coin} className="flex items-center gap-3 p-2 bg-secondary/30 rounded">
                          <span className="font-semibold w-10">{coin.toUpperCase()}</span>
                          <div className="w-10 h-14 flex items-center justify-center text-muted-foreground">--</div>
                          <div className="flex-1 text-right text-muted-foreground/50">No data</div>
                        </div>
                      );
                    }

                    const isUp = data.direction === 'up';
                    const isDown = data.direction === 'down';
                    const colorClass = isUp ? 'text-green-500' : isDown ? 'text-red-500' : 'text-muted-foreground';
                    const bgClass = isUp ? 'bg-green-500/10' : isDown ? 'bg-red-500/10' : 'bg-secondary/30';
                    const sign = data.price_change_pct >= 0 ? '+' : '';

                    const candleData = {
                      open: data.open,
                      high: data.high,
                      low: data.low,
                      close: data.close,
                    } as CurrentCandle;

                    // Get real-time price from exchangePrices
                    const exchangePrice = getExchangePrice(exchange, coin);
                    const currentPrice = exchangePrice?.price ?? data.close;
                    const priceChangePct = data.open > 0 ? ((currentPrice - data.open) / data.open) * 100 : 0;
                    const currentSign = priceChangePct >= 0 ? '+' : '';
                    const currentColorClass = priceChangePct > 0 ? 'text-green-500' : priceChangePct < 0 ? 'text-red-500' : 'text-muted-foreground';

                    return (
                      <div key={coin} className={`flex items-center gap-3 p-2 rounded ${bgClass}`}>
                        <span className="font-semibold w-10">{coin.toUpperCase()}</span>
                        <CandleStick data={candleData} />
                        <div className="flex-1 grid grid-cols-3 gap-2 text-xs">
                          <div>
                            <div className="text-muted-foreground">기준</div>
                            <div className="font-mono">${formatPrice(data.open, coin)}</div>
                          </div>
                          <div>
                            <div className="text-muted-foreground">현재</div>
                            <div className={`font-mono ${currentColorClass}`}>${formatPrice(currentPrice, coin)}</div>
                          </div>
                          <div>
                            <div className="text-muted-foreground">변동</div>
                            <div className={`font-mono font-semibold ${currentColorClass}`}>
                              {currentSign}{priceChangePct.toFixed(3)}%
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
