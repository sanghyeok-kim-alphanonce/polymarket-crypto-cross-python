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
            })

rdf = pd.DataFrame(results)

print("=" * 80)
print("전체 분포 (다음 캔들 crossing = N)")
print("=" * 80)
total = len(rdf)
for val in range(15):
    cnt = (rdf['curr'] == val).sum()
    pct = cnt / total * 100
    bar = '█' * int(pct)
    print(f"  {val:2d}: {pct:5.1f}% ({cnt:4d}) {bar}")

print(f"\n  <5: {(rdf['curr'] < 5).mean()*100:.1f}%")

# 조건별 분포
def show_dist(name, mask):
    subset = rdf[mask]
    n = len(subset)
    if n < 30:
        return
    print(f"\n{'='*80}")
    print(f"{name} (n={n})")
    print("=" * 80)
    print(f"  값:  ", end="")
    for val in range(10):
        print(f"{val:6d}", end="")
    print()
    print(f"  확률:", end="")
    for val in range(10):
        pct = (subset['curr'] == val).sum() / n * 100
        print(f"{pct:5.1f}%", end="")
    print()
    
    # 누적
    print(f"  누적:", end="")
    cumsum = 0
    for val in range(10):
        cumsum += (subset['curr'] == val).sum() / n * 100
        print(f"{cumsum:5.1f}%", end="")
    print()

# 주요 조건들
show_dist("전체", rdf['curr'] >= 0)
show_dist("sum3 <= 5", rdf['sum3'] <= 5)
show_dist("sum3 <= 6", rdf['sum3'] <= 6)
show_dist("sum3 <= 9", rdf['sum3'] <= 9)
show_dist("hour==21 & sum3<=9", (rdf['hour'] == 21) & (rdf['sum3'] <= 9))
show_dist("hour==20 & sum3<=9", (rdf['hour'] == 20) & (rdf['sum3'] <= 9))
show_dist("3개 모두 <=2", (rdf['prev3'] <= 2) & (rdf['prev2'] <= 2) & (rdf['prev1'] <= 2))
show_dist("3개 모두 <=3", (rdf['prev3'] <= 3) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3))
show_dist("prev1 <= 2", rdf['prev1'] <= 2)

# 개별 값 확률 상세
print()
print("=" * 80)
print("조건별 curr=0,1,2,3,4 개별 확률")
print("=" * 80)
print(f"{'조건':<35} {'0':>6} {'1':>6} {'2':>6} {'3':>6} {'4':>6} {'<5':>7} {'n':>6}")
print("-" * 85)

conditions = [
    ("전체", rdf['curr'] >= 0),
    ("sum3 <= 5", rdf['sum3'] <= 5),
    ("sum3 <= 6", rdf['sum3'] <= 6),
    ("sum3 <= 9", rdf['sum3'] <= 9),
    ("3개 모두 <=2", (rdf['prev3'] <= 2) & (rdf['prev2'] <= 2) & (rdf['prev1'] <= 2)),
    ("3개 모두 <=3", (rdf['prev3'] <= 3) & (rdf['prev2'] <= 3) & (rdf['prev1'] <= 3)),
    ("hour==20", rdf['hour'] == 20),
    ("hour==21", rdf['hour'] == 21),
    ("hour==21 & sum3<=6", (rdf['hour'] == 21) & (rdf['sum3'] <= 6)),
    ("hour==21 & sum3<=9", (rdf['hour'] == 21) & (rdf['sum3'] <= 9)),
    ("hour==20 & sum3<=9", (rdf['hour'] == 20) & (rdf['sum3'] <= 9)),
]

for name, mask in conditions:
    subset = rdf[mask]
    n = len(subset)
    if n < 20:
        continue
    probs = [(subset['curr'] == v).sum() / n * 100 for v in range(5)]
    under5 = (subset['curr'] < 5).sum() / n * 100
    print(f"{name:<35} {probs[0]:5.1f}% {probs[1]:5.1f}% {probs[2]:5.1f}% {probs[3]:5.1f}% {probs[4]:5.1f}% {under5:6.1f}% {n:6d}")

