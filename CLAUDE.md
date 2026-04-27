# poly-coin-test 작업 지침

## 목적
Polymarket Binary Options Paper Trading 시스템. 멀티 프로세스 아키텍처로 GIL 경합 제거 및 장애 격리.

## 구조

```
poly-test/
├── docker-compose.yml      # DB + Redis + 모든 서비스
├── init.sql                # 테이블 스키마
├── pyproject.toml          # Python 의존성
├── ARCHITECT.md            # 상세 아키텍처 문서
├── services/
│   ├── rtds/               # P1: Polymarket CLOB WS + RTDS WS + Chainlink REST
│   ├── binance_ws/         # P2: Binance miniTicker + kline (5m/15m candle)
│   ├── paper_trader/       # P4: delta momentum 전략 (v12_5)
│   ├── paper_trader_cross_v2/          # Crossing V2 paper trading
│   ├── paper_trader_cross_limit_15m/   # 15분봉 횟수 제한 crossing
│   ├── paper_trader_cross_limit_5m/    # 5분봉 횟수 제한 crossing
│   ├── real_trader_cross_limit_hedge/  # 15분봉 실거래 crossing
│   ├── real_trader_5m_cross_front/     # 5분봉 초반 실거래 crossing
│   └── multi_exchange_stream/          # 다중 거래소 수집 (Bybit/Gate/Bitget)
├── packages/               # 공유 Python 패키지 (polymarket_common, orderbook_shared, strategy_config)
├── scripts/                # 유틸리티 스크립트
├── backtest/               # 백테스트 모듈
└── dashboard/              # Next.js 대시보드 (port 3839)
```

## 서비스별 역할

### P1: rtds (cointest-rtds)
- Polymarket CLOB WebSocket: 오더북 실시간 수집
- Polymarket RTDS WebSocket: Binance/Chainlink 실시간 가격
- Chainlink REST API: 가격 수집 fallback (10초마다)
- Redis pub/sub publish: `ch:orderbook:*`, `ch:candle_boundary`

### P2: binance_ws (cointest-binance-ws)
- Binance miniTicker: 실시간 가격 → candle tracking
- Binance kline_1m: 1분봉 OHLCV → 5m/15m candle open 가격 계산
- Redis pub/sub publish: `ch:candle:*`, `ch:crossing:*`

### Paper Traders
**paper_trader (cointest-paper-trader-v12-5)**
- Delta momentum 전략 (v12_5)
- Redis subscribe: `ch:orderbook:*`, `ch:candle_boundary`

**paper_trader_cross_v2**
- 15분봉 Crossing V2 전략 (밸런싱 베팅)

**paper_trader_cross_limit_15m / 5m**
- 횟수 제한 crossing 전략 (캔들당 최대 10회)
- 1회차: UNIT, 2~9회차: 2*UNIT, 10회차: UNIT

### Real Traders
**real_trader_cross_limit_hedge**
- 15분봉 실거래 crossing (10회 제한)
- Polymarket 실거래 연동, 텔레그램 알림

**real_trader_5m_cross_front**
- 5분봉 초반 crossing 실거래
- Polymarket 실거래 연동

### Multi Exchange Stream (cointest-multi-exchange)
- ccxt.pro WebSocket: Bybit, Gate, Bitget 실시간 가격/오더북
- exchange_prices 테이블에 저장
- Redis 캐싱: `exchange_price:{exchange}:{coin}`, `exchange_orderbook:{exchange}:{coin}`

## 프로세스간 통신 (Redis pub/sub)

| 채널 | 방향 | 빈도 |
|------|------|------|
| `ch:orderbook:{coin}_{tf}_{side}` | P1 → traders | ~1000/sec |
| `ch:candle_boundary:{tf}` | P1 → traders | 5분/15분마다 |
| `ch:candle:{coin}_{tf}` | P2 → dashboard | ~8/sec |
| `ch:crossing:{coin}_{tf}` | P2 → traders | crossing 발생시 |

## 전략

### V12-5 Delta Momentum (paper_trader)
```
가격 delta 기반 모멘텀 전략
- 가격 상승 → UP 토큰 매수
- 가격 하락 → DOWN 토큰 매수
- Entry Price >= 0.35 필터 (싼 가격 = 역행 베팅 = 손실)
- 전반(<=10분): 높은 delta threshold
- 후반(>10분): Entry Price >= 0.55 + 낮은 threshold
- Cooldown으로 과다 거래 방지 (5초)
```

### Crossing 횟수 제한 전략 (paper_trader_cross_limit_*, real_trader_*)
```
15분봉/5분봉 시작가 crossing 기반 전략
- 가격이 캔들 시작가를 crossing하면 진입
- 캔들당 최대 10회 진입 제한
- 베팅 수량: 1회차=UNIT, 2~9회차=2*UNIT, 10회차=UNIT
- 14분 30초 이후 (5분봉: 4분 30초) → 바로 10회차
- GTC 고정가 0.70
```

## Polymarket CLOB V2 (2026-04-28 cutover)

### 코드 측 변경 (이미 적용됨)
- 의존성: `py-clob-client` → `py-clob-client-v2>=1.0.0`
- 모듈: `py_clob_client.*` → `py_clob_client_v2.*`
- API 키 메서드명: `create_or_derive_api_creds()` → `create_or_derive_api_key()`
- OrderArgs에서 `fee_rate_bps` 제거 (V2는 protocol에서 fee 자동 계산)
- 초기화 직후 `update_balance_allowance(asset_type=COLLATERAL)` 호출 추가
  (없으면 첫 주문이 stale balance/allowance cache로 reject됨)
- 주문 타입: `OrderType.GTC` → `OrderType.FAK` (V2에서 book을 cross하는 GTC는
  strict reject. 본 전략들은 GTC 0.70~0.90 고정으로 항상 시세 위 → 의도가 taker)
- Builder code 옵션 추가 (`POLYMARKET_BUILDER_CODE` env, 미설정 시 attribution 없이 거래)
- WebSocket / Gamma API URL은 V2에서도 동일 (변경 없음)

### Cutover 운영 절차 (한 번만, 4/27 EOD까지)
운영 Safe에 V2 approve 배치 + USDC.e → pUSD wrap을 relayer로 제출해야 함.
- USDC.e → V2 Exchange / V2 NegRisk / NegRiskAdapter / Onramp approve
- pUSD → V2 Exchange / V2 NegRisk / NegRiskAdapter approve
- CTF setApprovalForAll → V2 Exchange / V2 NegRisk / NegRiskAdapter
- USDC.e → pUSD wrap (Onramp.wrap)

자세한 절차는 `/Users/alphanonce/Downloads/poly-v2-api/migration-knowhow.md` 참조.

### Cutover 이후 (4/28 ~11:00 UTC)
- 모든 open order는 wipe됨 → 자동 재발사 (정상 동작)
- `clob.polymarket.com`은 V2 backend로 자동 교체 → URL 변경 불필요
- 4/28 이전 V2 미리 검증: `POLYMARKET_HOST=https://clob-v2.polymarket.com`

### Claim 서비스 (services/claimer)
- Builder Relayer 인증은 V1/V2 동일 (변경 없음)
- `redeemPositions`의 `collateralToken`은 마켓 생성 시점의 collateral과 일치해야 함:
  - cutover 이전 마켓: USDC.e (`0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`)
  - cutover 이후 마켓: pUSD (`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`)
- 단일 collateral 가정. 환경변수 `CLAIM_COLLATERAL_ADDRESS`로 오버라이드 가능.

## 실행 방법

### ⚠️ 중요: 환경변수 충돌 방지

`claudeCodeTelegram` 환경에서 실행 시, 셸에 `TELEGRAM_BOT_TOKEN` 등이 export 되어 있어 `.env` 파일 값이 무시될 수 있다.
**반드시 `.env` 파일을 명시적으로 export 후 실행**:

```bash
# poly-test 서비스 실행 전 필수
cd /home/yeonwoo/poly-test
export $(grep -v '^#' .env | xargs)

# 그 후 docker compose 실행
docker compose up -d --build
```

또는 한 줄로:
```bash
cd /home/yeonwoo/poly-test && export $(grep -v '^#' .env | xargs) && docker compose up -d --build
```

### 일반 실행

```bash
# 1. 전체 서비스 실행
cd poly-test && export $(grep -v '^#' .env | xargs) && docker compose up -d --build

# 2. Dashboard (별도 터미널)
cd poly-test/dashboard && bun run dev -- -p 3839

# 3. 개별 서비스 로그
docker logs -f cointest-rtds
docker logs -f cointest-binance-ws
docker logs -f cointest-paper-trader-v12-5
docker logs -f cointest-multi-exchange
```

## 포트 할당
| 서비스 | 컨테이너명 | 포트 |
|--------|-----------|------|
| PostgreSQL | cointest-paper-trade-db | 5435 |
| Redis | cointest-redis | 6380 |
| Dashboard | (별도 실행) | 3839 |

## DB 조회

```bash
# Paper Trade DB 접속
docker exec -it cointest-paper-trade-db psql -U paper -d paper_trade

# 최근 거래 조회
SELECT coin, side, order_price, pnl, outcome, market_result
FROM test_paper_trades
WHERE status = 'CLOSED'
ORDER BY time DESC
LIMIT 20;
```

## DB 테이블

| 테이블 | 소유 프로세스 | 용도 |
|--------|-------------|------|
| coin_prices | P1 (write), P2 (write) | Binance + Chainlink 가격 |
| binance_ohlcv_1m | P2 | 1분봉 OHLCV |
| binance_ticks | P2 | 1초 단위 가격 |
| orderbook_books | P1 | Polymarket 오더북 스냅샷 |
| orderbook_changes | P1 | Polymarket 오더북 변경 |
| test_paper_trades | P4 | Paper trading 거래 기록 |
| exchange_prices | multi_exchange_stream | 다중 거래소 가격 |

## 환경변수 관리 원칙

설정이 여러 곳에서 중복되면 혼란스럽다. 아래 원칙을 따른다:

### 설정 위치별 역할

| 위치 | 용도 | 예시 |
|------|------|------|
| `.env` | 민감한 credentials만 | `POLYMARKET_PRIVATE_KEY`, `TELEGRAM_BOT_TOKEN` |
| `config.py` (각 서비스) | 전략 파라미터 (고정값) | `CROSSING_FIRST_BET = 10.0` |
| `config.py` (각 서비스) | 인프라 설정 (os.getenv) | `DB_HOST = os.getenv("DB_HOST", "...")` |
| `packages/strategy_config/` | 공유 전략 파라미터 | `V12_5_*`, `V12_6_*`, `BET_AMOUNT` |
| `docker-compose.yml` | 인프라 설정만 | `DB_HOST`, `REDIS_HOST` |

### 규칙

1. **전략 파라미터는 고정값으로 config.py에서 관리**
   ```python
   # GOOD - 고정값
   CROSSING_BET_CONTRACT_UNIT = 10
   CROSSING_CUTOFF_SECONDS = 900
   GTC_FIXED_PRICE = 0.70

   # BAD - os.getenv 불필요
   CROSSING_BET_CONTRACT_UNIT = int(os.getenv("CROSSING_BET_CONTRACT_UNIT", "10"))
   ```

2. **os.getenv는 인프라/credentials만 사용**
   ```python
   # 인프라 (docker 네트워크에서 오버라이드 필요)
   DB_HOST = os.getenv("DB_HOST", "paper_trade_db")
   REDIS_HOST = os.getenv("REDIS_HOST", "localhost")

   # Credentials (.env에서 주입)
   POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
   TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
   ```

3. **docker-compose.yml에 전략 파라미터 넣지 않기**
   ```yaml
   environment:
     # GOOD - 인프라만
     DB_HOST: paper_trade_db
     REDIS_HOST: redis
     POLYMARKET_PRIVATE_KEY: ${POLYMARKET_PRIVATE_KEY:-}

     # BAD - 전략 파라미터는 config.py에서 관리
     # CROSSING_BET_SIZE: 10
     # GTC_FIXED_PRICE: 0.70
   ```

4. **예외: 같은 서비스를 여러 인스턴스로 돌릴 때**
   - `STRATEGY_NAME` 같이 인스턴스마다 달라야 하는 값은 docker-compose.yml에서 관리
   ```yaml
   # paper_trader_v12_5와 paper_trader_v12_6를 따로 돌림
   paper_trader_v12_5:
     environment:
       STRATEGY_NAME: v12_5
   paper_trader_v12_6:
     environment:
       STRATEGY_NAME: v12_6
   ```

5. **공유 파라미터는 packages/strategy_config/**
   - `paper_trader`, `backtest_api`가 공유하는 V12_5/V12_6 파라미터
   - 한 곳에서 수정하면 모든 서비스에 적용

## Dashboard 코딩 규칙

### null 안전성 (필수)
API/DB에서 오는 값은 항상 null일 수 있다. `.toLocaleString()`, `.toFixed()`, `.toUpperCase()` 등 메서드 호출 전에 반드시 null guard 적용:

```tsx
// BAD - null이면 TypeError 발생
{candle.close.toLocaleString()}
{price.toFixed(2)}

// GOOD - null guard
{(candle.close ?? 0).toLocaleString()}
{(price ?? 0).toFixed(2)}

// GOOD - 조건부 렌더링
{price != null ? `$${price.toLocaleString()}` : 'N/A'}
```

- API 응답의 숫자 필드: `(value ?? 0)` 또는 삼항 연산자 사용
- 문자열 필드: `value ?? '--'` 또는 optional chaining `value?.toUpperCase()`
- 배열 필드: `(arr ?? []).map(...)` 또는 `arr?.map(...) ?? []`

### Redis 키 변경 시 (필수)
서비스(rtds, binance_ws, paper_trader, multi_exchange_stream)에서 Redis 키 포맷이나 데이터 구조를 변경하면 **dashboard도 반드시 함께 수정**해야 한다:
- `dashboard/lib/redis.ts` - Redis 키 읽기 함수 및 타입 정의
- `dashboard/app/api/` - 해당 키를 사용하는 API 라우트
- `dashboard/components/` - 데이터를 표시하는 컴포넌트

### 새 페이지 추가 시
- `dashboard/app/{route}/page.tsx` 에 파일 생성
- 사이드바 네비게이션과 route가 일치해야 404 방지
