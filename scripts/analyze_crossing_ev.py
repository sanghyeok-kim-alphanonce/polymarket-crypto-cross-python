import pandas as pd
import numpy as np

df = pd.read_csv('/home/yeonwoo/poly-test/5m_crossing_count.csv')
df['candle_start'] = pd.to_datetime(df['candle_start'])

# 3개 캔들 연속 데이터
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

# 수익 계산 함수
def calc_profit(crossing):
    if crossing == 0:
        return 0  # 0은 수익 없음으로 가정
    elif crossing == 1:
        return 4
    elif crossing == 2:
        return 3
    elif crossing == 3:
        return 2
    elif crossing == 4:
        return 1
    else:  # >= 5
        return -1

rdf['profit'] = rdf['curr'].apply(calc_profit)

def calc_ev(subset):
    if len(subset) == 0:
        return 0, 0, 0
    ev = subset['profit'].mean()
    win_rate = (subset['profit'] > 0).mean() * 100
    total_profit = subset['profit'].sum()
    return ev, win_rate, total_profit

print("=" * 100)
print("수익 구조: 1=>+4, 2=>+3, 3=>+2, 4=>+1, >=5=>-1, 0=>0")
print("=" * 100)
print(f"{'조건':<40} {'EV':>8} {'승률':>8} {'총수익':>10} {'n':>8} {'EV*n':>10}")
print("-" * 100)

conditions = [
    ("전체 (무조건 진입)", rdf['curr'] >= 0),
    
    ("---", None),
    ("== 둘 다 <=N 조건 ==", None),
    ("둘 다 <=1", (rdf['prev2'] <= 1) & (rdf['prev1'] <= 1)),
    ("둘 다 <=2", (rdf['prev2'] <= 2) & (rdf['prev1'] <= 2)),
    ("둘 다 <=3", (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("둘 다 <=4", (rdf['prev2'] <= 4) & (rdf['prev1'] <= 4)),
    ("둘 다 <=5", (rdf['prev2'] <= 5) & (rdf['prev1'] <= 5)),
    
    ("---", None),
    ("== 하나만 <=N 조건 ==", None),
    ("하나만 <=3", ((rdf['prev2'] <= 3) & (rdf['prev1'] > 3)) | ((rdf['prev2'] > 3) & (rdf['prev1'] <= 3))),
    
    ("---", None),
    ("== 둘 다 >N 조건 ==", None),
    ("둘 다 >3", (rdf['prev2'] > 3) & (rdf['prev1'] > 3)),
    ("둘 다 >5", (rdf['prev2'] > 5) & (rdf['prev1'] > 5)),
    
    ("---", None),
    ("== sum 조건 ==", None),
    ("sum2 <= 4", rdf['sum2'] <= 4),
    ("sum2 <= 6", rdf['sum2'] <= 6),
    ("sum3 <= 6", rdf['sum3'] <= 6),
    ("sum3 <= 9", rdf['sum3'] <= 9),
    
    ("---", None),
    ("== 시간대 조건 ==", None),
    ("hour==20", rdf['hour'] == 20),
    ("hour==21", rdf['hour'] == 21),
    ("hour==22", rdf['hour'] == 22),
    ("hour in [20,21,22]", rdf['hour'].isin([20,21,22])),
    
    ("---", None),
    ("== 시간대 + 조건 조합 ==", None),
    ("hour==21 & 둘 다 <=3", (rdf['hour'] == 21) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("hour==21 & 둘 다 <=5", (rdf['hour'] == 21) & (rdf['prev2'] <= 5) & (rdf['prev1'] <= 5)),
    ("hour==20 & 둘 다 <=3", (rdf['hour'] == 20) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("hour in [20,21,22] & 둘 다 <=3", rdf['hour'].isin([20,21,22]) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("hour in [20,21,22] & 둘 다 <=5", rdf['hour'].isin([20,21,22]) & (rdf['prev2'] <= 5) & (rdf['prev1'] <= 5)),
]

for name, mask in conditions:
    if mask is None:
        print(name)
        continue
    if name == "---":
        print("-" * 100)
        continue
    subset = rdf[mask]
    n = len(subset)
    if n < 20:
        continue
    ev, win_rate, total_profit = calc_ev(subset)
    print(f"{name:<40} {ev:>+7.3f} {win_rate:>7.1f}% {total_profit:>+10.0f} {n:>8} {ev*n:>+10.1f}")

# Top EV 조건 탐색
print()
print("=" * 100)
print("EV 최적화 조건 탐색 (n >= 100)")
print("=" * 100)

search_results = []

# prev2, prev1 범위 조합
for p2_max in range(0, 10):
    for p1_max in range(0, 10):
        mask = (rdf['prev2'] <= p2_max) & (rdf['prev1'] <= p1_max)
        subset = rdf[mask]
        if len(subset) >= 100:
            ev, win_rate, _ = calc_ev(subset)
            search_results.append({
                'cond': f"prev2<={p2_max} & prev1<={p1_max}",
                'ev': ev,
                'win_rate': win_rate,
                'n': len(subset),
                'total': ev * len(subset)
            })

# 시간대 + 조건
for hour in range(24):
    for p_max in range(1, 8):
        mask = (rdf['hour'] == hour) & (rdf['prev2'] <= p_max) & (rdf['prev1'] <= p_max)
        subset = rdf[mask]
        if len(subset) >= 50:
            ev, win_rate, _ = calc_ev(subset)
            search_results.append({
                'cond': f"hour=={hour} & 둘다<={p_max}",
                'ev': ev,
                'win_rate': win_rate,
                'n': len(subset),
                'total': ev * len(subset)
            })

# EV 순 정렬
search_df = pd.DataFrame(search_results).sort_values('ev', ascending=False)
print(f"{'조건':<35} {'EV':>8} {'승률':>8} {'n':>8} {'총수익':>10}")
print("-" * 80)
for _, row in search_df.head(20).iterrows():
    print(f"{row['cond']:<35} {row['ev']:>+7.3f} {row['win_rate']:>7.1f}% {row['n']:>8} {row['total']:>+10.1f}")

# 총 수익 순 정렬
print()
print("=" * 100)
print("총 수익 순 (EV * n)")
print("=" * 100)
search_df_total = search_df.sort_values('total', ascending=False)
print(f"{'조건':<35} {'EV':>8} {'승률':>8} {'n':>8} {'총수익':>10}")
print("-" * 80)
for _, row in search_df_total.head(20).iterrows():
    print(f"{row['cond']:<35} {row['ev']:>+7.3f} {row['win_rate']:>7.1f}% {row['n']:>8} {row['total']:>+10.1f}")

