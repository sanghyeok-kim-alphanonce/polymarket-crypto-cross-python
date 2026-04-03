import pandas as pd
import numpy as np

df = pd.read_csv('/home/yeonwoo/poly-test/5m_crossing_count.csv')
df['candle_start'] = pd.to_datetime(df['candle_start'])

# 3개 캔들 연속 데이터 구성
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
        
        # 4개 연속 캔들인지 확인
        if ((t2-t3).total_seconds() == 300 and 
            (t1-t2).total_seconds() == 300 and 
            (t0-t1).total_seconds() == 300):
            results.append({
                'coin': coin,
                'prev3': prev3,
                'prev2': prev2,
                'prev1': prev1,
                'curr': curr,
                'curr_under5': curr < 5,
                'hour': t0.hour,
            })

rdf = pd.DataFrame(results)
print(f"총 샘플: {len(rdf)}, 기본 확률: {rdf['curr_under5'].mean()*100:.1f}%")
print()

# 파생 feature
rdf['sum3'] = rdf['prev3'] + rdf['prev2'] + rdf['prev1']
rdf['sum2'] = rdf['prev2'] + rdf['prev1']
rdf['max3'] = rdf[['prev3', 'prev2', 'prev1']].max(axis=1)
rdf['min3'] = rdf[['prev3', 'prev2', 'prev1']].min(axis=1)
rdf['mean3'] = (rdf['prev3'] + rdf['prev2'] + rdf['prev1']) / 3
rdf['trend'] = (rdf['prev1'] - rdf['prev3'])  # 2캔들 전 대비 변화

conditions = []

# 1. 직전 1개만
print("=" * 80)
print("직전 1개 캔들만 (prev1)")
print("=" * 80)
for val in range(0, 15):
    mask = rdf['prev1'] == val
    subset = rdf[mask]
    if len(subset) >= 30:
        prob = subset['curr_under5'].mean() * 100
        print(f"  prev1=={val:2d}: {prob:5.1f}% (n={len(subset):4d})")
        conditions.append({'cond': f'prev1=={val}', 'prob': prob, 'n': len(subset)})

# 2. 직전 3개 합계
print()
print("=" * 80)
print("직전 3개 캔들 합계 (sum3)")
print("=" * 80)
for s in range(3, 40):
    mask = rdf['sum3'] == s
    subset = rdf[mask]
    if len(subset) >= 30:
        prob = subset['curr_under5'].mean() * 100
        print(f"  sum3=={s:2d}: {prob:5.1f}% (n={len(subset):4d})")
        conditions.append({'cond': f'sum3=={s}', 'prob': prob, 'n': len(subset)})

# 3. 직전 3개 합계 <= N
print()
print("=" * 80)
print("직전 3개 합계 <= N (sum3 <= N)")
print("=" * 80)
for s in range(3, 25):
    mask = rdf['sum3'] <= s
    subset = rdf[mask]
    if len(subset) >= 30:
        prob = subset['curr_under5'].mean() * 100
        print(f"  sum3<={s:2d}: {prob:5.1f}% (n={len(subset):4d})")
        conditions.append({'cond': f'sum3<={s}', 'prob': prob, 'n': len(subset)})

# 4. 연속 감소 (prev3 > prev2 > prev1)
print()
print("=" * 80)
print("추세 패턴")
print("=" * 80)

patterns = [
    ("연속감소 (p3>p2>p1)", (rdf['prev3'] > rdf['prev2']) & (rdf['prev2'] > rdf['prev1'])),
    ("연속증가 (p3<p2<p1)", (rdf['prev3'] < rdf['prev2']) & (rdf['prev2'] < rdf['prev1'])),
    ("V자 (p2<p1 & p2<p3)", (rdf['prev2'] < rdf['prev1']) & (rdf['prev2'] < rdf['prev3'])),
    ("역V (p2>p1 & p2>p3)", (rdf['prev2'] > rdf['prev1']) & (rdf['prev2'] > rdf['prev3'])),
    ("모두 같음", (rdf['prev3'] == rdf['prev2']) & (rdf['prev2'] == rdf['prev1'])),
    ("3개 모두 <=2", (rdf['prev3'] <= 2) & (rdf['prev2'] <= 2) & (rdf['prev1'] <= 2)),
    ("3개 모두 <=3", (rdf['prev3'] <= 3) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
]

for name, mask in patterns:
    subset = rdf[mask]
    if len(subset) >= 20:
        prob = subset['curr_under5'].mean() * 100
        print(f"  {name:25s}: {prob:5.1f}% (n={len(subset):4d})")
        conditions.append({'cond': name, 'prob': prob, 'n': len(subset)})

# 5. 급감 후 안정
print()
print("=" * 80)
print("급변 패턴")
print("=" * 80)

patterns2 = [
    ("급감 (p3>=5, p1<=2)", (rdf['prev3'] >= 5) & (rdf['prev1'] <= 2)),
    ("급감 (p3>=8, p1<=3)", (rdf['prev3'] >= 8) & (rdf['prev1'] <= 3)),
    ("급증 (p3<=2, p1>=5)", (rdf['prev3'] <= 2) & (rdf['prev1'] >= 5)),
    ("안정→급증 (p3<=2, p2<=2, p1>=5)", (rdf['prev3'] <= 2) & (rdf['prev2'] <= 2) & (rdf['prev1'] >= 5)),
]

for name, mask in patterns2:
    subset = rdf[mask]
    if len(subset) >= 20:
        prob = subset['curr_under5'].mean() * 100
        print(f"  {name:35s}: {prob:5.1f}% (n={len(subset):4d})")
        conditions.append({'cond': name, 'prob': prob, 'n': len(subset)})

# 6. 시간대 + 3캔들 조합
print()
print("=" * 80)
print("시간대 + 3캔들 조합 (best)")
print("=" * 80)

for hour in [9, 20, 21, 22]:
    for sum_max in [6, 9, 12]:
        mask = (rdf['hour'] == hour) & (rdf['sum3'] <= sum_max)
        subset = rdf[mask]
        if len(subset) >= 20:
            prob = subset['curr_under5'].mean() * 100
            print(f"  hour=={hour} & sum3<={sum_max}: {prob:5.1f}% (n={len(subset):4d})")
            conditions.append({'cond': f'hour=={hour} & sum3<={sum_max}', 'prob': prob, 'n': len(subset)})

# 7. 평균 기반
print()
print("=" * 80)
print("3캔들 평균 기반")
print("=" * 80)
for avg in [1, 2, 3, 4, 5]:
    mask = rdf['mean3'] <= avg
    subset = rdf[mask]
    if len(subset) >= 30:
        prob = subset['curr_under5'].mean() * 100
        print(f"  mean3<={avg}: {prob:5.1f}% (n={len(subset):4d})")

# 8. max3 기반
print()
print("=" * 80)
print("3캔들 중 최대값 기반")
print("=" * 80)
for m in range(1, 10):
    mask = rdf['max3'] <= m
    subset = rdf[mask]
    if len(subset) >= 30:
        prob = subset['curr_under5'].mean() * 100
        print(f"  max3<={m}: {prob:5.1f}% (n={len(subset):4d})")

# 최종 순위
print()
print("=" * 80)
print("TOP 20 전체 (n >= 50)")
print("=" * 80)
cond_df = pd.DataFrame(conditions)
cond_df['lift'] = cond_df['prob'] - 62.1
filtered = cond_df[cond_df['n'] >= 50].sort_values('prob', ascending=False).head(20)
print(f"{'조건':<40} {'확률':>7} {'lift':>7} {'n':>6}")
print("-" * 70)
for _, row in filtered.iterrows():
    print(f"{row['cond']:<40} {row['prob']:>6.1f}% {row['lift']:>+6.1f} {row['n']:>6}")

