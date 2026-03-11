# No-Downtime 배포 및 추상화 개선

## 왜 이게 문제인가

Paper trading 단계에서는 서비스 껐다 켜도 손해가 없다. 문제는 real trading으로 넘어갔을 때.

전략 코드 버그 수정, 파라미터 튜닝, 새 전략 추가 — 운영 중 수정은 반드시 발생한다. 현재 구조에서 P1(수집기)이나 P4(전략)를 수정하려면 컨테이너를 내렸다 올려야 한다. 그 사이 데이터 수집이 끊기고, 진행 중인 포지션의 오더북 피드가 사라지고, 인메모리 전략 상태(밴드 위치, 방문 이력)가 유실된다.

전략 1~2개면 수동으로 캔들 경계 맞춰서 교체할 수 있다. 하지만 전략을 수십, 수백 개 병렬로 돌리는 단계가 되면:

- 전략 A 하나 고치려고 수집기를 내리면 전략 B~Z도 전부 블라인드
- 전략별로 다른 코인/타임프레임을 보는데 수집기는 하나 — config 바꾸려면 전부 재시작
- P4 재시작하면 현재 캔들의 밴드 상태, 투자액 추적이 날아감 — 같은 밴드에 재진입
- 두 인스턴스를 overlap 시키면 중복 주문이 나감 — dedup 장치가 없으므로

결국 "수정할 때마다 전체를 멈추고 다시 시작"이 되며, 전략 수가 늘수록 이 비용이 선형으로 증가한다. No-downtime 배포가 안 되면 운영 스케일링이 막힌다.

---

## 1. 배포 전략: 세 가지 접근법

코드 업데이트 시 no-downtime을 달성하는 방법은 서비스 성격에 따라 다르다.

### 1-1. 단순 재시작 (P2, multi_exchange, dashboard)

DB/Redis 쓰기가 모두 idempotent인 서비스는 그냥 재시작하면 된다. 겹침도 안전하고, 간극도 문제없다.

### 1-2. 듀얼 컨테이너 배포 (P1, P4 인프라 변경 시)

새 컨테이너를 먼저 띄우고, 준비되면 기존 것을 내리는 방식.

```
P1-old ──→ Redis ──→ P4
P1-new ──→ Redis ──→ P4    (겹침 구간)
           Redis ──→ P4    (old 종료 후)
```

**현재 상정: 배포 시점에만 일시적으로 듀얼 구동.** 상시 듀얼이 아니라 교체 과정의 과도기로만 사용한다.

이 경우에도 겹침 구간에서 중복이 발생한다:
- P1 듀얼: P4가 같은 오더북을 2회 수신 → 중복 진입 시도
- P4 듀얼: 같은 조건에서 2배 주문 생성

따라서 듀얼 배포의 전제조건:
1. **P4 consumer 메시지 dedup** (timestamp 비교로 P1 중복 메시지 무시)
2. **DB 주문 dedup** (UNIQUE 제약으로 P4 중복 주문 방지)
3. **전략 상태 외부화** (Redis에 저장 → 새 인스턴스가 이어받기)
4. **시작 시 오더북 캐시 로드** (Redis에서 즉시 최신 데이터 확보)

### 1-3. 전략 Hot-Reload (P4 전략 코드 변경 시)

P4에서 가장 자주 바뀌는 건 전략 코드다 (파라미터 튜닝, 로직 수정, 새 전략 추가). P4의 subscriber 루프나 인프라 코드가 바뀔 일은 드물다.

이 경우 **컨테이너를 새로 띄울 필요 없이**, 실행 중인 P4 안에서 전략 모듈만 교체할 수 있다.

```
P4 프로세스 (계속 실행):
  redis_subscriber()  ← 유지 (데이터 수신 끊김 없음)
  ob_cache             ← 유지 (최신 오더북 보존)
  strategies[coin]     ← 이것만 교체
```

이게 "달리는 차의 부품 교체"다. 듀얼 배포는 "새 차를 옆에 세워두고 갈아타기"이고, hot-reload는 "엔진만 빼서 새 엔진으로 교체"하는 것.

```python
# 개념적 구현
async def hot_reload_strategy(self, coin: str, new_strategy_name: str, **kwargs):
    old = self.strategies[coin]

    try:
        # 1. 전략 모듈 리로드 + 새 인스턴스 생성
        importlib.reload(strategies.v12_5)
        new = get_strategy(new_strategy_name, **kwargs)

        # 2. 상태 이전 (candle 중간이면)
        new._prev_mid_prices = old._prev_mid_prices
        new._last_trade_time = old._last_trade_time
        new._current_candle = old._current_candle

        # 3. 원자적 교체
        self.strategies[coin] = new

    except Exception as e:
        # 실패 시 롤백 — old 객체 아직 메모리에 있음
        logger.error(f"Hot-reload failed for {coin}: {e}")
        self.strategies[coin] = old
```

**트리거 방식:**
- Redis 명령: `PUBLISH ch:admin:reload '{"coin":"btc","strategy":"v12_5"}'`
- 파일 watch: `strategies/` 디렉토리 변경 감지
- HTTP API: health 엔드포인트에 reload 핸들러 추가

**장점:**
- 데이터 수신 끊김 없음 (subscriber 유지)
- 상태 명시적 이전 (old → new 직접 복사)
- 실패 시 즉시 롤백 (old 객체 메모리에 보존)
- dedup 불필요 (인스턴스가 1개)
- 듀얼 배포보다 훨씬 가볍고 빠름

**한계:**
- P4의 subscriber 루프나 인프라 코드가 바뀌면 사용 불가 → 듀얼 배포 필요
- Python `importlib.reload`는 모듈 간 참조가 복잡하면 불안정할 수 있음

### P1은 왜 hot-reload가 안 되는가

P1의 "부품"은 WS 연결 자체다. 코드를 바꾸려면 새 WS 연결이 필요하고, WS 핸들러와 파싱 로직이 연결에 바인딩되어 있다. 연결을 교체하는 것 = 프로세스를 교체하는 것이므로, P1에서는 "부품 교체"와 "듀얼 배포"가 사실상 같은 문제다.

다만 P1은 이미 `market_refresh_loop`에서 15분마다 WS 연결을 hot-swap하는 구조가 있으므로, 캔들 경계에 맞춰 교체하면 clean한 전환이 가능하다.

### 서비스별 교체 방법 요약

| 서비스 | 주요 변경 대상 | 교체 방법 |
|--------|---------------|----------|
| P1 (rtds) | WS 연결 + 파싱 로직 | 듀얼 컨테이너 (캔들 경계) |
| P4 전략 코드 | 전략 로직/파라미터 | **Hot-Reload** (가장 빈번) |
| P4 인프라 코드 | subscriber, 정산 등 | 듀얼 컨테이너 |
| P2, multi_exchange | 수집 로직 | 단순 재시작 |
| dashboard | UI | 단순 재시작 |

---

## 2. 향후 고려: 상시 듀얼 구동 (HA)

현재는 **배포 시점의 과도기**로만 듀얼을 상정했다. 하지만 향후 고가용성(HA)을 위해 **상시 듀얼 구동**이 필요해질 수 있다 (P1 장애 시 자동 failover 등).

상시 듀얼은 배포 듀얼과 근본적으로 다른 문제:

| | 배포 듀얼 (현재 상정) | 상시 듀얼 (향후) |
|---|---|---|
| 목적 | 코드 교체 시 간극 제거 | 장애 내성, 자동 failover |
| 겹침 시간 | 수초~수십초 | 항상 |
| 중복 처리 | DB UNIQUE + consumer dedup으로 충분 | 전용 dedup 서비스 필요 |
| 주문 중복 | 겹침 구간에만 발생, 빈도 낮음 | 상시 발생, 빈도 높음 |

상시 듀얼 구동 시 필요한 추가 아키텍처:

```
P4-a ──→ Order Dedup Service ──→ DB
P4-b ──→ Order Dedup Service ──→ DB
```

- **Leader Election**: Redis `SET NX` 또는 etcd로 active/standby 결정
- **Order Gateway**: 주문을 중앙 dedup 서비스를 거쳐서만 실행
- **Health Monitoring**: standby가 active 장애 감지 시 자동 승격

이 수준의 HA는 현재 단계에서는 과설계. 배포 듀얼 + DB dedup으로 시작하고, 운영 규모가 커지면 점진적으로 도입한다.

---

## 3. 문제점

### 3-1. P1 듀얼 인스턴스 시 중복 publish

P1 두 개가 동시에 돌면 P4가 같은 오더북 메시지를 2회 수신한다.

- `ch:orderbook:*` 중복 → `_check_entry()` 2회 호출 → 중복 진입 시도
- `ch:candle_boundary` 중복 → 전략 2회 리셋 (치명적이진 않으나 불필요)
- DB flush 중복 → `ON CONFLICT DO UPDATE`로 데이터 깨지진 않지만 값 뒤섞임 가능

**근본 원인**: P4(consumer)에 메시지 dedup 로직이 없음.

### 3-2. P4 듀얼 인스턴스 시 중복 주문

P4 두 개가 같은 `ch:orderbook:*`을 subscribe하면 동일 조건에서 2배 주문 생성.

```
P4(old) → INSERT test_paper_trades (btc, UP, band_50_55)
P4(new) → INSERT test_paper_trades (btc, UP, band_50_55)  ← 중복
```

**근본 원인**: `test_paper_trades`에 비즈니스 키 UNIQUE 제약이 없음 (`id`는 SERIAL).

### 3-3. COINS/TIMEFRAMES 하드코딩

```python
# rtds/config.py
COINS = ['btc', 'eth', 'sol', 'xrp']     # 하드코딩
TIMEFRAMES = ['15m']                       # 하드코딩

# paper_trader/config.py
COINS = ['btc', 'eth', 'sol', 'xrp']     # 동일하게 중복 하드코딩
TIMEFRAMES = ['15m']                       # 동일하게 중복 하드코딩
```

- P1과 P4가 각자 config.py에 같은 값을 중복 정의
- 한쪽만 바꾸면 불일치 → P4가 존재하지 않는 채널 구독하거나 수집 안 되는 마켓 기대
- 이미지 재빌드 없이 코인/타임프레임 변경 불가 (env var 아님)

### 3-4. 전략-마켓 매핑 고정

```python
# paper_trader/main.py
for coin in COINS:
    self.strategies[coin] = self._create_strategy()  # 모든 코인에 동일 전략
```

- 코인별 다른 전략 적용 불가
- 코인별 다른 파라미터(band_width 등) 불가
- 전략 A는 btc/eth 15m, 전략 B는 sol/xrp 1h 같은 구성 불가

### 3-5. P4 인메모리 전략 상태 유실

P4 재시작 시 유실되는 상태:
- `ob_cache`: 오더북 캐시 → 새 메시지 수신 시 수초 내 복구
- `strategies[coin]._current_band`: 현재 밴드 위치
- `strategies[coin]._visited_bands`: 방문한 밴드 (V2 재진입 방지)
- `strategies[coin]._candle_spent`: 캔들당 투자액

재시작 후 밴드가 초기화되어 이미 진입한 밴드에 재진입 가능.
캔들 경계에 맞춰 교체하면 어차피 리셋이므로 문제 없지만, 임의 시점 교체 시 중복 진입.

---

## 4. 개선 방안

### 4-1. P4 Consumer 측 메시지 dedup

P1 듀얼 인스턴스 문제를 consumer 측에서 해결.

```python
# paper_trader/main.py - _handle_orderbook_message 수정

async def _handle_orderbook_message(self, data: Dict):
    coin, tf, side = data.get("coin"), data.get("timeframe"), data.get("side")
    cache_key = f"{coin}_{tf}_{side}"
    new_ts = data.get("timestamp", 0)

    # dedup: 기존 캐시보다 오래된 메시지 무시
    existing = self.ob_cache.get(cache_key)
    if existing and existing.get("timestamp", 0) >= new_ts:
        return

    self.ob_cache[cache_key] = { ... }
    await self._check_entry(coin, tf)
```

candle_boundary도 동일하게 timestamp 비교:

```python
async def _handle_candle_boundary(self, data: Dict):
    ts = data.get("timestamp", 0)
    if ts <= self._last_boundary_ts:
        return  # 중복 무시
    self._last_boundary_ts = ts
    self.ob_cache.clear()
```

### 4-2. DB 레벨 주문 dedup

`test_paper_trades`에 비즈니스 키 UNIQUE 제약 추가:

```sql
-- init.sql에 추가
CREATE UNIQUE INDEX IF NOT EXISTS idx_test_dedup
ON test_paper_trades (strategy_name, coin, timeframe, candle_start_time, side, reason)
WHERE status != 'CANCELLED';
```

주문 INSERT를 `ON CONFLICT DO NOTHING`으로 변경:

```python
# paper_trader/main.py - create_order 수정
trade_id = await conn.fetchval("""
    INSERT INTO test_paper_trades (...)
    VALUES (...)
    ON CONFLICT (strategy_name, coin, timeframe, candle_start_time, side, reason)
      WHERE status != 'CANCELLED'
    DO NOTHING
    RETURNING id
""", ...)

if trade_id is None:
    logger.info(f"[{coin}] ORDER skipped (duplicate): {side} @ {order_price:.4f}")
    return None
```

### 4-3. COINS/TIMEFRAMES를 env var로 추출

```python
# 공통 패턴 (rtds/config.py, paper_trader/config.py 모두)
COINS = os.getenv("COINS", "btc,eth,sol,xrp").split(",")
TIMEFRAMES = os.getenv("TIMEFRAMES", "15m").split(",")
```

docker-compose.yml:

```yaml
rtds:
  environment:
    COINS: "btc,eth,sol,xrp"
    TIMEFRAMES: "15m"

paper_trader:
  environment:
    COINS: "btc,eth,sol,xrp"
    TIMEFRAMES: "15m"
```

이상적으로는 `packages/` 레벨의 공유 config로 통합:

```python
# packages/service_common/config.py
COINS = os.getenv("COINS", "btc,eth,sol,xrp").split(",")
TIMEFRAMES = os.getenv("TIMEFRAMES", "15m").split(",")
```

### 4-4. 전략-마켓 매핑 분리

```python
# paper_trader/config.py
# 환경변수 예시: STRATEGY_MAP='{"btc":{"strategy":"v12_5","min_entry_price":0.35},"sol":{"strategy":"v12_5","min_entry_price":0.40}}'
STRATEGY_MAP = json.loads(os.getenv("STRATEGY_MAP", "{}"))
```

```python
# paper_trader/main.py
for coin in COINS:
    coin_cfg = STRATEGY_MAP.get(coin, {})
    strategy_name = coin_cfg.get("strategy", STRATEGY_NAME)
    self.strategies[coin] = get_strategy(strategy_name, **coin_cfg)
```

### 4-5. 캔들 경계 배포 자동화 스크립트

현재 가장 현실적인 no-downtime 방법. 별도 스크립트 없이도 수동으로 가능하지만, 자동화하면:

```bash
#!/bin/bash
# deploy.sh <service_name>
SERVICE=$1

# 1. 다음 캔들 경계까지 대기
python3 -c "
from polymarket_common.time_utils import get_seconds_to_next_candle
import time
wait = get_seconds_to_next_candle('15m') + 2  # 경계 2초 후
print(f'Waiting {wait:.0f}s for candle boundary...')
time.sleep(wait)
"

# 2. 새 이미지 빌드
docker compose build $SERVICE

# 3. 새 컨테이너 시작 (old은 아직 살아있음)
docker compose up -d --no-deps $SERVICE

# 4. health check 대기
echo "Waiting for $SERVICE to be healthy..."
until docker inspect --format='{{.State.Health.Status}}' cointest-$SERVICE 2>/dev/null | grep -q healthy; do
  sleep 1
done

echo "$SERVICE deployed successfully"
```

---

### 4-6. 전략 상태 Redis 외부화

P4 재시작 시 인메모리 전략 상태 유실이 핵심 문제.

현재 유실되는 상태:
```python
strategies[coin]._prev_mid_prices = {"btc": 0.52}
strategies[coin]._last_trade_time = {"btc": 1738940000.0}
strategies[coin]._current_candle = "btc_15m_2026-02-20T12:00:00"
```

Redis에 저장:
```
HSET "strategy:v12_5:btc:state"
  prev_mid_prices '{"btc": 0.52}'
  last_trade_time '{"btc": 1738940000.0}'
  current_candle "btc_15m_2026-02-20T12:00:00"
```

변경 범위:
- `v12_5.py`: 상태 변경 시 Redis sync 추가
- `paper_trader/main.py`: 시작 시 Redis에서 상태 로드
- 전략 로직 자체는 변경 없음

### 4-7. 시작 시 Redis에서 오더북 캐시 즉시 로드

P1이 이미 `setex("orderbook:{key}", 60, snapshot)` 하고 있음 (rtds/main.py:534).
P4 시작 시 이 키를 읽으면 pub/sub 메시지 대기 없이 즉시 최신 오더북 보유.

```python
# paper_trader/main.py 시작 시 추가
for coin in COINS:
    for tf in TIMEFRAMES:
        for side in ["up", "down"]:
            key = f"orderbook:{coin}_{tf}_{side}"
            data = await redis.get(key)
            if data:
                self.ob_cache[f"{coin}_{tf}_{side}"] = json.loads(data)
```

---

## 5. 우선순위

| 순위 | 작업 | 효과 | 난이도 |
|------|------|------|--------|
| 1 | 전략 상태 Redis 외부화 (4-6) | 재시작해도 상태 유지 | 중간 |
| 2 | DB 주문 dedup (4-2) | 듀얼 인스턴스 중복 방지 | 낮음 |
| 3 | 시작 시 오더북 캐시 로드 (4-7) | 시작 즉시 최신 데이터 | 낮음 |
| 4 | P4 전략 hot-reload (1-3) | 가장 빈번한 변경을 무중단 처리 | 중간 |
| 5 | COINS/TIMEFRAMES env var 추출 (4-3) | 재빌드 없이 설정 변경 | 낮음 |
| 6 | P4 메시지 dedup (4-1) | P1 임의 시점 교체 가능 | 낮음 |
| 7 | 전략-마켓 매핑 (4-4) | 코인별 전략 파라미터 분리 | 중간 |

1~3을 적용하면 **단순 재시작만으로도 실질적 no-downtime**이 된다 (상태 유지 + 중복 방지 + 즉시 데이터 확보).
4를 추가하면 **전략 코드 변경 시 재시작 자체가 불필요**해진다.
