'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { OrderbookView } from './OrderbookView';
import { ExternalLink } from 'lucide-react';

interface OrderLevel {
  price: string;
  size: string;
}

interface OrderbookData {
  timestamp: number;
  market_slug?: string;
  best_bid: number;
  best_ask: number;
  bids: OrderLevel[];
  asks: OrderLevel[];
}

interface OrderbookRedisDisplayProps {
  coin: string;
  timeframe: string;
  depth?: number;
}

export function OrderbookRedisDisplay({ coin, timeframe, depth = 5 }: OrderbookRedisDisplayProps) {
  // 기존 orderbook (trade only)
  const [upOrderbook, setUpOrderbook] = useState<OrderbookData | null>(null);
  const [downOrderbook, setDownOrderbook] = useState<OrderbookData | null>(null);
  // Full orderbook (price_change)
  const [upOrderbookFull, setUpOrderbookFull] = useState<OrderbookData | null>(null);
  const [downOrderbookFull, setDownOrderbookFull] = useState<OrderbookData | null>(null);

  const [loading, setLoading] = useState(true);
  const [isVisible, setIsVisible] = useState(true);

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsVisible(!document.hidden);
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, []);

  useEffect(() => {
    if (!isVisible) return;

    const fetchOrderbooks = async () => {
      try {
        // 기존 orderbook과 full orderbook 동시 fetch
        const [resNormal, resFull] = await Promise.all([
          fetch(`/api/orderbook-redis?coin=${coin}&timeframe=${timeframe}`),
          fetch(`/api/orderbook-redis-full?coin=${coin}&timeframe=${timeframe}`),
        ]);

        if (resNormal.ok) {
          const data = await resNormal.json();
          setUpOrderbook(data.up);
          setDownOrderbook(data.down);
        } else {
          setUpOrderbook(null);
          setDownOrderbook(null);
        }

        if (resFull.ok) {
          const data = await resFull.json();
          setUpOrderbookFull(data.up);
          setDownOrderbookFull(data.down);
        } else {
          setUpOrderbookFull(null);
          setDownOrderbookFull(null);
        }

        setLoading(false);
      } catch (err) {
        console.error('Failed to fetch Redis orderbooks:', err);
        setLoading(false);
      }
    };

    fetchOrderbooks();
    const interval = setInterval(fetchOrderbooks, 500);
    return () => clearInterval(interval);
  }, [coin, timeframe, isVisible]);

  const formatTimestamp = (ts: number) => {
    const age = (Date.now() / 1000 - ts).toFixed(1);
    return `${age}s`;
  };

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Live Orderbook Comparison</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Loading...</p>
        </CardContent>
      </Card>
    );
  }

  // market_slug가 있으면 정확한 마켓 페이지로 링크
  const marketSlug = upOrderbook?.market_slug || downOrderbook?.market_slug;
  const polymarketUrl = marketSlug
    ? `https://polymarket.com/event/${marketSlug}`
    : `https://polymarket.com/crypto`;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <a
            href={polymarketUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 hover:text-primary transition-colors"
          >
            <span>Live Orderbook - {coin.toUpperCase()} {timeframe}</span>
            <ExternalLink className="w-4 h-4" />
          </a>
          <div className="flex items-center gap-4 text-xs font-normal">
            <div className="flex items-center gap-1">
              <span className="px-2 py-1 bg-blue-500/20 text-blue-400 rounded">Trade Only</span>
              {upOrderbook?.timestamp && (
                <span className="text-muted-foreground">{formatTimestamp(upOrderbook.timestamp)}</span>
              )}
            </div>
            <div className="flex items-center gap-1">
              <span className="px-2 py-1 bg-green-500/20 text-green-400 rounded">Full (price_change)</span>
              {upOrderbookFull?.timestamp && (
                <span className="text-muted-foreground">{formatTimestamp(upOrderbookFull.timestamp)}</span>
              )}
            </div>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* 2x2 Grid: [Trade Up, Trade Down] / [Full Up, Full Down] */}
        <div className="space-y-4">
          {/* Row 1: Trade Only */}
          <div>
            <div className="text-xs text-muted-foreground mb-2 font-medium">Trade Only (book event)</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {upOrderbook ? (
                <OrderbookView
                  bids={upOrderbook.bids}
                  asks={upOrderbook.asks}
                  label="Up"
                  depth={depth}
                />
              ) : (
                <div className="p-3 text-center text-muted-foreground text-sm bg-secondary/30 rounded">
                  Up unavailable
                </div>
              )}

              {downOrderbook ? (
                <OrderbookView
                  bids={downOrderbook.bids}
                  asks={downOrderbook.asks}
                  label="Down"
                  depth={depth}
                />
              ) : (
                <div className="p-3 text-center text-muted-foreground text-sm bg-secondary/30 rounded">
                  Down unavailable
                </div>
              )}
            </div>
          </div>

          {/* Row 2: Full (price_change) */}
          <div>
            <div className="text-xs text-muted-foreground mb-2 font-medium">Full (price_change event)</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {upOrderbookFull ? (
                <OrderbookView
                  bids={upOrderbookFull.bids}
                  asks={upOrderbookFull.asks}
                  label="Up"
                  depth={depth}
                />
              ) : (
                <div className="p-3 text-center text-muted-foreground text-sm bg-secondary/30 rounded">
                  Up unavailable (start orderbook_ws_full)
                </div>
              )}

              {downOrderbookFull ? (
                <OrderbookView
                  bids={downOrderbookFull.bids}
                  asks={downOrderbookFull.asks}
                  label="Down"
                  depth={depth}
                />
              ) : (
                <div className="p-3 text-center text-muted-foreground text-sm bg-secondary/30 rounded">
                  Down unavailable (start orderbook_ws_full)
                </div>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
