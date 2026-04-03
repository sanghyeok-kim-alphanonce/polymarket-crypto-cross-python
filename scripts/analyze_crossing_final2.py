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

print("=" * 90)
print("직전 2캔들 (prev2, prev1) 조건별 분석")
print("=" * 90)
print(f"{'조건':<40} {'0':>6} {'1':>6} {'2':>6} {'3':>6} {'4':>6} {'<5':>7} {'n':>6}")
print("-" * 90)

conditions = [
    ("전체", rdf['curr'] >= 0),
    
    # 둘 다 조건
    ("둘 다 <=1", (rdf['prev2'] <= 1) & (rdf['prev1'] <= 1)),
    ("둘 다 <=2", (rdf['prev2'] <= 2) & (rdf['prev1'] <= 2)),
    ("둘 다 <=3", (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("둘 다 <=4", (rdf['prev2'] <= 4) & (rdf['prev1'] <= 4)),
    ("둘 다 <=5", (rdf['prev2'] <= 5) & (rdf['prev1'] <= 5)),
    
    ("---", rdf['curr'] < -999),  # separator
    
    # 둘 중 하나만 조건
    ("하나만 <=1 (다른건 >1)", ((rdf['prev2'] <= 1) & (rdf['prev1'] > 1)) | ((rdf['prev2'] > 1) & (rdf['prev1'] <= 1))),
    ("하나만 <=2 (다른건 >2)", ((rdf['prev2'] <= 2) & (rdf['prev1'] > 2)) | ((rdf['prev2'] > 2) & (rdf['prev1'] <= 2))),
    ("하나만 <=3 (다른건 >3)", ((rdf['prev2'] <= 3) & (rdf['prev1'] > 3)) | ((rdf['prev2'] > 3) & (rdf['prev1'] <= 3))),
    
    ("---", rdf['curr'] < -999),  # separator
    
    # 둘 다 > 조건
    ("둘 다 >3", (rdf['prev2'] > 3) & (rdf['prev1'] > 3)),
    ("둘 다 >5", (rdf['prev2'] > 5) & (rdf['prev1'] > 5)),
    
    ("---", rdf['curr'] < -999),  # separator
    
    # prev1만
    ("prev1 <= 3", rdf['prev1'] <= 3),
    ("prev1 > 3", rdf['prev1'] > 3),
    
    ("---", rdf['curr'] < -999),  # separator
    
    # 합계 조건
    ("sum2 <= 4", rdf['sum2'] <= 4),
    ("sum2 <= 6", rdf['sum2'] <= 6),
    ("sum2 <= 8", rdf['sum2'] <= 8),
]

for name, mask in conditions:
    if name == "---":
        print("-" * 90)
        continue
    subset = rdf[mask]
    n = len(subset)
    if n < 20:
        continue
    probs = [(subset['curr'] == v).sum() / n * 100 for v in range(5)]
    under5 = (subset['curr'] < 5).sum() / n * 100
    print(f"{name:<40} {probs[0]:5.1f}% {probs[1]:5.1f}% {probs[2]:5.1f}% {probs[3]:5.1f}% {probs[4]:5.1f}% {under5:6.1f}% {n:6d}")

# 시간대 조합
print()
print("=" * 90)
print("시간대 + 조건 조합")
print("=" * 90)
print(f"{'조건':<40} {'0':>6} {'1':>6} {'2':>6} {'3':>6} {'4':>6} {'<5':>7} {'n':>6}")
print("-" * 90)

time_conditions = [
    ("hour==21 & 둘 다 <=3", (rdf['hour'] == 21) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("hour==21 & 하나만 <=3", (rdf['hour'] == 21) & (((rdf['prev2'] <= 3) & (rdf['prev1'] > 3)) | ((rdf['prev2'] > 3) & (rdf['prev1'] <= 3)))),
    ("hour==20 & 둘 다 <=3", (rdf['hour'] == 20) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("hour==22 & 둘 다 <=3", (rdf['hour'] == 22) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
]

for name, mask in time_conditions:
    subset = rdf[mask]
    n = len(subset)
    if n < 20:
        continue
    probs = [(subset['curr'] == v).sum() / n * 100 for v in range(5)]
    under5 = (subset['curr'] < 5).sum() / n * 100
    print(f"{name:<40} {probs[0]:5.1f}% {probs[1]:5.1f}% {probs[2]:5.1f}% {probs[3]:5.1f}% {probs[4]:5.1f}% {under5:6.1f}% {n:6d}")

