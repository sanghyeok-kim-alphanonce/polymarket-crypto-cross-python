import pandas as pd

df = pd.read_csv('/home/yeonwoo/poly-test/5m_crossing_count_v2.csv')
df['candle_start'] = pd.to_datetime(df['candle_start'])

# btc만 확인
btc = df[df['coin'] == 'btc'].sort_values('candle_start').reset_index(drop=True)

# 연속 0 구간 찾기
streaks = []
start_idx = None
for i, row in btc.iterrows():
    if row['crossing_count'] == 0:
        if start_idx is None:
            start_idx = i
    else:
        if start_idx is not None:
            streak_len = i - start_idx
            if streak_len >= 3:  # 3개 이상 연속 0
                streaks.append({
                    'start': btc.iloc[start_idx]['candle_start'],
                    'end': btc.iloc[i-1]['candle_start'],
                    'length': streak_len,
                    'duration_hours': streak_len * 5 / 60
                })
            start_idx = None

print(f"BTC 연속 0 구간 (3캔들+ = 15분+)")
print("=" * 70)
print(f"{'시작':<25} {'종료':<25} {'캔들수':>8} {'시간':>8}")
print("-" * 70)

# 긴 구간 먼저 출력
for s in sorted(streaks, key=lambda x: -x['length'])[:20]:
    print(f"{str(s['start']):<25} {str(s['end']):<25} {s['length']:>8} {s['duration_hours']:>7.1f}h")

print()
print(f"총 연속0 구간: {len(streaks)}개")
print(f"1시간+ 구간: {sum(1 for s in streaks if s['duration_hours'] >= 1)}개")
print(f"3시간+ 구간: {sum(1 for s in streaks if s['duration_hours'] >= 3)}개")

# 하루 중 0 비율 확인
print()
print("=" * 70)
print("시간대별 crossing=0 비율 (BTC)")
print("=" * 70)
btc['hour'] = btc['candle_start'].dt.hour
for hour in range(24):
    subset = btc[btc['hour'] == hour]
    zero_pct = (subset['crossing_count'] == 0).mean() * 100
    bar = '█' * int(zero_pct/3)
    print(f"  {hour:02d}시: {zero_pct:5.1f}% {bar}")

