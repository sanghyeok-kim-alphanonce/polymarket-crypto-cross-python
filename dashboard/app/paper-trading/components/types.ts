// Shared Chart Types
export interface PnlChartDataPoint {
  timestamp: number;
  timeStr: string;
  pnl: number;
  cumPnl: number;
  coin: string;
}

// Legacy Types
export interface Position {
  id: number;
  coin: string;
  timeframe: string;
  token_id: string;
  market_slug: string;
  side: string;
  total_size: number;
  avg_price: number;
  total_cost: number;
  unrealized_pnl: number;
  updated_at: string;
}

export interface Balance {
  id: number;
  coin: string;
  timeframe: string;
  initial_balance: number;
  current_balance: number;
  reserved: number;
  total_pnl: number;
  updated_at: string;
}

export interface Trade {
  id: number;
  time: string;
  coin: string;
  timeframe: string;
  token_id: string;
  market_slug: string;
  side: string;
  action: string;
  entry_price: number;
  size: number;
  cost: number;
  model_prob: number;
  orderbook_price: number;
  kelly_fraction: number;
  status: string;
  pnl: number | null;
  outcome: string | null;
  market_result: string | null;
  contracts: number | null;
}

export interface Summary {
  total_trades: number;
  wins: number;
  losses: number;
  total_pnl: number;
  total_invested: number;
  total_initial: number;
  total_current: number;
  total_reserved: number;
}

export interface ChartDataPoint {
  time: string;
  coin: string;
  pnl: number;
}

export interface PaperTradingData {
  positions: Position[];
  balances: Balance[];
  trades: Trade[];
  chartData?: ChartDataPoint[];
  summary: Summary;
}

// V12 HFT Types
export interface V12Trade {
  id: number;
  time: string;
  coin: string;
  threshold_id: string;
  delta_threshold: number;
  side: string;
  delta_price: number;
  delta_pct: number;
  entry_price: number;
  exit_price: number | null;
  exit_target_price: number | null;
  exit_type: string | null;
  hold_seconds: number | null;
  cost: number;
  status: string;
  pnl: number | null;
  elapsed_minutes: number | null;
}

export interface V12ThresholdSummary {
  threshold_id: string;
  coin: string;
  delta_threshold: number;
  total_trades: number;
  open_trades: number;
  wins: number;
  losses: number;
  total_cost: number;
  total_pnl: number;
  avg_pnl: number;
  avg_delta_price: number;
  avg_elapsed: number;
  up_trades: number;
  down_trades: number;
  up_avg_entry: number;
  down_avg_entry: number;
}

export interface V12Overall {
  total_trades: number;
  open_trades: number;
  wins: number;
  losses: number;
  total_cost: number;
  total_pnl: number;
  threshold_count: number;
}

export interface V12CoinStat {
  coin: string;
  total_trades: number;
  wins: number;
  losses: number;
  total_pnl: number;
  total_cost: number;
  up_trades: number;
  down_trades: number;
  up_avg_entry: number;
  down_avg_entry: number;
}

export interface V12SideStat {
  side: string;
  total_trades: number;
  wins: number;
  losses: number;
  total_pnl: number;
}

export interface V12Recent {
  trades_5min: number;
  cost_5min: number;
}

export interface V12MarketStat {
  coin: string;
  market_slug: string;
  candle_start_time: string;
  candle_end_time: string;
  status: string;
  total_trades: number;
  up_trades: number;
  down_trades: number;
  up_avg_entry: number;
  down_avg_entry: number;
  up_total_cost: number;
  down_total_cost: number;
  up_pnl: number;
  down_pnl: number;
  total_pnl: number;
  total_cost: number;
  wins: number;
  losses: number;
}

export interface V12ChartDataPoint {
  time: string;
  pnl: number;
  cumPnl: number;
  tradeCount: number;
}

export interface V12Data {
  trades: V12Trade[];
  thresholdSummary: V12ThresholdSummary[];
  overall: V12Overall;
  coinStats: V12CoinStat[];
  sideStats: V12SideStat[];
  recent: V12Recent;
  marketStats: V12MarketStat[];
  chartData?: V12ChartDataPoint[];
}

// V14 Types (Reversal Signal Strategy)
export interface V14Trade {
  id: number;
  entry_time: string;
  coin: string;
  candle_open: number;
  candle_high: number;
  candle_low: number;
  candle_close: number;
  btr: number;
  opposite_tail: number;
  reversal_rate: number;
  elapsed_min: number;
  bet_side: string;
  entry_price: number;
  exit_price: number | null;
  contracts: number;
  cost: number;
  pnl: number | null;
  status: string;
  is_additional: boolean;
}

export interface V14ElapsedStat {
  elapsed_min: number;
  total_trades: number;
  open_trades: number;
  wins: number;
  losses: number;
  total_cost: number;
  total_pnl: number;
  avg_pnl: number;
  avg_opposite_tail: number;
  avg_btr: number;
  avg_reversal_rate: number;
  win_rate: number;
}

export interface V14Overall {
  total_trades: number;
  open_trades: number;
  wins: number;
  losses: number;
  total_cost: number;
  total_pnl: number;
  avg_pnl: number;
  avg_opposite_tail: number;
  avg_btr: number;
  win_rate: number;
}

export interface V14CoinStat {
  coin: string;
  total_trades: number;
  wins: number;
  losses: number;
  total_pnl: number;
  total_cost: number;
  up_trades: number;
  down_trades: number;
  avg_opposite_tail?: number;
  avg_btr?: number;
  win_rate: number;
}

export interface V14SideStat {
  side: string;
  total_trades: number;
  wins: number;
  losses: number;
  total_pnl: number;
  avg_opposite_tail?: number;
  avg_btr?: number;
  win_rate: number;
}

export interface V14Recent {
  trades_5min: number;
  cost_5min: number;
  wins_5min: number;
  losses_5min: number;
  pnl_5min: number;
}

export interface V14Data {
  trades: V14Trade[];
  elapsedStats: V14ElapsedStat[];
  overall: V14Overall;
  coinStats: V14CoinStat[];
  sideStats: V14SideStat[];
  recent: V14Recent;
}

// Dual Source Types (Redis vs WebSocket comparison)
export interface DualSourceTrade {
  id: number;
  time: string;
  strategy_name: string;
  coin: string;
  timeframe: string;
  prev_price: number;
  curr_price: number;
  delta_price: number;
  delta_threshold: number;
  side: string;
  is_late: boolean;
  elapsed_minutes: number;
  // Redis orderbook
  redis_best_bid: number;
  redis_best_ask: number;
  redis_spread: number;
  redis_bid_size: number;
  redis_ask_size: number;
  // WebSocket orderbook
  ws_best_bid: number;
  ws_best_ask: number;
  ws_spread: number;
  ws_bid_size: number;
  ws_ask_size: number;
  // Actual entry (Redis)
  actual_entry_price: number;
  actual_contracts: number;
  actual_cost: number;
  // Virtual entry (WebSocket)
  virtual_entry_price: number;
  virtual_contracts: number;
  virtual_cost: number;
  entry_price_diff: number;
  // Market info
  market_slug: string;
  candle_start_time: string;
  candle_end_time: string;
  // Settlement
  market_result: string | null;
  actual_pnl: number | null;
  virtual_pnl: number | null;
  pnl_diff: number | null;
  status: string;
}

export interface DualSourceOverall {
  total_trades: number;
  open_trades: number;
  closed_trades: number;
  redis_wins: number;
  redis_losses: number;
  ws_wins: number;
  ws_losses: number;
  redis_total_cost: number;
  ws_total_cost: number;
  redis_total_pnl: number;
  ws_total_pnl: number;
  total_pnl_diff: number;
  avg_entry_diff: number;
  avg_abs_entry_diff: number;
  ws_higher_count: number;
  ws_lower_count: number;
  same_price_count: number;
}

export interface DualSourceCoinStat {
  coin: string;
  total_trades: number;
  closed_trades: number;
  redis_wins: number;
  redis_losses: number;
  ws_wins: number;
  ws_losses: number;
  redis_pnl: number;
  ws_pnl: number;
  pnl_diff: number;
  avg_entry_diff: number;
  redis_avg_entry: number;
  ws_avg_entry: number;
  ws_higher: number;
  ws_lower: number;
}

export interface DualSourceSideStat {
  side: string;
  total_trades: number;
  redis_wins: number;
  redis_losses: number;
  redis_pnl: number;
  ws_pnl: number;
  pnl_diff: number;
  avg_entry_diff: number;
}

export interface DualSourceDiffDist {
  diff_range: string;
  count: number;
  redis_pnl: number;
  ws_pnl: number;
}

export interface DualSourceChartPoint {
  time: string;
  redisPnl: number;
  wsPnl: number;
  redisCumPnl: number;
  wsCumPnl: number;
  pnlDiff: number;
  tradeCount: number;
  avgEntryDiff: number;
}

export interface DualSourceRecent {
  trades_5min: number;
  cost_5min: number;
  avg_entry_diff_5min: number;
}

export interface DualSourceData {
  trades: DualSourceTrade[];
  overall: DualSourceOverall;
  coinStats: DualSourceCoinStat[];
  sideStats: DualSourceSideStat[];
  diffDistribution: DualSourceDiffDist[];
  chartData: DualSourceChartPoint[];
  recent: DualSourceRecent;
}
