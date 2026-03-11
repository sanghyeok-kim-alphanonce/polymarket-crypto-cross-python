# Polymarket Crypto Crossing Strategy

Polymarket 크립토 바이너리 옵션 (UP/DOWN) 자동 트레이딩 시스템.

## 아키텍처

```
┌─────────────┐     ┌─────────────┐
│    rtds     │     │  binance_ws │
│ Polymarket  │     │   Binance   │
│  orderbook  │     │  aggTrade   │
└──────┬──────┘     └──────┬──────┘
       │ ch:orderbook:*    │ ch:crossing:*
       └─────────┬─────────┘
                 ▼
         ┌───────────────┐
         │    Redis      │
         │   pub/sub     │
         └───────┬───────┘
                 │
       ┌─────────┴─────────┐
       ▼                   ▼
┌─────────────┐     ┌─────────────┐
│paper_trader │     │real_trader  │
│  (paper)    │     │  (live)     │
└─────────────┘     └─────────────┘
```

## 서비스

| 서비스 | 컨테이너 | 설명 |
|--------|----------|------|
| rtds | cointest-rtds | Polymarket 오더북 + Chainlink 가격 수집 |
| binance_ws | cointest-binance-ws | Binance aggTrade로 crossing 감지 |
| paper_trader | cointest-paper-trader | Paper trading 전략 실행 |
| real_trader_cross_limit_hedge | cointest-real-trader-cross-limit-hedge | Live trading (BTC 15분봉) |
| dashboard | localhost:3839 | Next.js 모니터링 UI |
| dozzle | localhost:9999 | Docker 로그 뷰어 |

## 실행

```bash
# 전체 서비스 실행
docker compose up -d --build

# Dashboard 실행 (별도 터미널)
cd dashboard && bun install && bun run dev -- -p 3839
```

## 환경변수

### .env (credentials)

```bash
# Polymarket
POLYMARKET_PRIVATE_KEY=0x...
POLYMARKET_PROXY_ADDRESS=0x...

# Telegram 알림
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### config.py 관리 원칙

| 위치 | 용도 |
|------|------|
| `.env` | credentials만 (PRIVATE_KEY, BOT_TOKEN) |
| `config.py` | 전략 파라미터 (고정값) |
| `docker-compose.yml` | 인프라 설정만 (DB_HOST, REDIS_HOST) |

**전략 파라미터는 os.getenv 사용하지 않고 config.py에서 고정값으로 관리**

## 페이지

### Dashboard (localhost:3839)

| 페이지 | 경로 | 설명 |
|--------|------|------|
| Home | `/` | 실시간 캔들 방향 + 오더북 요약 |
| Prices | `/prices` | Binance/Chainlink 실시간 가격 |
| Orderbooks | `/orderbooks` | Polymarket UP/DOWN 오더북 |
| Paper Trading | `/paper-trading` | Paper trade 거래 내역 + PnL |
| Real Trading | `/real-trading` | Live trade 거래 내역 + PnL |
| Crossing Analysis | `/crossing-analysis` | Crossing 이벤트 분석 |
| Backtest | `/backtest` | 백테스트 실행 |

### Dozzle (localhost:9999)

Docker 컨테이너 로그 실시간 뷰어.

- 접속: `http://localhost:9999/logs`
- 모든 컨테이너 로그를 웹에서 실시간 확인
- 필터링, 검색 지원

## 모니터링

- **Dashboard** (localhost:3839): 거래 현황, PnL, 오더북 실시간 확인
- **Dozzle** (localhost:9999/logs): 모든 컨테이너 로그 웹에서 실시간 확인

## 포트

| 서비스 | 포트 |
|--------|------|
| PostgreSQL | 5435 |
| Redis | 6380 |
| Dashboard | 3839 |
| Dozzle | 9999 |
