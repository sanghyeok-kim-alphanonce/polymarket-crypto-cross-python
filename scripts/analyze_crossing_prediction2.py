import pandas as pd
import numpy as np

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
            })

rdf = pd.DataFrame(results)

# 1. 80%+ 조건 탐색 (더 세밀하게)
print("=" * 70)
print("80%+ 확률 조건 탐색 (n >= 30)")
print("=" * 70)

high_prob = []

# prev2, prev1 각각의 값 조합
for p2_max in range(0, 10):
    for p1_max in range(0, 10):
        subset = rdf[(rdf['prev2'] <= p2_max) & (rdf['prev1'] <= p1_max)]
        if len(subset) >= 30:
            prob = subset['curr_under5'].mean() * 100
            if prob >= 75:
                high_prob.append((f"prev2 <= {p2_max} AND prev1 <= {p1_max}", prob, len(subset)))

# 둘 다 특정 값
for val in range(0, 6):
    subset = rdf[(rdf['prev2'] == val) & (rdf['prev1'] == val)]
    if len(subset) >= 20:
        prob = subset['curr_under5'].mean() * 100
        if prob >= 70:
            high_prob.append((f"prev2 == {val} AND prev1 == {val}", prob, len(subset)))

# 둘 다 0 또는 1
for vals in [[0], [1], [0,1], [0,1,2]]:
    subset = rdf[(rdf['prev2'].isin(vals)) & (rdf['prev1'].isin(vals))]
    if len(subset) >= 20:
        prob = subset['curr_under5'].mean() * 100
        if prob >= 70:
            high_prob.append((f"prev2 in {vals} AND prev1 in {vals}", prob, len(subset)))

# 연속 감소
subset = rdf[(rdf['prev2'] > rdf['prev1']) & (rdf['prev1'] <= 2)]
if len(subset) >= 20:
    prob = subset['curr_under5'].mean() * 100
    high_prob.append((f"감소 추세 + prev1 <= 2", prob, len(subset)))

# 연속 0 crossing
subset = rdf[(rdf['prev2'] == 0) | (rdf['prev1'] == 0)]
if len(subset) >= 20:
    prob = subset['curr_under5'].mean() * 100
    high_prob.append((f"prev2 == 0 OR prev1 == 0", prob, len(subset)))

high_prob.sort(key=lambda x: (-x[1], -x[2]))
for cond, prob, n in high_prob[:20]:
    print(f"  {cond:40s}: {prob:5.1f}% (n={n:4d})")

# 2. prev1만으로 예측
print()
print("=" * 70)
print("직전 캔들(prev1)만으로 예측 → 다음 캔들 < 5 확률")
print("=" * 70)

for val in range(0, 20):
    subset = rdf[rdf['prev1'] == val]
    if len(subset) >= 20:
        prob = subset['curr_under5'].mean() * 100
        print(f"  prev1 == {val:2d}: {prob:5.1f}% (n={len(subset):4d})")

# 3. 0 crossing 캔들 다음
print()
print("=" * 70)
print("0 crossing 캔들 후 분포")
print("=" * 70)

zero_next = rdf[rdf['prev1'] == 0]['curr']
print(f"prev1 == 0 다음 캔들 crossing 분포:")
print(zero_next.value_counts().sort_index().head(15).to_string())

# 4. 실전 시그널 제안
print()
print("=" * 70)
print("실전 시그널 제안 (Low Volatility 진입 조건)")
print("=" * 70)

signals = [
    ("둘 다 <= 2", (rdf['prev2'] <= 2) & (rdf['prev1'] <= 2)),
    ("둘 다 <= 3", (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("prev1 <= 1", rdf['prev1'] <= 1),
    ("prev1 <= 2", rdf['prev1'] <= 2),
    ("합계 <= 4", (rdf['prev2'] + rdf['prev1']) <= 4),
    ("합계 <= 6", (rdf['prev2'] + rdf['prev1']) <= 6),
    ("max <= 2", rdf[['prev2','prev1']].max(axis=1) <= 2),
    ("max <= 3", rdf[['prev2','prev1']].max(axis=1) <= 3),
]

for name, cond in signals:
    subset = rdf[cond]
    prob = subset['curr_under5'].mean() * 100
    coverage = len(subset) / len(rdf) * 100
    print(f"  {name:15s}: {prob:5.1f}% 확률, {coverage:5.1f}% 커버리지 (n={len(subset)})")

