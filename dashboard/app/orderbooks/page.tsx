'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { OrderbookRedisDisplay } from '@/components/OrderbookRedisDisplay';
import { OrderbookView } from '@/components/OrderbookView';
import { BookOpen } from 'lucide-react';

const COINS = ['btc'] as const;
const TIMEFRAMES = ['5m', '15m', '1h'] as const;

interface BookRow {
  time: string;
  coin: string;
  timeframe: string;
  side: string;
  market_slug?: string;
  token_id?: string;
  bid_levels?: number;
  ask_levels?: number;
  bids?: [number, number][];
  asks?: [number, number][];
}

interface ChangeRow {
  time: string;
  token_id: string;
  price: number;
  size: number;
  book_side: string;
}

export default function OrderbooksPage() {
  const [selectedCoin, setSelectedCoin] = useState<string>('btc');
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>('15m');
  const [activeTab, setActiveTab] = useState<'books' | 'changes'>('books');

  // Books tab state
  const [bookRows, setBookRows] = useState<BookRow[]>([]);
  const [bookTotal, setBookTotal] = useState(0);
  const [bookLoading, setBookLoading] = useState(false);
  const [bookLimit, setBookLimit] = useState(50);
  const [bookOffset, setBookOffset] = useState(0);
  const [bookOrder, setBookOrder] = useState<'ASC' | 'DESC'>('DESC');

  // Changes tab state
  const [changeRows, setChangeRows] = useState<ChangeRow[]>([]);
  const [changeTotal, setChangeTotal] = useState(0);
  const [changeLoading, setChangeLoading] = useState(false);
  const [changeLimit, setChangeLimit] = useState(100);
  const [changeOffset, setChangeOffset] = useState(0);
  const [changeOrder, setChangeOrder] = useState<'ASC' | 'DESC'>('DESC');
  const [tokenSideMap, setTokenSideMap] = useState<Record<string, string>>({});

  // Shared state
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Detail view (books tab)
  const [selectedTime, setSelectedTime] = useState<string | null>(null);
  const [detailRows, setDetailRows] = useState<BookRow[]>([]);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Reset on coin/timeframe change
  useEffect(() => {
    setBookOffset(0);
    setChangeOffset(0);
    setSelectedTime(null);
    setDetailRows([]);
  }, [selectedCoin, selectedTimeframe]);

  // Fetch Books
  useEffect(() => {
    if (activeTab !== 'books') return;
    const fetchBooks = async () => {
      setBookLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          tab: 'books',
          coin: selectedCoin,
          timeframe: selectedTimeframe,
          limit: bookLimit.toString(),
          offset: bookOffset.toString(),
          order: bookOrder,
        });
        const res = await fetch(`/api/orderbook-db?${params}`);
        if (!res.ok) throw new Error('Failed to fetch');
        const data = await res.json();
        setBookRows(data.rows ?? []);
        setBookTotal(data.total ?? 0);
      } catch {
        setError('Failed to load orderbook books');
      } finally {
        setBookLoading(false);
      }
    };
    fetchBooks();
  }, [selectedCoin, selectedTimeframe, bookLimit, bookOffset, bookOrder, refreshKey, activeTab]);

  // Fetch Changes
  useEffect(() => {
    if (activeTab !== 'changes') return;
    const fetchChanges = async () => {
      setChangeLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          tab: 'changes',
          coin: selectedCoin,
          timeframe: selectedTimeframe,
          limit: changeLimit.toString(),
          offset: changeOffset.toString(),
          order: changeOrder,
        });
        const res = await fetch(`/api/orderbook-db?${params}`);
        if (!res.ok) throw new Error('Failed to fetch');
        const data = await res.json();
        setChangeRows(data.rows ?? []);
        setChangeTotal(data.total ?? 0);
        setTokenSideMap(data.tokenSideMap ?? {});
      } catch {
        setError('Failed to load orderbook changes');
      } finally {
        setChangeLoading(false);
      }
    };
    fetchChanges();
  }, [selectedCoin, selectedTimeframe, changeLimit, changeOffset, changeOrder, refreshKey, activeTab]);

  // Fetch detail (bids/asks for specific time)
  const fetchDetail = async (timeStr: string) => {
    if (selectedTime === timeStr) {
      setSelectedTime(null);
      setDetailRows([]);
      return;
    }
    setSelectedTime(timeStr);
    setLoadingDetail(true);
    try {
      const params = new URLSearchParams({
        coin: selectedCoin,
        timeframe: selectedTimeframe,
        rowId: timeStr,
      });
      const res = await fetch(`/api/orderbook-db?${params}`);
      if (res.ok) {
        const data = await res.json();
        setDetailRows(data.rows ?? []);
      }
    } catch (err) {
      console.error('Failed to fetch detail:', err);
    } finally {
      setLoadingDetail(false);
    }
  };

  const bookCurrentPage = Math.floor(bookOffset / bookLimit) + 1;
  const bookTotalPages = Math.ceil(bookTotal / bookLimit);
  const changeCurrentPage = Math.floor(changeOffset / changeLimit) + 1;
  const changeTotalPages = Math.ceil(changeTotal / changeLimit);

  const loading = activeTab === 'books' ? bookLoading : changeLoading;

  return (
    <div className="min-h-screen p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold mb-6 flex items-center gap-2">
          <BookOpen className="w-6 h-6" />
          Orderbook Explorer
        </h1>

        {/* Controls */}
        <Card className="mb-6">
          <CardContent className="pt-6">
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
                <span className="text-sm text-muted-foreground font-medium">Timeframe:</span>
                <ToggleGroup type="single" value={selectedTimeframe} onValueChange={(v) => v && setSelectedTimeframe(v)}>
                  {TIMEFRAMES.map((tf) => (
                    <ToggleGroupItem key={tf} value={tf} className="text-xs">
                      {tf}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Live Orderbook */}
        <div className="mb-6">
          <OrderbookRedisDisplay coin={selectedCoin} timeframe={selectedTimeframe} />
        </div>

        {/* Tab Selector */}
        <div className="flex gap-2 mb-4">
          <button
            onClick={() => setActiveTab('books')}
            className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
              activeTab === 'books'
                ? 'bg-primary text-primary-foreground'
                : 'bg-secondary text-secondary-foreground hover:opacity-80'
            }`}
          >
            Books ({bookTotal.toLocaleString()})
          </button>
          <button
            onClick={() => setActiveTab('changes')}
            className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
              activeTab === 'changes'
                ? 'bg-primary text-primary-foreground'
                : 'bg-secondary text-secondary-foreground hover:opacity-80'
            }`}
          >
            Changes ({changeTotal.toLocaleString()})
          </button>
        </div>

        {/* History Controls */}
        <Card className="mb-4">
          <CardContent className="pt-4 pb-4">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">Rows:</span>
                <select
                  value={activeTab === 'books' ? bookLimit : changeLimit}
                  onChange={(e) => {
                    const val = parseInt(e.target.value);
                    if (activeTab === 'books') { setBookLimit(val); setBookOffset(0); }
                    else { setChangeLimit(val); setChangeOffset(0); }
                  }}
                  className="bg-secondary px-2 py-1 rounded text-sm"
                >
                  {[50, 100, 200, 500].map((l) => (
                    <option key={l} value={l}>{l}</option>
                  ))}
                </select>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">Order:</span>
                <select
                  value={activeTab === 'books' ? bookOrder : changeOrder}
                  onChange={(e) => {
                    const val = e.target.value as 'ASC' | 'DESC';
                    if (activeTab === 'books') { setBookOrder(val); setBookOffset(0); }
                    else { setChangeOrder(val); setChangeOffset(0); }
                  }}
                  className="bg-secondary px-2 py-1 rounded text-sm"
                >
                  <option value="DESC">Newest first</option>
                  <option value="ASC">Oldest first</option>
                </select>
              </div>
              <div className="flex items-center gap-3 ml-auto">
                <span className="text-sm text-muted-foreground">
                  {activeTab === 'books'
                    ? (bookTotal > 0 ? `${bookTotal.toLocaleString()} books` : 'No data')
                    : (changeTotal > 0 ? `${changeTotal.toLocaleString()} changes` : 'No data')
                  }
                </span>
                <button
                  onClick={() => setRefreshKey((k) => k + 1)}
                  disabled={loading}
                  className="bg-primary text-primary-foreground px-3 py-1 rounded text-sm hover:opacity-90 disabled:opacity-50"
                >
                  Refresh
                </button>
              </div>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="text-destructive mb-4 p-4 bg-destructive/10 rounded">{error}</div>
        )}

        {/* Books Tab */}
        {activeTab === 'books' && (
          <>
            <Card className="mb-4">
              <CardHeader className="pb-2">
                <CardTitle className="text-lg">
                  orderbook_books - {selectedCoin.toUpperCase()}/{selectedTimeframe}
                  {bookLoading && <span className="text-sm font-normal text-muted-foreground ml-2">(Loading...)</span>}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto max-h-[500px] overflow-y-auto border rounded">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-secondary z-10">
                      <tr className="border-b">
                        <th className="text-left py-2 px-3 whitespace-nowrap">Time</th>
                        <th className="text-left py-2 px-3 whitespace-nowrap">Side</th>
                        <th className="text-right py-2 px-3 whitespace-nowrap">Bid Levels</th>
                        <th className="text-right py-2 px-3 whitespace-nowrap">Ask Levels</th>
                        <th className="text-left py-2 px-3 whitespace-nowrap">Market Slug</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bookRows.length === 0 && !bookLoading && (
                        <tr>
                          <td colSpan={5} className="text-center py-8 text-muted-foreground">
                            No orderbook books recorded yet.
                          </td>
                        </tr>
                      )}
                      {bookRows.map((row, i) => {
                        const isSelected = selectedTime === row.time;
                        return (
                          <tr
                            key={`${row.time}-${row.side}-${i}`}
                            onClick={() => fetchDetail(row.time)}
                            className={`border-b cursor-pointer hover:bg-secondary/50 transition-colors ${
                              isSelected ? 'bg-primary/10' : ''
                            }`}
                          >
                            <td className="py-1.5 px-3 font-mono text-xs whitespace-nowrap">
                              {new Date(row.time).toLocaleString()}
                            </td>
                            <td className="py-1.5 px-3">
                              <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${
                                row.side === 'up'
                                  ? 'bg-green-500/20 text-green-400'
                                  : 'bg-red-500/20 text-red-400'
                              }`}>
                                {(row.side ?? '--').toUpperCase()}
                              </span>
                            </td>
                            <td className="py-1.5 px-3 text-right font-mono text-xs text-green-400">
                              {(row.bid_levels ?? 0)}
                            </td>
                            <td className="py-1.5 px-3 text-right font-mono text-xs text-red-400">
                              {(row.ask_levels ?? 0)}
                            </td>
                            <td className="py-1.5 px-3 font-mono text-xs text-muted-foreground truncate max-w-[200px]">
                              {row.market_slug ?? '--'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Pagination */}
                {bookTotal > 0 && (
                  <div className="flex items-center justify-between mt-4">
                    <div className="text-sm text-muted-foreground">
                      {bookOffset + 1} - {Math.min(bookOffset + bookRows.length, bookTotal)} of {bookTotal.toLocaleString()}
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setBookOffset(0)}
                        disabled={bookOffset === 0 || bookLoading}
                        className="px-3 py-1 bg-secondary rounded text-sm hover:opacity-90 disabled:opacity-50"
                      >
                        First
                      </button>
                      <button
                        onClick={() => setBookOffset(Math.max(0, bookOffset - bookLimit))}
                        disabled={bookOffset === 0 || bookLoading}
                        className="px-3 py-1 bg-secondary rounded text-sm hover:opacity-90 disabled:opacity-50"
                      >
                        Prev
                      </button>
                      <span className="px-3 py-1 text-sm text-muted-foreground">
                        Page {bookCurrentPage} / {bookTotalPages}
                      </span>
                      <button
                        onClick={() => setBookOffset(bookOffset + bookLimit)}
                        disabled={bookOffset + bookLimit >= bookTotal || bookLoading}
                        className="px-3 py-1 bg-secondary rounded text-sm hover:opacity-90 disabled:opacity-50"
                      >
                        Next
                      </button>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Detail View - Orderbook Visualization */}
            {selectedTime && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-lg flex items-center justify-between">
                    <span>
                      Orderbook Detail - {new Date(selectedTime).toLocaleString()}
                      {loadingDetail && <span className="text-sm font-normal text-muted-foreground ml-2">(Loading...)</span>}
                    </span>
                    <button
                      onClick={() => { setSelectedTime(null); setDetailRows([]); }}
                      className="text-sm px-3 py-1 bg-secondary rounded hover:opacity-90"
                    >
                      Close
                    </button>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {detailRows.length > 0 ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {detailRows.map((detail) => {
                        const bids = (detail.bids ?? []).map(([p, s]) => ({
                          price: String(p),
                          size: String(s),
                        }));
                        const asks = (detail.asks ?? []).map(([p, s]) => ({
                          price: String(p),
                          size: String(s),
                        }));
                        return (
                          <div key={detail.side}>
                            <div className="flex items-center gap-2 mb-2">
                              <span className={`text-sm font-semibold px-2 py-0.5 rounded ${
                                detail.side === 'up'
                                  ? 'bg-green-500/20 text-green-400'
                                  : 'bg-red-500/20 text-red-400'
                              }`}>
                                {(detail.side ?? '--').toUpperCase()}
                              </span>
                              {detail.market_slug && (
                                <a
                                  href={`https://polymarket.com/event/${detail.market_slug}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-xs text-blue-400 hover:underline ml-auto truncate max-w-[200px]"
                                >
                                  {detail.market_slug}
                                </a>
                              )}
                            </div>
                            <OrderbookView
                              bids={bids}
                              asks={asks}
                              depth={10}
                            />
                          </div>
                        );
                      })}
                    </div>
                  ) : loadingDetail ? (
                    <div className="text-muted-foreground text-sm">Loading orderbook detail...</div>
                  ) : (
                    <div className="text-muted-foreground text-sm">No detail data available</div>
                  )}
                </CardContent>
              </Card>
            )}
          </>
        )}

        {/* Changes Tab */}
        {activeTab === 'changes' && (
          <Card className="mb-4">
            <CardHeader className="pb-2">
              <CardTitle className="text-lg">
                orderbook_changes - {selectedCoin.toUpperCase()}/{selectedTimeframe}
                {changeLoading && <span className="text-sm font-normal text-muted-foreground ml-2">(Loading...)</span>}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto max-h-[500px] overflow-y-auto border rounded">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-secondary z-10">
                    <tr className="border-b">
                      <th className="text-left py-2 px-3 whitespace-nowrap">Time</th>
                      <th className="text-left py-2 px-3 whitespace-nowrap">Side</th>
                      <th className="text-left py-2 px-3 whitespace-nowrap">Book Side</th>
                      <th className="text-right py-2 px-3 whitespace-nowrap">Price</th>
                      <th className="text-right py-2 px-3 whitespace-nowrap">Size</th>
                    </tr>
                  </thead>
                  <tbody>
                    {changeRows.length === 0 && !changeLoading && (
                      <tr>
                        <td colSpan={5} className="text-center py-8 text-muted-foreground">
                          No orderbook changes recorded yet.
                        </td>
                      </tr>
                    )}
                    {changeRows.map((row, i) => {
                      const marketSide = tokenSideMap[row.token_id] ?? '?';
                      return (
                        <tr key={`${row.time}-${row.token_id}-${row.price}-${i}`} className="border-b">
                          <td className="py-1.5 px-3 font-mono text-xs whitespace-nowrap">
                            {new Date(row.time).toLocaleString()}
                          </td>
                          <td className="py-1.5 px-3">
                            <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${
                              marketSide === 'up'
                                ? 'bg-green-500/20 text-green-400'
                                : marketSide === 'down'
                                  ? 'bg-red-500/20 text-red-400'
                                  : 'bg-secondary text-muted-foreground'
                            }`}>
                              {marketSide.toUpperCase()}
                            </span>
                          </td>
                          <td className="py-1.5 px-3">
                            <span className={`text-xs font-medium ${
                              row.book_side === 'BUY' ? 'text-green-400' : 'text-red-400'
                            }`}>
                              {row.book_side ?? '--'}
                            </span>
                          </td>
                          <td className="py-1.5 px-3 text-right font-mono text-xs">
                            {((row.price ?? 0) * 100).toFixed(1)}¢
                          </td>
                          <td className="py-1.5 px-3 text-right font-mono text-xs">
                            <span className={row.size === 0 ? 'text-muted-foreground line-through' : ''}>
                              {(row.size ?? 0).toLocaleString()}
                            </span>
                            {row.size === 0 && <span className="text-xs text-muted-foreground ml-1">(removed)</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              {changeTotal > 0 && (
                <div className="flex items-center justify-between mt-4">
                  <div className="text-sm text-muted-foreground">
                    {changeOffset + 1} - {Math.min(changeOffset + changeRows.length, changeTotal)} of {changeTotal.toLocaleString()}
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setChangeOffset(0)}
                      disabled={changeOffset === 0 || changeLoading}
                      className="px-3 py-1 bg-secondary rounded text-sm hover:opacity-90 disabled:opacity-50"
                    >
                      First
                    </button>
                    <button
                      onClick={() => setChangeOffset(Math.max(0, changeOffset - changeLimit))}
                      disabled={changeOffset === 0 || changeLoading}
                      className="px-3 py-1 bg-secondary rounded text-sm hover:opacity-90 disabled:opacity-50"
                    >
                      Prev
                    </button>
                    <span className="px-3 py-1 text-sm text-muted-foreground">
                      Page {changeCurrentPage} / {changeTotalPages}
                    </span>
                    <button
                      onClick={() => setChangeOffset(changeOffset + changeLimit)}
                      disabled={changeOffset + changeLimit >= changeTotal || changeLoading}
                      className="px-3 py-1 bg-secondary rounded text-sm hover:opacity-90 disabled:opacity-50"
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
