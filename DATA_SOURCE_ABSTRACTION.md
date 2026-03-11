# Data Source Abstraction: Shared vs Direct

> **현재 상태**: Shared 모드만 구현됨 (P1 → Redis pub/sub → P4).
> 독립 연결(Direct) 모드가 필요하다면 이 문서의 설계를 참고하여 구현할 것.

---

## 배경

Paper/Real/Backtest 등 여러 전략 소비자가 동일한 외부 데이터(Polymarket 오더북, 가격 등)를 필요로 한다.
데이터를 어떻게 전달할지 두 가지 방식이 있고, 각각 트레이드오프가 다르다.

### 논의 맥락

- **Shared (현재 채택)**: 수집기(P1) 1개가 외부 WS에 연결 → Redis pub/sub으로 fan-out → N개 consumer가 동일 메시지 수신
- **Direct (미구현)**: 각 consumer가 독립적으로 외부 WS에 직접 연결 → 수집+파싱을 자체 수행

초기에는 장애 격리를 위해 독립 연결이 나을 수 있다고 판단했으나,
실제 운영 관점에서 **Shared가 기본이 되어야 하는 이유**가 더 크다:

1. **데이터 동일성**: Paper와 Real이 물리적으로 같은 메시지를 수신 → paper 결과가 real을 정확히 예측
2. **Rate limit**: 전략 N개 돌려도 외부 WS 연결은 1세트 → Polymarket/Binance rate limit 걱정 없음
3. **자원 효율**: 오더북 JSON 파싱 1회, Redis publish 1회로 N개 consumer 서비스

P1이 SPOF가 되는 문제는 격리가 아니라 안정성으로 해결한다:
- Docker `restart: always`
- Health check (`_health_file_loop`)
- Consumer 측 watchdog (P4의 `p1_watchdog`: 30초 무응답 감지)
- WS hot-swap (캔들 경계 끊김 없이 교체)

---

## 아키텍처 비교

### Shared (현재)

```
External WS ──→ P1(rtds) ──→ Redis pub/sub ──→ paper_trader
                    │              (1세트)  ──→ real_trader
                    │                       ──→ backtest
                    └──→ DB (히스토리)
```

- P1 코드 변경 없이 subscriber 추가만으로 확장
- 데이터 동일성 보장
- WS 연결 1세트

### Direct (미구현, 필요 시)

```
paper_trader ──→ Polymarket WS 직접 연결 (자체 파싱)
real_trader  ──→ Polymarket WS 직접 연결 (자체 파싱)
```

- P1 없이 단독 실행 가능
- 로컬 개발/디버깅에 편리
- 전략 1개만 빠르게 테스트할 때 유용

---

## Direct 모드 구현 가이드

### 추상화 인터페이스

```python
# packages/data_source/base.py

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Dict

class DataSource(ABC):
    @abstractmethod
    async def start(self) -> None:
        """연결 초기화"""

    @abstractmethod
    async def subscribe_orderbook(self) -> AsyncIterator[Dict]:
        """오더북 메시지 스트림. 메시지 형식은 아래 참조."""

    @abstractmethod
    async def subscribe_candle_boundary(self) -> AsyncIterator[Dict]:
        """캔들 경계 이벤트 스트림"""

    @abstractmethod
    async def close(self) -> None:
        """연결 정리"""
```

### 메시지 형식 (두 모드 모두 동일해야 함)

```json
// orderbook message
{
  "coin": "btc",
  "timeframe": "15m",
  "side": "up",
  "best_bid": 0.52,
  "best_ask": 0.54,
  "mid_price": 0.53,
  "slug": "btc-updown-15m-1763618400",
  "token_id": "12345...",
  "timestamp": 1763618400.123,
  "candle_start_ts": 1763618400,
  "candle_end_ts": 1763619300
}
```

```json
// candle_boundary message
{
  "event": "candle_boundary",
  "timeframe": "15m",
  "timestamp": 1763619300
}
```

### 구현할 파일

```
packages/data_source/
├── __init__.py
├── base.py           # DataSource ABC
├── shared.py         # SharedDataSource — Redis pub/sub (현재 로직 래핑)
└── direct.py         # DirectDataSource — Polymarket WS 직접 연결
```

### SharedDataSource (현재 로직 래핑)

```python
# packages/data_source/shared.py

class SharedDataSource(DataSource):
    """Redis pub/sub 기반. P1(rtds)이 publish한 메시지를 수신."""

    def __init__(self, redis_host, redis_port):
        self.redis_host = redis_host
        self.redis_port = redis_port

    async def subscribe_orderbook(self):
        pubsub = self.redis_client.pubsub()
        await pubsub.psubscribe("ch:orderbook:*")
        async for message in pubsub.listen():
            if message["type"] in ("message", "pmessage"):
                yield json.loads(message["data"])
```

### DirectDataSource (새로 구현)

```python
# packages/data_source/direct.py

class DirectDataSource(DataSource):
    """Polymarket WS 직접 연결. P1 없이 단독 동작."""

    # rtds/main.py의 아래 로직을 재사용:
    # - _fetch_markets() → Gamma API에서 token_id 로드
    # - Polymarket CLOB WS 연결 + 구독
    # - _handle_book() / _handle_price_change() → 오더북 파싱
    # - _get_candle_window() → 캔들 경계 감지
    #
    # 핵심: 출력 메시지 형식을 SharedDataSource와 동일하게 맞출 것
```

### Trader 수정 (paper_trader/main.py)

```python
# 변경 전 (현재)
async def redis_subscriber(self):
    pubsub = self.redis_client.pubsub()
    await pubsub.psubscribe("ch:orderbook:*", "ch:candle_boundary")
    async for message in pubsub.listen():
        data = json.loads(message["data"])
        await self._handle_orderbook_message(data)

# 변경 후
async def data_subscriber(self):
    async for data in self.source.subscribe_orderbook():
        await self._handle_orderbook_message(data)
    # _handle_orderbook_message(), _check_entry(), 전략 로직은 그대로
```

### Config

```python
# services/paper_trader/config.py
DATA_SOURCE = os.getenv("DATA_SOURCE", "shared")  # "shared" | "direct"
```

### 변경 범위 요약

| 파일 | 변경 |
|------|------|
| `packages/data_source/*` | 새로 생성 (ABC + 2개 구현체) |
| `services/paper_trader/main.py` | `redis_subscriber()` → `data_subscriber()` 교체 |
| `services/paper_trader/config.py` | `DATA_SOURCE` env 추가 |
| `services/rtds/*` | 변경 없음 |
| `strategies/*` | 변경 없음 |
| `dashboard/*` | 변경 없음 |

### 주의사항

- `DirectDataSource`는 `rtds/main.py`의 WS 연결/파싱 코드를 재사용하므로, 해당 로직을 `packages/`로 추출하는 것이 선행되어야 함
- Direct 모드에서는 DB 히스토리 저장이 없으므로 settlement에 필요한 가격 데이터를 별도로 확보해야 함
- 두 모드의 출력 메시지 형식이 반드시 동일해야 전략 코드가 모드에 무관하게 동작함
