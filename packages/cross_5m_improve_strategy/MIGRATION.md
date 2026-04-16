# cross_5m_improve → trade-infra Migration Guide

이 문서는 `packages/cross_5m_improve_strategy/` 패키지를 **trade-infra repo**로 이식하고, trade-infra의 데이터 파이프라인에 와이어링하기 위한 컨텍스트 문서입니다. 이 문서와 해당 패키지 디렉토리를 trade-infra 세션에 넘겨서 와이어링 구현을 요청하세요.

---

## 0. 한 줄 요약

> **5분봉 Crossing 전략 (Max5 + 속도필터 + 1초 cooltime + 헤지).** 외부 I/O는 전부 Port(Protocol)로 추상화되어 있어, trade-infra가 5개 포트 구현체만 작성하면 그대로 돌아갑니다.

---

## 1. 패키지 구조

```
cross_5m_improve_strategy/
├── __init__.py         ← Public API (import 해서 쓰는 것들)
├── types.py            ← dataclass: TradeSignal/CrossingEvent/Orderbook/OrderResult/CandleState
├── config.py           ← StrategyParams (frozen dataclass, 전략 파라미터만)
├── ports.py            ← Protocol 5개 (IClockPort/IMarketDataPort/IOrderExecutor/ITradeRecorder/INotifier)
├── strategy.py         ← CrossingStrategy5MImprove (순수 로직, 외부 의존성 0)
├── runner.py           ← CrossingRunner (이벤트 수신 → 포트 조회 → 전략 호출 → 주문/기록)
└── tests/
    └── test_strategy.py   ← 12개 유닛 테스트 (모두 통과)
```

**설계 원칙:**
- `strategy.py`는 외부 import 0개 (Python 표준 라이브러리만). 이게 migration 안전망.
- 시계는 `IClockPort`로 주입. `time.time()` 직접 호출 없음. → 백테스트 시뮬 시계로 대체 가능.
- `runner.py`는 얇은 Facade. trade-infra 데이터 모델 ↔ `CrossingEvent/Orderbook` 변환만 어댑터에서 해주면 됨.

---

## 2. 전략 요약 (비즈니스 로직)

### 2.1 타임라인

```
0s ───────────────── 250s ───── 290s ─── 300s
│ 정상 진입         │ 헤지 구간 │ 금지   │
│ #1: x10           │ x10       │ 진입   │
│ #2~4: x20         │ HEDGE_TIME│ 없음   │
│ #5: x10 HEDGE_MAX5│           │        │
└───────────────────┴───────────┴────────┘
```

### 2.2 헤지 조건 (3가지 중 하나라도 맞으면 x10 + 캔들 잠금)

| 조건 | 트리거 | reason |
|---|---|---|
| 시간 헤지 | `elapsed ≥ 250s` | `HEDGE_TIME` |
| Max5 헤지 | `count ≥ 4` (5번째 진입) | `HEDGE_MAX5` |
| 속도 헤지 | 최근 20s 내 crossing ≥ 3회 | `HEDGE_SPEED` |

헤지 체결되면 `state.hedged = True` → 이후 crossing 전부 무시.

### 2.3 Cooltime (1초)

- 주문 직전에 `start_cooltime()` 호출 (동시주문 방지)
- Cooltime 중 들어온 crossing은 `pending_direction`에 저장
- 1초 후 타이머가 `check_pending_after_cooltime()` 호출
  - pending이 `last_trade_direction`과 **반대**면 실행
  - 같은 방향이면 drop

### 2.4 High-volatility filter

- 직전 N(기본 2)캔들 crossing 수 중 하나라도 threshold(기본 3) 초과면 해당 캔들 **전체 진입 skip**
- runner가 `ITradeRecorder.get_recent_crossing_counts()`로 부트 시 로드
- 캔들 경계마다 자동 갱신

### 2.5 베팅 수량 매트릭스

| 회차 | 0–250s | 250–290s (HEDGE_TIME) | 20s/3회 crossing (HEDGE_SPEED) |
|:---:|:---:|:---:|:---:|
| #1 | 10 | 10 | 10 |
| #2~4 | 20 | 10 | 10 |
| #5 | 10 (HEDGE_MAX5) | 10 | 10 |
| 290s+ | 진입 금지 | 진입 금지 | 진입 금지 |

---

## 3. Port 계약 (trade-infra가 구현해야 할 5개)

### 3.1 `IClockPort`
```python
class IClockPort(Protocol):
    def monotonic(self) -> float: ...   # cooltime 경과 측정
    def now_utc(self) -> datetime: ...  # 캔들 경계 판단
```
- **Live 구현:** `time.monotonic()` + `datetime.now(timezone.utc)` 그대로.
- **Backtest 구현:** 시뮬레이션 시계 → 결정론적 테스트.

### 3.2 `IMarketDataPort`
```python
class IMarketDataPort(Protocol):
    def get_orderbook(self, coin, tf, side) -> Optional[Orderbook]: ...
    def get_token_id(self, coin, tf, side) -> Optional[str]: ...
```
- trade-infra의 호가 캐시(Redis/메모리 등)를 조회해서 `Orderbook` dataclass로 반환.
- `token_id`는 해당 venue의 주문 대상 식별자 (Polymarket에선 ERC1155 토큰ID).

### 3.3 `IOrderExecutor`
```python
class IOrderExecutor(Protocol):
    async def place_gtc(self, coin, token_id, price, contracts) -> OrderResult: ...
```
- GTC maker 주문 실행. 실패 시 `OrderResult(success=False, error=...)` 반환.
- 현 구현은 Polymarket CLOB 기반 → trade-infra의 venue adapter로 교체.

### 3.4 `ITradeRecorder`
```python
class ITradeRecorder(Protocol):
    async def record_trade(coin, tf, signal, order_result, candle_start, candle_end, crossing_info, latency_ms): ...
    async def save_crossing_stats(coin, tf, candle_key, candle_start, candle_end, crossing_count, trade_count): ...
    async def get_recent_crossing_counts(coin, limit=2) -> list[int]: ...
```
- **없어도 전략 동작 OK** (no-op 구현 가능). 단, `get_recent_crossing_counts`는 `[]` 반환 시 high-vol filter가 자동 비활성화됨.
- 현 구현은 `test_paper_trades`, `candle_crossing_stats` 두 테이블 사용 → trade-infra 테이블 스키마에 맞춰 INSERT/SELECT 구현.

### 3.5 `INotifier`
```python
class INotifier(Protocol):
    def notify(self, message: str) -> None: ...
```
- Telegram/Slack/로그. 미구현시 `def notify(self, m): pass`.

---

## 4. 이벤트 계약 (trade-infra가 공급해야 할 3종)

trade-infra가 자기 데이터 파이프라인에서 아래 3가지 이벤트를 만들어 `CrossingRunner`에 전달하기만 하면 됩니다.

### 4.1 Crossing event
```python
from cross_5m_improve_strategy import CrossingEvent, CrossingRunner

event = CrossingEvent(
    coin="btc",
    timeframe="5m",
    direction="up",          # "up" | "down"
    candle_start=...,        # datetime (UTC)
    candle_end=...,          # datetime (UTC)
    elapsed_ms=45_123,       # 캔들 시작 이후 경과 밀리초
    candle_open=45000.0,     # 알림용, None 허용
    prev_price=44998.0,      # 알림용, None 허용
    current_price=45002.0,   # 알림용, None 허용
    prev_elapsed_ms=44_000,  # 알림용, None 허용
    curr_elapsed_ms=45_123,  # 알림용, None 허용
)
await runner.handle_crossing(event)
```

### 4.2 Candle boundary
```python
await runner.handle_candle_boundary(
    ended_candle_start=...,  # 방금 닫힌 캔들 시작
    ended_candle_end=...,    # 방금 닫힌 캔들 종료
)
```
- 순서 중요: **stats flush → 상태 리셋** (runner가 알아서 처리).

### 4.3 Orderbook tick
- `CrossingRunner`는 직접 안 받음. 대신 `IMarketDataPort.get_orderbook()`이 호출될 때 trade-infra 캐시에서 최신 값을 반환하도록 구현.
- 즉 trade-infra는 orderbook 업데이트를 받아서 자기 캐시만 갱신하면 됨.

---

## 5. 와이어링 예시 (pseudo-code)

```python
# trade-infra/strategies/cross_5m/adapters.py
from cross_5m_improve_strategy import (
    IClockPort, IMarketDataPort, IOrderExecutor, ITradeRecorder, INotifier,
    Orderbook, OrderResult,
)

class LiveClock:
    def monotonic(self): return time.monotonic()
    def now_utc(self): return datetime.now(timezone.utc)

class TradeInfraMarketData:
    def __init__(self, trade_infra_cache): self._c = trade_infra_cache
    def get_orderbook(self, coin, tf, side):
        row = self._c.get(...)   # trade-infra 방식대로 조회
        return Orderbook(
            best_bid=row.bid, best_ask=row.ask, ...
            token_id=row.token_id, market_slug=row.slug,
            timestamp=row.ts,
        ) if row else None
    def get_token_id(self, coin, tf, side): ...

class TradeInfraExecutor:
    async def place_gtc(self, coin, token_id, price, contracts):
        # trade-infra 거래 모듈 호출
        resp = await self._venue.send_gtc(...)
        return OrderResult(success=..., filled_contracts=..., ...)

class TradeInfraRecorder:
    async def record_trade(self, ...): await self._db.insert(...)
    async def save_crossing_stats(self, ...): await self._db.upsert(...)
    async def get_recent_crossing_counts(self, coin, limit=2): return await self._db.fetch(...)

class LogNotifier:
    def notify(self, msg): logger.info(msg)
```

```python
# trade-infra/strategies/cross_5m/main.py
from cross_5m_improve_strategy import CrossingRunner, CrossingEvent

runner = CrossingRunner(
    coin="btc",
    timeframe="5m",
    clock=LiveClock(),
    market_data=TradeInfraMarketData(cache),
    executor=TradeInfraExecutor(venue_client),
    recorder=TradeInfraRecorder(db),
    notifier=LogNotifier(),
)
await runner.load_recent_crossing_counts()

# trade-infra 이벤트 루프 안에서:
async for raw_event in trade_infra_event_stream:
    if raw_event.type == "crossing":
        await runner.handle_crossing(CrossingEvent(
            coin=raw_event.symbol, timeframe=raw_event.tf,
            direction=raw_event.dir, ...
        ))
    elif raw_event.type == "candle_close":
        await runner.handle_candle_boundary(raw_event.start, raw_event.end)
```

---

## 6. 체크리스트 (trade-infra 세션에서)

### 필수
- [ ] 패키지 디렉토리 `cross_5m_improve_strategy/` 를 trade-infra repo에 복사 (의존성 추가 없이 바로 import 가능)
- [ ] 5개 포트 구현 (`adapters.py`)
- [ ] trade-infra 이벤트 → `CrossingEvent`/`handle_candle_boundary` 변환 레이어
- [ ] `CrossingRunner` 부트 시 `load_recent_crossing_counts()` 호출
- [ ] `pytest cross_5m_improve_strategy/tests/` 통과 확인 (패키지 자체 검증)

### trade-infra 통합 시 결정 필요
- [ ] **Crossing 정의 (원본 동작 = Option A):**
  - 판정 가격 시리즈: **Binance miniTicker tick** (현물 실시간가)
  - 기준선: 5분봉 시작 시점 snapshot한 `candle_open` (첫 tick의 가격)
  - 판정식:
    - `prev_tick < candle_open <= current_tick` → `direction="up"`
    - `prev_tick > candle_open >= current_tick` → `direction="down"`
  - 생성 책임: trade-infra 내부 tick handler가 직접 emit (원본에선 별도 서비스 `binance_ws`가 담당). `CrossingRunner`에 `CrossingEvent`로 전달.
  - **주의:** Polymarket venue 사용 시 정산은 Chainlink 기준, crossing 판정은 Binance tick 기준 → 소스 불일치는 원본 거동. 다른 venue면 venue 정산 소스와 crossing 판정 소스 일치 여부 재검토.
- [ ] **Candle 정의:** 5분봉 경계 시점 (exchange time vs wall clock) 확인 — 원본은 UTC wall clock (`%M % 5 == 0` at second 0)
- [ ] **Token/market 식별자:** Polymarket token_id 대신 trade-infra venue의 식별자로 변경 필요
- [ ] **High-vol filter:** trade-infra에 crossing stats 이력이 없으면 초기 N캔들은 자동 비활성화됨. 원하지 않으면 `StrategyParams(low_vol_history_size=0)` 로 끄기
- [ ] **GTC 0.80:** Polymarket binary option 기준 가격. trade-infra venue 특성에 맞게 `StrategyParams(gtc_fixed_price=...)` 조정

### 선택
- [ ] Settlement/PENDING 주문 체크: 현 repo의 `_settle_pending_trades`, `_check_pending_orders`는 **패키지에 포함 안 됨**. trade-infra가 자체적으로 정산 파이프라인이 있으면 그걸 쓰고, 없으면 별도 구현 필요.
- [ ] `p1_watchdog` / 헬스체크: 이건 infra 레이어라 패키지 바깥에서 처리.

---

## 7. 숨은 함정 (주의)

### 7.1 시계 단조성
- `strategy.py`의 cooltime 판정은 `clock.monotonic()` 기준. `now_utc()`는 wall clock이라 NTP 보정 시 되감길 수 있음 → monotonic 사용 엄수.

### 7.2 `elapsed_seconds` 계산 주체
- runner는 `event.elapsed_ms // 1000` 을 사용. trade-infra에서 이미 계산해서 주는 게 정확. 여기서 다시 계산하지 말 것 (이벤트 전달 지연 포함됨).

### 7.3 `record_crossing_time` 호출 시점
- **Runner가 high-vol filter 체크 전에** 호출함 (현 로직 동일). 즉 스킵된 crossing도 통계엔 잡힘 → 통계 정확도 ○, 전략 판정엔 영향 ×. 변경 금지.

### 7.4 `start_cooltime`을 주문 성공 후가 아닌 직전에 호출
- **동시 주문 방지**가 목적. 주문 실패해도 cooltime은 유지됨 (실패가 연쇄되어 쏟아지는 것 방지). runner 내부에서 처리.

### 7.5 `_cooltime_timer`는 주문 성공 시에만 예약
- 실패 시에는 pending 체크용 타이머도 안 걸림 → 다음 crossing이 알아서 cooltime 만료 감지해서 처리. 이 경로도 테스트됨.

### 7.6 `candle_key` 형식
- `f"{coin}_{timeframe}_{candle_start.isoformat()}"` → `"btc_5m_2026-04-15T00:00:00+00:00"`
- candle_start를 isoformat으로 직렬화한 값이 key. trade-infra에서도 이 형식 유지 (통계 조회 시 join 키).

### 7.7 ⚠️ 가격 소스 해상도 (CRITICAL)

**원본 전략은 Binance `miniTicker` (1초 고정 간격 스냅샷) 기준으로 파라미터 튜닝됨.** 해상도를 바꾸면 3개 파라미터가 의미를 잃음. 반드시 재튜닝 필요.

#### 해상도별 영향

| 스트림 | Push 주기 | Crossing 감지 민감도 | 파라미터 영향 |
|---|---|---|---|
| `@miniTicker` | 1000ms 고정 | 기준 (1x) | 원본 파라미터 그대로 |
| `@kline_1m close` | 60000ms | 더 둔감 (0.017x) | speed_filter/high_vol 거의 미발동 |
| `@aggTrade` | ~10~100ms (변동) | 50~100x 민감 | **재튜닝 필수** |
| `@trade` | 체결마다 | 100x+ 민감 | **재튜닝 필수** |
| `@bookTicker` | BBO 변경마다 | ~100x 민감 | **재튜닝 필수** |

#### aggTrade/trade/bookTicker 전환 시 재튜닝 대상

**🔴 HIGH: `speed_filter_max_crossings` (기본 3)**
- 1s 샘플링 기준 "20초 내 3회 방향 전환 = 노이즈" 의미
- aggTrade로 바꾸면 1초 안에도 3회 oscillation 가능 → **거의 매 진입이 HEDGE_SPEED로 전환** → 전략이 "첫 진입 후 즉시 헤지"로 퇴화
- 권장: 새 해상도에서 평균 crossing 빈도 측정 후 threshold 재산정 (예: aggTrade 기준 `15` 수준)

**🔴 HIGH: `low_vol_threshold` (기본 3, high-vol filter)**
- 캔들당 3회 초과 crossing이면 high-vol로 간주 → 다음 캔들 skip
- aggTrade 전환 후 평범한 캔들도 50+ crossing 나옴 → **거의 모든 캔들 skip**
- 권장: 새 해상도 기준 재산정 (예: aggTrade 기준 `30~100`). 또는 비율 기반으로 재설계.

**🟡 MEDIUM: `crossing_max_count` (기본 5)**
- 민감도 증가 → Max5가 더 빨리 소진됨 (캔들 초반에 5회 채워질 수 있음)
- x20 베팅 기회는 늘어나지만 이후 4분 가까이 잠김
- 의도된 거동이면 유지. 아니면 `7~10`으로 상향 고려.

#### 영향 없는 항목 (해상도 무관)

- `crossing_bet_contract_unit` (수량)
- `cooltime_seconds` (시간 기반)
- `crossing_entry_cutoff_seconds` / `crossing_hedge_cutoff_seconds` (시간 기반)
- `gtc_fixed_price` (가격 기반)
- 방향 중복 차단 / 시간대 컷오프 로직

#### Cooltime은 해상도 무관하게 안전

걱정 포인트: "해상도 올리면 로직이 너무 자주 돌아 문제가 될까?"
- 전략 로직은 **crossing 이벤트당 1회** 실행 (O(1) 판정), tick마다 돌지 않음
- cooltime 중 같은 방향 crossing은 즉시 `return None` (무시)
- 반대 방향 crossing은 pending에 덮어쓰기만 함 (추가 작업 없음)
- 실제 주문(`place_gtc`)은 cooltime이 선제 차단 → **주문 남발 위험 0**
- 단, **cooltime 중 pending 시맨틱 변화:** 원본(1s)에서는 cooltime 동안 pending이 0~1회 기록되지만, aggTrade에서는 수십 번 덮어써져 "cooltime 만료 시점의 최신 방향"으로 수렴. 로직 정상, 직관적 의미만 변함.

#### 권장 전략 (trade-infra)

- **보수적:** miniTicker 유지 → 파라미터 그대로 사용, 원본 백테스트 재현성 유지
- **공격적:** aggTrade 통일 → `speed_filter_max_crossings`, `low_vol_threshold`, (선택)`crossing_max_count` 재튜닝 + 백테스트 재검증 필수
- **하이브리드 (권장):**
  - Crossing 판정: miniTicker (파라미터 그대로)
  - Candle OHLC 저장: aggTrade 기반 (데이터 품질 향상)
  - → 리스크 최소화 + 데이터 정확도 향상

어느 쪽을 택하든, 선택 사실과 근거를 전략 인스턴스 근처에 주석으로 남길 것.

---

## 8. 파라미터 튜닝

```python
from cross_5m_improve_strategy import StrategyParams, CrossingRunner

params = StrategyParams(
    crossing_bet_contract_unit=20,     # 기본 10 → 2배
    crossing_max_count=7,              # 기본 5 → 7번까지 허용
    gtc_fixed_price=0.75,              # 기본 0.80 → 0.75
    speed_filter_max_crossings=4,      # 기본 3 → 4회까지 허용
    low_vol_history_size=3,            # 기본 2 → 3캔들 윈도우
)

runner = CrossingRunner(..., params=params)
```

모든 파라미터는 `config.py` 의 `StrategyParams` dataclass 필드로 있음. 전략 코어 수정 없이 override 가능.

---

## 9. 테스트 실행

```bash
cd packages
python3 -m pytest cross_5m_improve_strategy/tests/ -v
```

12개 시나리오 전부 통과 확인됨 (2026-04-15 기준):
- first entry / opposite direction / same direction skip
- HEDGE_TIME / HEDGE_MAX5 / HEDGE_SPEED / forbidden zone
- cooltime pending opposite / same direction drop
- reset / failed / custom params

trade-infra 쪽에서도 이 테스트 suite는 그대로 돌려서 패키지 무결성 검증 가능.

---

## 10. 원본 비교 (감사용)

- 원본: `services/real_trader_5m_improve/main.py` (L47~268 — 전략 코어, L489~795 — 이벤트 처리/실행)
- 추출본 대비 **의미 변경 없음**. 리팩터링 내역:
  - `time.time()` → `clock.monotonic()` / `clock.now_utc()`
  - `state dict` → `CandleState` dataclass
  - `generate_slug` 의존 제거 (slug 검증은 venue adapter 책임)
  - DB/Redis/CLOB 호출을 포트로 분리
  - `_save_candle_crossing_stats`, `_init_recent_crossing_counts`, `_is_high_volatility` → `CrossingRunner`로 이동 (전략 코어 외부)

---

## 부록: public API cheat-sheet

```python
from cross_5m_improve_strategy import (
    # 코어 (테스트나 직접 제어용)
    CrossingStrategy5MImprove,

    # 프로덕션용 facade
    CrossingRunner,

    # 파라미터
    StrategyParams, DEFAULT_PARAMS,

    # 데이터 모델
    TradeSignal, CrossingEvent, Orderbook, OrderResult, CandleState,

    # 포트 (trade-infra가 구현)
    IClockPort, IMarketDataPort, IOrderExecutor, ITradeRecorder, INotifier,
)
```
