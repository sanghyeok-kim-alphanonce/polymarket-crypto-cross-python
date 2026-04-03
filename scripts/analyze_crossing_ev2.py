import pandas as pd
import numpy as np

df = pd.read_csv('/home/yeonwoo/poly-test/5m_crossing_count.csv')
df['candle_start'] = pd.to_datetime(df['candle_start'])

results = []
for coin in df['coin'].unique():
    coin_df = df[df['coin'] == coin].sort_values('candle_start').reset_index(drop=True)
    
    for i in range(3, len(coin_df)):
        prev3 = coin_df.iloc[i-3]['crossing_count']
        prev2 = coin_df.iloc[i-2]['crossing_count']
        prev1 = coin_df.iloc[i-1]['crossing_count']
        curr = coin_df.iloc[i]['crossing_count']
        
        t3 = coin_df.iloc[i-3]['candle_start']
        t2 = coin_df.iloc[i-2]['candle_start']
        t1 = coin_df.iloc[i-1]['candle_start']
        t0 = coin_df.iloc[i]['candle_start']
        
        if ((t2-t3).total_seconds() == 300 and 
            (t1-t2).total_seconds() == 300 and 
            (t0-t1).total_seconds() == 300):
            results.append({
                'prev3': prev3, 'prev2': prev2, 'prev1': prev1,
                'curr': curr, 'hour': t0.hour,
                'sum3': prev3 + prev2 + prev1,
                'sum2': prev2 + prev1,
            })

rdf = pd.DataFrame(results)

# 실제 수익 구조
def calc_profit(crossing):
    if crossing == 0:
        return 0
    elif crossing == 1:
        return 3.0
    elif crossing == 2:
        return 1.0
    elif crossing == 3:
        return 0.12
    elif crossing == 4:
        return -0.18
    else:  # >= 5
        return -0.54

rdf['profit'] = rdf['curr'].apply(calc_profit)

print("=" * 95)
print("수익 구조 (실제): 1=>+3, 2=>+1, 3=>+0.12, 4=>-0.18, >=5=>-0.54, 0=>0")
print("=" * 95)
print(f"{'조건':<40} {'EV':>8} {'승률':>8} {'총수익':>10} {'n':>8}")
print("-" * 95)

conditions = [
    ("전체", rdf['curr'] >= 0),
    ("---", None),
    ("둘 다 <=1", (rdf['prev2'] <= 1) & (rdf['prev1'] <= 1)),
    ("둘 다 <=2", (rdf['prev2'] <= 2) & (rdf['prev1'] <= 2)),
    ("둘 다 <=3", (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("둘 다 <=4", (rdf['prev2'] <= 4) & (rdf['prev1'] <= 4)),
    ("둘 다 <=5", (rdf['prev2'] <= 5) & (rdf['prev1'] <= 5)),
    ("---", None),
    ("하나만 <=3", ((rdf['prev2'] <= 3) & (rdf['prev1'] > 3)) | ((rdf['prev2'] > 3) & (rdf['prev1'] <= 3))),
    ("둘 다 >3", (rdf['prev2'] > 3) & (rdf['prev1'] > 3)),
    ("---", None),
    ("hour in [20,21,22]", rdf['hour'].isin([20,21,22])),
    ("hour==21 & 둘 다 <=3", (rdf['hour'] == 21) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("hour in [20,21,22] & 둘 다 <=3", rdf['hour'].isin([20,21,22]) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
]

for name, mask in conditions:
    if mask is None:
        print("-" * 95)
        continue
    subset = rdf[mask]
    n = len(subset)
    if n < 20:
        continue
    ev = subset['profit'].mean()
    win_rate = (subset['profit'] > 0).mean() * 100
    total = subset['profit'].sum()
    print(f"{name:<40} {ev:>+7.3f} {win_rate:>7.1f}% {total:>+10.1f} {n:>8}")

# EV 최적화 탐색
print()
print("=" * 95)
print("EV TOP 20 (n >= 100)")
print("=" * 95)

search = []
for p2 in range(0, 10):
    for p1 in range(0, 10):
        mask = (rdf['prev2'] <= p2) & (rdf['prev1'] <= p1)
        subset = rdf[mask]
        if len(subset) >= 100:
            search.append({
                'cond': f"둘다<={min(p2,p1)}" if p2==p1 else f"p2<={p2} & p1<={p1}",
                'ev': subset['profit'].mean(),
                'n': len(subset),
                'total': subset['profit'].sum()
            })

for hour in [20, 21, 22]:
    for p in range(1, 8):
        mask = (rdf['hour'] == hour) & (rdf['prev2'] <= p) & (rdf['prev1'] <= p)
        subset = rdf[mask]
        if len(subset) >= 50:
            search.append({
                'cond': f"h{hour} & 둘다<={p}",
                'ev': subset['profit'].mean(),
                'n': len(subset),
                'total': subset['profit'].sum()
            })

sdf = pd.DataFrame(search).drop_duplicates('cond').sort_values('ev', ascending=False)
print(f"{'조건':<30} {'EV':>8} {'n':>8} {'총수익':>10}")
print("-" * 60)
for _, r in sdf.head(20).iterrows():
    print(f"{r['cond']:<30} {r['ev']:>+7.3f} {r['n']:>8} {r['total']:>+10.1f}")

