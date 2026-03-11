'use client';

interface OrderLevel {
  price: string;
  size: string;
}

interface OrderbookViewProps {
  bids: OrderLevel[];
  asks: OrderLevel[];
  label?: string;
  depth?: number;
  showCumulative?: boolean;
}

export function OrderbookView({
  bids,
  asks,
  label,
  depth = 5,
  showCumulative = true
}: OrderbookViewProps) {
  // bids: 높은 가격순으로 정렬 후 상위 N개
  const bidsRaw = (bids || [])
    .sort((a, b) => parseFloat(b.price) - parseFloat(a.price))
    .slice(0, depth);

  // asks: 낮은 가격순으로 정렬 후 상위 N개
  const asksRaw = (asks || [])
    .sort((a, b) => parseFloat(a.price) - parseFloat(b.price))
    .slice(0, depth);

  // 누적 Total 계산
  const calculateCumulative = (levels: OrderLevel[], reverse = false) => {
    let cumulative = 0;
    const result = levels.map((level) => {
      const price = parseFloat(level.price);
      const size = parseFloat(level.size);
      cumulative += price * size;
      return { price, size, total: cumulative };
    });
    return reverse ? result.reverse() : result;
  };

  // 누적 계산 (asks는 역순으로 표시 - 높은 가격이 위)
  const processedAsks = calculateCumulative(asksRaw, true);
  const processedBids = calculateCumulative(bidsRaw, false);

  const allSizes = [...bidsRaw, ...asksRaw].map(l => parseFloat(l.size));
  const maxSize = Math.max(...allSizes, 1);

  return (
    <div className="bg-secondary/30 p-3 rounded border">
      {label && (
        <div className="flex justify-between items-center mb-2">
          <div className="font-semibold text-sm">{label}</div>
        </div>
      )}

      {/* Header */}
      <div className="grid grid-cols-3 text-xs text-muted-foreground mb-1 px-1">
        <span>Price</span>
        <span className="text-right">Shares</span>
        {showCumulative && <span className="text-right">Total</span>}
      </div>

      {/* Asks (역순 - 높은 가격이 위) */}
      <div className="mb-1">
        {processedAsks.map((ask, i) => {
          const barWidth = (ask.size / maxSize) * 100;
          return (
            <div key={i} className="relative h-5 flex items-center">
              <div
                className="absolute right-0 h-full bg-red-500/30"
                style={{ width: `${barWidth}%` }}
              />
              <div className={`relative grid ${showCumulative ? 'grid-cols-3' : 'grid-cols-2'} w-full text-xs px-1`}>
                <span className="text-red-400 font-mono">{((ask.price ?? 0) * 100).toFixed(0)}¢</span>
                <span className="text-right font-mono">{(ask.size ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}</span>
                {showCumulative && (
                  <span className="text-right text-muted-foreground font-mono">${(ask.total ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* 구분선 */}
      <div className="flex items-center justify-center py-1 text-xs border-y border-border">
        <span className="text-red-400 text-[10px]">asks</span>
        <span className="mx-1 text-muted-foreground">|</span>
        <span className="text-green-400 text-[10px]">bids</span>
      </div>

      {/* Bids */}
      <div className="mt-1">
        {processedBids.map((bid, i) => {
          const barWidth = (bid.size / maxSize) * 100;
          return (
            <div key={i} className="relative h-5 flex items-center">
              <div
                className="absolute left-0 h-full bg-green-500/30"
                style={{ width: `${barWidth}%` }}
              />
              <div className={`relative grid ${showCumulative ? 'grid-cols-3' : 'grid-cols-2'} w-full text-xs px-1`}>
                <span className="text-green-400 font-mono">{((bid.price ?? 0) * 100).toFixed(0)}¢</span>
                <span className="text-right font-mono">{(bid.size ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}</span>
                {showCumulative && (
                  <span className="text-right text-muted-foreground font-mono">${(bid.total ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
