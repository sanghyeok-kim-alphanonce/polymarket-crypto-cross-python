import pandas as pd
import numpy as np
from collections import defaultdict

# CSV 로드
df = pd.read_csv('/home/yeonwoo/poly-test/5m_crossing_count.csv')
df['candle_start'] = pd.to_datetime(df['candle_start'])

results = []

for coin in df['coin'].unique():
    coin_df = df[df['coin'] == coin].sort_values('candle_start').reset_index(drop=True)
    
    for i in range(2, len(coin_df)):
        prev2 = coin_df.iloc[i-2]['crossing_count']
        prev1 = coin_df.iloc[i-1]['crossing_count']
        curr = coin_df.iloc[i]['crossing_count']
        
        # 연속 캔들인지 확인 (5분 간격)
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
print(f"총 샘플: {len(rdf)}")
print(f"다음 캔들 < 5: {rdf['curr_under5'].sum()} ({rdf['curr_under5'].mean()*100:.1f}%)")
print()

# 1. 이전 2캔들 합계별 분석
print("=" * 70)
print("1. 이전 2캔들 합계 (prev2 + prev1) → 다음 캔들 < 5 확률")
print("=" * 70)
rdf['prev_sum'] = rdf['prev2'] + rdf['prev1']

for s in sorted(rdf['prev_sum'].unique()):
    subset = rdf[rdf['prev_sum'] == s]
    if len(subset) >= 10:
        prob = subset['curr_under5'].mean() * 100
        print(f"  합계 {s:2d}: {prob:5.1f}% (n={len(subset):4d})")

# 2. 이전 2캔들 최대값별 분석
print()
print("=" * 70)
print("2. 이전 2캔들 최대값 (max) → 다음 캔들 < 5 확률")
print("=" * 70)
rdf['prev_max'] = rdf[['prev2', 'prev1']].max(axis=1)

for m in sorted(rdf['prev_max'].unique()):
    subset = rdf[rdf['prev_max'] == m]
    if len(subset) >= 10:
        prob = subset['curr_under5'].mean() * 100
        print(f"  max {m:2d}: {prob:5.1f}% (n={len(subset):4d})")

# 3. 둘 다 N 이하일 때
print()
print("=" * 70)
print("3. 이전 2캔들 모두 N 이하 → 다음 캔들 < 5 확률")
print("=" * 70)

for threshold in range(1, 10):
    subset = rdf[(rdf['prev2'] <= threshold) & (rdf['prev1'] <= threshold)]
    if len(subset) >= 10:
        prob = subset['curr_under5'].mean() * 100
        print(f"  둘 다 <= {threshold}: {prob:5.1f}% (n={len(subset):4d})")

# 4. 감소 추세 (prev2 > prev1)
print()
print("=" * 70)
print("4. 추세 분석 → 다음 캔들 < 5 확률")
print("=" * 70)

decreasing = rdf[rdf['prev2'] > rdf['prev1']]
increasing = rdf[rdf['prev2'] < rdf['prev1']]
flat = rdf[rdf['prev2'] == rdf['prev1']]

print(f"  감소 추세 (prev2 > prev1): {decreasing['curr_under5'].mean()*100:5.1f}% (n={len(decreasing)})")
print(f"  증가 추세 (prev2 < prev1): {increasing['curr_under5'].mean()*100:5.1f}% (n={len(increasing)})")
print(f"  유지 (prev2 == prev1):     {flat['curr_under5'].mean()*100:5.1f}% (n={len(flat)})")

# 5. 복합 조건 탐색
print()
print("=" * 70)
print("5. 고확률 조건 조합 (확률 70%+ & n >= 20)")
print("=" * 70)

combos = []
for max_val in range(1, 15):
    for sum_val in range(2, 30):
        subset = rdf[(rdf['prev_max'] <= max_val) & (rdf['prev_sum'] <= sum_val)]
        if len(subset) >= 20:
            prob = subset['curr_under5'].mean() * 100
            if prob >= 70:
                combos.append((max_val, sum_val, prob, len(subset)))

combos.sort(key=lambda x: (-x[2], -x[3]))
for max_val, sum_val, prob, n in combos[:15]:
    print(f"  max <= {max_val:2d} AND sum <= {sum_val:2d}: {prob:5.1f}% (n={n:4d})")

# 6. 코인별 차이
print()
print("=" * 70)
print("6. 코인별 기본 확률 (다음 캔들 < 5)")
print("=" * 70)
for coin in rdf['coin'].unique():
    subset = rdf[rdf['coin'] == coin]
    prob = subset['curr_under5'].mean() * 100
    print(f"  {coin}: {prob:5.1f}% (n={len(subset)})")

