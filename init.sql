-- ============================================================================
-- Coin Prices: Chainlink + RTDS 가격 데이터
-- ============================================================================
CREATE TABLE IF NOT EXISTS coin_prices (
    time TIMESTAMPTZ NOT NULL,
    coin TEXT NOT NULL,
    chainlink_price NUMERIC,
    rest_chainlink_price NUMERIC,
    binance_price NUMERIC,
    chainlink_source TEXT,
    PRIMARY KEY (coin, time)
);

CREATE INDEX IF NOT EXISTS idx_coin_prices_time ON coin_prices(time DESC);
CREATE INDEX IF NOT EXISTS idx_coin_prices_coin ON coin_prices(coin);

-- ============================================================================
-- Binance OHLCV 1분봉
-- ============================================================================
CREATE TABLE IF NOT EXISTS binance_ohlcv_1m (
    time TIMESTAMPTZ NOT NULL,
    coin TEXT NOT NULL,
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume NUMERIC NOT NULL,
    close_time TIMESTAMPTZ,
    num_trades INTEGER,
    PRIMARY KEY (coin, time)
);

CREATE INDEX IF NOT EXISTS idx_binance_ohlcv_time ON binance_ohlcv_1m(time DESC);
CREATE INDEX IF NOT EXISTS idx_binance_ohlcv_coin ON binance_ohlcv_1m(coin);

-- ============================================================================
-- Exchange Prices: 다중 거래소 가격 (Binance, Bybit, Gate, Bitget)
-- ============================================================================
CREATE TABLE IF NOT EXISTS exchange_prices (
    time TIMESTAMPTZ NOT NULL,
    exchange TEXT NOT NULL,
    coin TEXT NOT NULL,
    price NUMERIC NOT NULL,
    bid NUMERIC,
    ask NUMERIC,
    PRIMARY KEY (exchange, coin, time)
);

CREATE INDEX IF NOT EXISTS idx_exchange_prices_time ON exchange_prices(time DESC);
CREATE INDEX IF NOT EXISTS idx_exchange_prices_exchange ON exchange_prices(exchange);
CREATE INDEX IF NOT EXISTS idx_exchange_prices_coin ON exchange_prices(coin);

-- ============================================================================
-- Binance Ticks: 1초 단위 가격 (V12-5 백테스트용)
-- ============================================================================
CREATE TABLE IF NOT EXISTS binance_ticks (
    time TIMESTAMPTZ NOT NULL,
    coin TEXT NOT NULL,
    price NUMERIC NOT NULL,
    bid NUMERIC,
    ask NUMERIC,
    volume_24h NUMERIC,
    PRIMARY KEY (coin, time)
);

CREATE INDEX IF NOT EXISTS idx_binance_ticks_time ON binance_ticks(time DESC);
CREATE INDEX IF NOT EXISTS idx_binance_ticks_coin ON binance_ticks(coin);
-- 백테스트 쿼리 최적화: 특정 시간 범위 + 코인
CREATE INDEX IF NOT EXISTS idx_binance_ticks_coin_time ON binance_ticks(coin, time DESC);

-- ============================================================================
-- Orderbook Books: Polymarket 오더북 전체 스냅샷 (book 이벤트 + 캔들 경계)
-- ============================================================================
CREATE TABLE IF NOT EXISTS orderbook_books (
    time        TIMESTAMPTZ NOT NULL,
    coin        TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    side        TEXT NOT NULL,          -- 'up' or 'down'
    bids        JSONB NOT NULL,         -- [[price, size], ...] full book
    asks        JSONB NOT NULL,         -- [[price, size], ...] full book
    market_slug TEXT NOT NULL,
    token_id    TEXT NOT NULL,
    PRIMARY KEY (coin, timeframe, side, time)
);

CREATE INDEX IF NOT EXISTS idx_ob_books_market ON orderbook_books(market_slug);
CREATE INDEX IF NOT EXISTS idx_ob_books_token ON orderbook_books(token_id, time DESC);

-- ============================================================================
-- Orderbook Changes: 개별 호가 변경 (price_change 이벤트)
-- ============================================================================
CREATE TABLE IF NOT EXISTS orderbook_changes (
    time        TIMESTAMPTZ NOT NULL,
    token_id    TEXT NOT NULL,
    price       NUMERIC NOT NULL,
    size        INTEGER NOT NULL,       -- 0 = level removed
    book_side   TEXT NOT NULL,          -- 'BUY' or 'SELL'
    PRIMARY KEY (token_id, time, price, book_side)
);

CREATE INDEX IF NOT EXISTS idx_ob_changes_time ON orderbook_changes(time DESC);

-- ============================================================================
-- Test Paper Trades: Backtest vs Paper Trading 비교용
-- ============================================================================
CREATE TABLE IF NOT EXISTS test_paper_trades (
    id SERIAL PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    strategy_name TEXT NOT NULL,
    coin TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    -- 진입 시점 정보 (UP/DOWN 각각의 mid_price)
    mid_price NUMERIC NOT NULL,  -- deprecated: 호환성 유지, up_mid_price 사용 권장
    up_mid_price NUMERIC,
    down_mid_price NUMERIC,

    -- 주문 정보
    side TEXT NOT NULL,
    order_price NUMERIC NOT NULL,
    reason TEXT,  -- 진입 사유 (밴드 정보 등)

    -- 오더북 스냅샷 (주문 시점)
    orderbook_snapshot JSONB,

    -- 마켓 정보
    market_slug TEXT NOT NULL,
    token_id TEXT,
    order_id TEXT,  -- CLOB order ID (for tracking PENDING orders)
    candle_start_time TIMESTAMPTZ,
    candle_end_time TIMESTAMPTZ,

    -- Paper 실제 결과
    fill_time TIMESTAMPTZ,
    fill_price NUMERIC,
    fill_orderbook JSONB,
    contracts INTEGER,           -- 주문 수량
    cost NUMERIC,                -- 주문 비용
    filled_contracts INTEGER,    -- 실제 체결 수량
    filled_cost NUMERIC,         -- 실제 체결 비용

    -- 정산
    exit_price NUMERIC,
    pnl NUMERIC,
    outcome TEXT,
    market_result TEXT,

    -- 상태: PENDING → FILLED → CLOSED / CANCELLED
    status TEXT NOT NULL DEFAULT 'PENDING',
    closed_at TIMESTAMPTZ,

    -- Claim 정보 (real trading용)
    claim_tx TEXT,           -- claim transaction hash
    claim_time TIMESTAMPTZ   -- claim 완료 시각
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_test_time ON test_paper_trades(time DESC);
CREATE INDEX IF NOT EXISTS idx_test_coin ON test_paper_trades(coin);
CREATE INDEX IF NOT EXISTS idx_test_status ON test_paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_test_candle ON test_paper_trades(candle_start_time);

-- ============================================================================
-- Crossing Events: Binance 가격이 15분봉 open을 교차한 이벤트
-- ============================================================================
CREATE TABLE IF NOT EXISTS crossing_events (
    id SERIAL PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    coin TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,          -- 'up' or 'down'
    candle_start TIMESTAMPTZ NOT NULL,
    candle_open NUMERIC NOT NULL,     -- 캔들 시작가
    prev_price NUMERIC,               -- 교차 직전 가격
    current_price NUMERIC NOT NULL,   -- 교차 시점 가격
    candle_elapsed_sec INTEGER,       -- 캔들 시작 후 경과 시간 (초)
    source TEXT DEFAULT 'mini'        -- 'mini' (miniTicker) or 'agg' (aggTrade)
);

CREATE INDEX IF NOT EXISTS idx_crossing_time ON crossing_events(time DESC);
CREATE INDEX IF NOT EXISTS idx_crossing_coin ON crossing_events(coin);
CREATE INDEX IF NOT EXISTS idx_crossing_candle ON crossing_events(candle_start);
CREATE INDEX IF NOT EXISTS idx_crossing_coin_candle ON crossing_events(coin, candle_start);
