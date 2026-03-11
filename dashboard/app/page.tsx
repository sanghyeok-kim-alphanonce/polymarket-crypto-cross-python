'use client';

import { useState } from 'react';
import { AllCandleDirections } from '@/components/CandleDirectionDisplay';
import { OrderbookRedisDisplay } from '@/components/OrderbookRedisDisplay';
import { OHLCVChart } from '@/components/OHLCVChart';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { TimezoneDisplay } from '@/components/TimezoneDisplay';
const coins = ['btc', 'eth', 'sol', 'xrp'] as const;
const timeframes = ['5m', '15m', '1h', '4h'] as const;

export default function Home() {
  const [selectedCoin, setSelectedCoin] = useState<string>('btc');
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>('15m');

  return (
    <main className="min-h-screen p-4">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Polymarket Coin Dashboard</h1>
          <TimezoneDisplay compact />
        </div>

        {/* Filters */}
        <div className="flex flex-col sm:flex-row gap-4 mb-6 items-center justify-center">
          {/* Coin Selection */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground font-medium">Coin:</span>
            <ToggleGroup type="single" value={selectedCoin} onValueChange={(v) => v && setSelectedCoin(v)}>
              {coins.map((coin) => (
                <ToggleGroupItem key={coin} value={coin} aria-label={coin.toUpperCase()}>
                  {coin.toUpperCase()}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>

          {/* Timeframe Selection */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground font-medium">Timeframe:</span>
            <ToggleGroup type="single" value={selectedTimeframe} onValueChange={(v) => v && setSelectedTimeframe(v)}>
              {timeframes.map((tf) => (
                <ToggleGroupItem key={tf} value={tf} aria-label={tf}>
                  {tf}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        </div>

        {/* Dashboard Grid */}
        <div className="space-y-4">
          {/* All Candle Directions (with Binance + Chainlink prices) */}
          <AllCandleDirections timeframe={selectedTimeframe} />

          {/* Orderbook from Redis */}
          <OrderbookRedisDisplay coin={selectedCoin} timeframe={selectedTimeframe} />

          {/* OHLCV Chart */}
          <OHLCVChart coin={selectedCoin} timeframe={selectedTimeframe} />
        </div>
      </div>
    </main>
  );
}
