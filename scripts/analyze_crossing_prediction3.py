import pandas as pd
import numpy as np
from itertools import product

df = pd.read_csv('/home/yeonwoo/poly-test/5m_crossing_count.csv')
df['candle_start'] = pd.to_datetime(df['candle_start'])

results = []
for coin in df['coin'].unique():
    coin_df = df[df['coin'] == coin].sort_values('candle_start').reset_index(drop=True)
    
    for i in range(2, len(coin_df)):
        prev2 = coin_df.iloc[i-2]['crossing_count']
        prev1 = coin_df.iloc[i-1]['crossing_count']
        curr = coin_df.iloc[i]['crossing_count']
        
        t2 = coin_df.iloc[i-2]['candle_start']
        t1 = coin_df.iloc[i-1]['candle_start']
        t0 = coin_df.iloc[i]['candle_start']
        
        if (t1 - t2).total_seconds() == 300 and (t0 - t1).total_seconds() == 300:
            results.append({
                'coin': coin,
                'prev2': prev2,
                'prev1': prev1,
                'curr': curr,
                'curr_under5': curr < 5,
                'hour': t0.hour,
            })

rdf = pd.DataFrame(results)

# 파생 feature 생성
rdf['diff'] = rdf['prev1'] - rdf['prev2']  # 변화량
rdf['sum'] = rdf['prev2'] + rdf['prev1']
rdf['max'] = rdf[['prev2', 'prev1']].max(axis=1)
rdf['min'] = rdf[['prev2', 'prev1']].min(axis=1)
rdf['trend'] = np.where(rdf['diff'] > 0, 'up', np.where(rdf['diff'] < 0, 'down', 'flat'))

print(f"총 샘플: {len(rdf)}, 기본 확률: {rdf['curr_under5'].mean()*100:.1f}%")
print()

# 모든 조건 조합 탐색
conditions = []

# 1. (prev2 == a) AND (prev1 == b) 모든 조합
for p2 in range(0, 12):
    for p1 in range(0, 12):
        mask = (rdf['prev2'] == p2) & (rdf['prev1'] == p1)
        subset = rdf[mask]
        if len(subset) >= 20:
            prob = subset['curr_under5'].mean() * 100
            conditions.append({
                'cond': f"prev2=={p2} & prev1=={p1}",
                'prob': prob,
                'n': len(subset),
                'lift': prob - 62.1
            })

# 2. 변화량 기반
for d in range(-10, 11):
    mask = rdf['diff'] == d
    subset = rdf[mask]
    if len(subset) >= 30:
        prob = subset['curr_under5'].mean() * 100
        conditions.append({
            'cond': f"diff=={d} (prev1-prev2)",
            'prob': prob,
            'n': len(subset),
            'lift': prob - 62.1
        })

# 3. 변화량 + prev1 조합
for d_min, d_max in [(-10, -3), (-3, 0), (0, 0), (0, 3), (3, 10)]:
    for p1_max in range(1, 6):
        mask = (rdf['diff'] >= d_min) & (rdf['diff'] <= d_max) & (rdf['prev1'] <= p1_max)
        subset = rdf[mask]
        if len(subset) >= 30:
            prob = subset['curr_under5'].mean() * 100
            conditions.append({
                'cond': f"diff∈[{d_min},{d_max}] & prev1<={p1_max}",
                'prob': prob,
                'n': len(subset),
                'lift': prob - 62.1
            })

# 4. 급감 패턴 (prev2 크고 prev1 작음)
for p2_min in range(3, 15):
    for p1_max in range(1, 5):
        mask = (rdf['prev2'] >= p2_min) & (rdf['prev1'] <= p1_max)
        subset = rdf[mask]
        if len(subset) >= 20:
            prob = subset['curr_under5'].mean() * 100
            conditions.append({
                'cond': f"prev2>={p2_min} & prev1<={p1_max} (급감)",
                'prob': prob,
                'n': len(subset),
                'lift': prob - 62.1
            })

# 5. 급증 패턴 (prev2 작고 prev1 큼)
for p2_max in range(1, 5):
    for p1_min in range(3, 15):
        mask = (rdf['prev2'] <= p2_max) & (rdf['prev1'] >= p1_min)
        subset = rdf[mask]
        if len(subset) >= 20:
            prob = subset['curr_under5'].mean() * 100
            conditions.append({
                'cond': f"prev2<={p2_max} & prev1>={p1_min} (급증)",
                'prob': prob,
                'n': len(subset),
                'lift': prob - 62.1
            })

# 6. 시간대 조합
for hour in range(24):
    mask = rdf['hour'] == hour
    subset = rdf[mask]
    if len(subset) >= 30:
        prob = subset['curr_under5'].mean() * 100
        conditions.append({
            'cond': f"hour=={hour}",
            'prob': prob,
            'n': len(subset),
            'lift': prob - 62.1
        })

# 7. 시간대 + low crossing 조합
for hour in range(24):
    for p1_max in [2, 3]:
        mask = (rdf['hour'] == hour) & (rdf['prev1'] <= p1_max)
        subset = rdf[mask]
        if len(subset) >= 20:
            prob = subset['curr_under5'].mean() * 100
            conditions.append({
                'cond': f"hour=={hour} & prev1<={p1_max}",
                'prob': prob,
                'n': len(subset),
                'lift': prob - 62.1
            })

# 8. min-max 차이
for gap in range(0, 10):
    mask = (rdf['max'] - rdf['min']) == gap
    subset = rdf[mask]
    if len(subset) >= 30:
        prob = subset['curr_under5'].mean() * 100
        conditions.append({
            'cond': f"max-min=={gap}",
            'prob': prob,
            'n': len(subset),
            'lift': prob - 62.1
        })

# 결과 정렬
cond_df = pd.DataFrame(conditions)
cond_df = cond_df.sort_values('prob', ascending=False).drop_duplicates('cond')

print("=" * 80)
print("TOP 30 고확률 조건 (확률 순)")
print("=" * 80)
print(f"{'조건':<45} {'확률':>7} {'lift':>7} {'n':>6}")
print("-" * 80)
for _, row in cond_df.head(30).iterrows():
    print(f"{row['cond']:<45} {row['prob']:>6.1f}% {row['lift']:>+6.1f} {row['n']:>6}")

print()
print("=" * 80)
print("TOP 20 고확률 + 충분한 샘플 (n >= 100)")
print("=" * 80)
filtered = cond_df[cond_df['n'] >= 100].head(20)
print(f"{'조건':<45} {'확률':>7} {'lift':>7} {'n':>6}")
print("-" * 80)
for _, row in filtered.iterrows():
    print(f"{row['cond']:<45} {row['prob']:>6.1f}% {row['lift']:>+6.1f} {row['n']:>6}")

