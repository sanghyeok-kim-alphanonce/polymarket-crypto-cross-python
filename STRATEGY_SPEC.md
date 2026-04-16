# 5M Crossing Strategy Spec (real_trader_5m_improve)

## 개요

Binance BTC 가격이 5분봉 시작가(open)를 crossing하면 Polymarket에서 해당 방향의 토큰을 매수하는 전략.

## 데이터 흐름

```
Binance aggTrade WS (실시간 체결)
    ↓
binance_ws: 가격 vs 5분봉 open 비교 → crossing 감지
    ↓
Redis pub/sub: ch:crossing:btc_5m
    ↓
real_trader_5m_improve: 주문 결정 → Polymarket CLOB 주문
    ↓
settlement_loop (5분마다): Chainlink 가격으로 UP/DOWN 판정 → PnL 계산
```

## Crossing 감지 로직

- **소스**: Binance aggTrade (실시간, ms 단위) — miniTicker 아님
- **기준**: 현재 Binance 가격 vs 5분봉 candle open
- **판정**: price zone 전환 (below→above = UP, above→below = DOWN)
- **Polymarket 토큰 가격은 crossing 판단에 사용하지 않음** (Binance 가격만 봄)

## 주문 실행

- **방식**: GTC limit order, 고정가 $0.80
- **실질**: $0.80 이하 최적가 체결 → 사실상 시장가
- **실제 fill**: 대부분 $0.47~$0.67 범위
- **오더북 기반 가격 최적화 없음**

## 포지션 사이징

| 구분 | 계약 수 | 조건 |
|------|---------|------|
| Entry #1 | 10 (UNIT) | 첫 crossing |
| Entry #2~4 | 20 (2×UNIT) | 후속 crossing |
| Hedge | 10 (UNIT) | 아래 조건 중 하나 |

## Hedge 트리거 (하나만 충족 시)

| 이름 | 조건 |
|------|------|
| HEDGE_TIME | elapsed >= 250초 (캔들 4분 10초) |
| HEDGE_MAX5 | 누적 entry >= 4회 |
| HEDGE_SPEED | 20초 내 3회+ crossing |

## 필터

- **고변동성 필터**: 이전 2캔들 crossing > 3이면 skip
- **Cooltime**: 주문 간 1초 간격 (pending crossing은 큐잉 후 실행)

## 정산 (PnL)

- **판정**: Chainlink 가격 기준 (캔들 시작 vs 종료)
- **WIN**: pnl = filled_contracts × $1 - filled_cost
- **LOSS**: pnl = -filled_cost
- **FLAT**: pnl = 0

## 핵심 파라미터

```python
GTC_FIXED_PRICE = 0.80
CROSSING_BET_CONTRACT_UNIT = 10
CROSSING_MAX_COUNT = 5
CROSSING_ENTRY_CUTOFF_SECONDS = 250
CROSSING_HEDGE_CUTOFF_SECONDS = 290
SPEED_FILTER_WINDOW_SECONDS = 20
SPEED_FILTER_MAX_CROSSINGS = 3
COOLTIME_SECONDS = 1.0
```

## Fee 구조 (Polymarket)

- **Taker fee**: 360 bps
- **공식**: fee = fee_rate × min(price, 1 - price)
- **$0.50에서 fee 최대**: $0.018/contract
- **$0.70에서**: $0.0108/contract (40% 절감)

## 가격대별 수익 구조

### 50¢ (현재 주요 체결 구간)

- pair cost (UP+DOWN) = $1.00 = payout → **완전헷지, break-even**
- 수익은 순수 crossing 방향 엣지에서만 발생
- fee가 가장 비싼 구간 (360bps × 0.50)
- **flat sizing 가능** (매 crossing 동일 수량)

### 50¢ 이외 (예: 70¢)

- 같은 시점: UP 70¢ + DOWN 30¢ = $1.00 (정상)
- **다른 시점**: UP 70¢ 매수 → 반전 → DOWN 70¢ 매수 = 쌍당 $1.40 > $1.00 payout
- **crossing마다 확정 손실 누적** → 복구하려면 p/(1-p) 배수 필요 (마틴게일)
- 70¢: 매 crossing **2.33x** 증가 필요 (break-even)
- fee는 낮지만 자본 요구량 기하급수적 증가

### 가격별 crossing 성장 배수 (break-even 기준)

| 매수가 | 배수 p/(1-p) | 매 crossing 추가% |
|--------|-------------|-----------------|
| 50¢ | 1.0x | +0% |
| 55¢ | 1.22x | +22% |
| 60¢ | 1.5x | +50% |
| 65¢ | 1.86x | +86% |
| 70¢ | 2.33x | +133% |
| 80¢ | 4.0x | +300% |

## 결론

- **50¢가 crossing 전략의 최적 가격대** (대칭 payoff, flat sizing 가능)
- 50¢에서 벗어날수록 마틴게일 구조 → 자본 리스크 급증
- 현재 전략은 Polymarket 토큰 가격 필터 없이 진입 → 비효율 구간 노출
