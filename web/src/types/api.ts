// Types for the SolHunter API responses. The backend serialises ORM rows as plain JSON objects with
// ISO-8601 timestamps; optional/unknown values are null (never faked as zero).

export type Json = Record<string, unknown>;

export interface Snapshot {
  id?: number;
  mint?: string;
  captured_at: string;
  snapshot_kind?: string;
  age_seconds: number | null;
  pool_kind?: string | null;
  price_sol: number | null;
  price_usd: number | null;
  sol_usd?: number | null;
  market_cap_sol?: number | null;
  market_cap_usd: number | null;
  fdv_usd?: number | null;
  liquidity_sol?: number | null;
  liquidity_usd: number | null;
  bonding_progress?: number | null;
  trades_total?: number | null;
  buys_1m?: number | null;
  sells_1m?: number | null;
  buys_5m: number | null;
  sells_5m: number | null;
  buy_volume_sol_5m?: number | null;
  sell_volume_sol_5m?: number | null;
  volume_sol_1m?: number | null;
  volume_sol_5m: number | null;
  volume_sol_15m?: number | null;
  net_sol_flow_5m?: number | null;
  unique_buyers_5m: number | null;
  unique_sellers_5m?: number | null;
  unique_buyers_total?: number | null;
  holder_count: number | null;
  largest_holder_pct: number | null;
  top10_pct: number | null;
  top20_pct?: number | null;
  dev_holding_pct?: number | null;
  momentum_1m_pct?: number | null;
  momentum_3m_pct?: number | null;
  momentum_5m_pct?: number | null;
  momentum_10m_pct?: number | null;
  momentum_15m_pct?: number | null;
  smart_money_buyers?: number | null;
  whale_net_flow_sol_5m?: number | null;
  data_quality?: Json | null;
}

export interface CandidateItem {
  id: number;
  mint: string;
  snapshot_id?: number | null;
  scored_at: string;
  status: "candidate" | "watchlist" | "rejected" | string;
  overall_score: number;
  confidence: "high" | "medium" | "low" | string;
  components: Record<string, number>;
  rejection_reasons: string[] | null;
  hard_fail: boolean;
  whale_signal: string | null;
  rug_risk: number | null;
  breakout_p10: number | null;
  breakout_p15: number | null;
  breakout_p20: number | null;
  expected_return_pct: number | null;
  ml_is_experimental: boolean;
  model_version: string | null;
  gpt_recommendation: string | null;
  gpt_analysis_id?: number | null;
  rank?: number | null;
  explanation: string[] | null;
  alerted?: boolean;
  is_latest?: boolean;
  symbol: string | null;
  name: string | null;
  launch_time: string | null;
  pool_kind?: string | null;
  token_status?: string | null;
  latest_snapshot?: Snapshot | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Token {
  mint: string;
  name: string | null;
  symbol: string | null;
  uri?: string | null;
  decimals?: number;
  creator_wallet: string | null;
  source?: string;
  launch_time: string | null;
  bonding_curve_key?: string | null;
  pool_kind: string;
  status: string;
  status_reason?: string | null;
  migrated_at?: string | null;
  first_seen_at: string;
  last_activity_at?: string | null;
  tracking_until?: string | null;
  total_supply?: number | null;
  created_at?: string;
  updated_at?: string;
}

export interface FilterStage {
  stage: string;
  name: string;
  passed: boolean;
  hard_fail: boolean;
  safety: boolean;
  score: number;
  reasons: string[];
  metrics: Json;
  data_available: boolean;
}

export interface FilterResults {
  rejected: boolean;
  hard_fail: boolean;
  candidate_eligible?: boolean;
  rejection_reasons: string[];
  quality_failures: string[];
  stage_scores: Record<string, number>;
  stages: FilterStage[];
}

export interface ExecutionEstimate {
  size_usd: number;
  size_sol: number | null;
  available: boolean;
  reason?: string | null;
  spot_price_sol?: number | null;
  executable_buy_price_sol?: number | null;
  tokens_received?: number | null;
  buy_impact_pct: number | null;
  sell_impact_pct: number | null;
  immediate_exit_proceeds_sol?: number | null;
  immediate_exit_proceeds_usd: number | null;
  round_trip_cost_pct: number | null;
  fees_usd: number | null;
  network_fees_usd?: number | null;
  targets: Record<string, { exit_proceeds_usd: number | null; net_return_pct: number | null; sell_impact_pct?: number | null }>;
}

export interface ExecutionReport {
  venue: string;
  source: string;
  reserves_sol: number | null;
  reserves_tokens: number | null;
  sol_usd: number | null;
  fee_bps: number;
  estimates: ExecutionEstimate[];
  max_size_usd_within_slippage: number | null;
  notes: string[];
  disclaimer?: string;
}

export interface WhaleActivity {
  wallet: string;
  short: string;
  category: string;
  confidence: number;
  direction: "buy" | "sell" | string;
  sol_amount: number;
  usd_amount: number | null;
  pct_of_liquidity: number | null;
  timestamp: string;
  signature: string;
  performance: Json;
  corroborated: boolean;
  cluster_id: string | null;
}

export interface WhaleSummary {
  primary_signal: string;
  signals: string[];
  whale_net_flow_1m: number;
  whale_net_flow_5m: number;
  whale_net_flow_15m: number;
  whale_net_flow_1h: number;
  whale_buy_sol_5m: number;
  whale_sell_sol_5m: number;
  whales_buying_5m: number;
  whales_selling_5m: number;
  whales_holding: number;
  whales_distributing: number;
  smart_money_buyers: number;
  smart_money_sellers: number;
  independent_profitable_buyers: number;
  smart_money_net_sol: number;
  insider_sell_sol: number;
  dev_sell_sol: number;
  accumulation_trend: string;
  accumulation_score: number;
  distribution_score: number;
  hard_warning: string | null;
  activities: WhaleActivity[];
  data_confidence: string;
  notes: string[];
}

export interface GptAnalysisRow {
  id: number;
  mint: string;
  model: string;
  created_at: string;
  status: string;
  recommendation: string | null;
  analysis: Json | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  estimated_cost_usd?: number | null;
  latency_ms?: number | null;
  error?: string | null;
}

export interface WalletProfile {
  address: string;
  category: string;
  category_confidence: number;
  label?: string | null;
  is_infrastructure: boolean;
  realized_pnl_sol: number;
  unrealized_pnl_sol: number;
  win_rate: number | null;
  avg_return_pct: number | null;
  median_return_pct: number | null;
  profit_factor: number | null;
  avg_holding_seconds: number | null;
  early_entry_win_rate: number | null;
  max_drawdown_pct?: number | null;
  rug_exposure_count: number;
  tokens_traded: number;
  closed_positions: number;
  trades_count: number;
  total_sol_bought?: number;
  total_sol_sold?: number;
  first_seen_at: string | null;
  last_active_at: string | null;
  classification_reasons?: string[] | null;
}

export interface CandidateDetail {
  token: Token;
  latest_score: (CandidateItem & { filter_results?: FilterResults; execution?: ExecutionReport; whale_summary?: WhaleSummary; penalties?: Record<string, number> }) | null;
  score_history: { id: number; scored_at: string; overall_score: number; status: string; confidence: string }[];
  snapshots: Snapshot[];
  holders: {
    captured_at: string;
    holder_count: number | null;
    total_supply: number | null;
    largest_holder_pct: number | null;
    top10_pct: number | null;
    top20_pct: number | null;
    dev_holding_pct: number | null;
    infra_excluded_pct: number | null;
    top_holders: { owner: string | null; token_account: string; amount: number; is_infrastructure: boolean; label: string | null }[] | null;
    source: string;
    is_complete: boolean;
  } | null;
  launches: Json[];
  pools: Json[];
  whale_events: Json[];
  smart_money_events: Json[];
  risk_events: { id: number; risk_type: string; severity: string; title: string; occurred_at: string; details?: Json | null }[];
  predictions: Json[];
  gpt_analysis: GptAnalysisRow | null;
  history: { id: number; event: string; from_status: string | null; to_status: string | null; score: number | null; reason: string | null; occurred_at: string }[];
  wallet_profiles: WalletProfile[];
  creator: { creator_wallet: string; profile: WalletProfile | null; prior_launches: { mint: string; symbol: string | null; status: string; launch_time: string | null; status_reason: string | null }[] } | null;
  execution: ExecutionReport | null;
  whale_summary: WhaleSummary | null;
  filter_results: FilterResults | null;
}

export interface ScannerStatus {
  status: string;
  worker_id?: string;
  is_leader?: boolean;
  last_heartbeat_at: string | null;
  heartbeat_age_seconds?: number | null;
  stale: boolean;
  started_at?: string;
  stats?: Json;
  error?: string;
}

export interface ProviderStatus {
  provider: string;
  status: string;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
  reconnect_count: number;
  details: Json;
}

export interface Costs {
  month_to_date_usd: number;
  projected_month_usd: number;
  breakdown: {
    railway_estimate_usd: number;
    helius_plan_usd: number;
    openai_month_to_date_usd: number;
    openai_projected_usd: number;
    providers: { provider: string; requests_month: number; credits_month: number; estimated_cost_usd_month: number }[];
  };
  helius_credits: { month_to_date: number; monthly_budget: number; daily_budget: number };
  note: string;
}

export interface Dashboard {
  scanner: ScannerStatus;
  providers: ProviderStatus[];
  counts_24h: { discovered: number; rejected: number; candidates: number; watchlist: number; high_risk_alerts: number };
  live_counters: Record<string, number>;
  top_candidates: (CandidateItem & Json)[];
  recent_whale_activity: Json[];
  recent_smart_money: Json[];
  recent_alerts: Json[];
  costs: Costs;
  queue: Record<string, number>;
}

export interface SystemStatus {
  api: { status: string; version: string; environment: string; memory_mb: number; ws_clients: number; pid: number };
  database: { ok: boolean; error: string | null };
  redis: { ok: boolean; error: string | null };
  scanner: ScannerStatus;
  workers: Json[];
  providers: ProviderStatus[];
  queue: Record<string, number>;
  live_counters: Record<string, number>;
  incomplete_data_warning: string | null;
  config_warnings: string[];
}

export interface AiStatus {
  enabled: boolean;
  model: string;
  today: { analyses: number; failed: number; input_tokens: number; output_tokens: number; estimated_cost_usd: number; limit_usd: number; max_analyses: number };
  month: { estimated_cost_usd: number; analyses: number; input_tokens: number; output_tokens: number; limit_usd: number };
  pricing_assumption: { input_per_1m_usd: number; output_per_1m_usd: number; note: string };
}

export interface ModelVersionInfo {
  id: number;
  name: string;
  version: string;
  trained_at: string;
  training_samples: number;
  positive_samples: number | null;
  metrics: Json;
  is_active: boolean;
  is_experimental: boolean;
  notes: string | null;
}

export interface Trade {
  id: number;
  mint: string;
  symbol: string | null;
  candidate_score_id: number | null;
  is_paper: boolean;
  status: "open" | "closed" | string;
  entry_price_usd: number;
  entry_at: string;
  entry_liquidity_usd: number | null;
  entry_market_cap_usd: number | null;
  position_size_usd: number;
  planned_stop_pct: number | null;
  planned_targets_pct: number[] | null;
  exit_price_usd: number | null;
  exit_at: string | null;
  fees_usd: number;
  slippage_usd: number;
  realized_pnl_usd: number | null;
  realized_return_pct: number | null;
  entry_reason: string | null;
  exit_reason: string | null;
  signal_correct: boolean | null;
  signal_category: string | null;
  candidate_type: string | null;
  model_version: string | null;
  gpt_recommendation: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface TradeCreate {
  mint: string;
  symbol?: string | null;
  candidate_score_id?: number | null;
  is_paper: boolean;
  entry_price_usd: number;
  entry_liquidity_usd?: number | null;
  entry_market_cap_usd?: number | null;
  position_size_usd: number;
  planned_stop_pct?: number | null;
  planned_targets_pct?: number[] | null;
  fees_usd?: number;
  slippage_usd?: number;
  entry_reason?: string | null;
  signal_category?: string | null;
  candidate_type?: string | null;
  model_version?: string | null;
  gpt_recommendation?: string | null;
}

export interface TradeUpdate {
  status?: "open" | "closed";
  exit_price_usd?: number;
  exit_at?: string;
  fees_usd?: number;
  slippage_usd?: number;
  exit_reason?: string;
  signal_correct?: boolean;
}

export interface PerformanceBucket {
  trades: number;
  win_rate?: number;
  total_pnl_usd?: number;
  avg_return_pct?: number;
  median_return_pct?: number;
  profit_factor?: number | null;
  max_drawdown_usd?: number;
  signal_accuracy?: number;
}

export interface Performance {
  overall: PerformanceBucket;
  by_candidate_type: Record<string, PerformanceBucket>;
  by_model_version: Record<string, PerformanceBucket>;
  by_signal_category: Record<string, PerformanceBucket>;
  by_gpt_recommendation: Record<string, PerformanceBucket>;
  paper_vs_manual: { paper: PerformanceBucket; manual: PerformanceBucket };
}

export interface RiskState {
  open_positions: number;
  max_open_positions: number;
  can_open_new: boolean;
  realized_pnl_today_usd: number;
  daily_loss_limit_usd: number;
  daily_loss_limit_hit: boolean;
}

export interface PositionSize {
  account_equity_usd: number;
  risk_per_trade_pct: number;
  risk_usd: number;
  stop_loss_pct: number;
  position_size_usd: number;
  token_quantity: number | null;
  stop_price_usd: number;
  targets: { pct: number; price_usd: number; profit_usd: number }[];
  daily_loss_limit_usd: number;
  max_open_positions: number;
  note: string;
}

export interface JournalEntry {
  id: number;
  trade_id: number | null;
  mint: string | null;
  entry_type: string;
  title: string | null;
  content: string;
  tags: string[] | null;
  created_at: string;
}

export interface SettingsView {
  effective: Json;
  overridable: Record<string, unknown>;
  overrides: Record<string, unknown>;
}

export interface LogRecord {
  event?: string;
  level?: string;
  timestamp?: string;
  logger?: string;
  source?: string;
  [key: string]: unknown;
}

export interface HistoryItem extends CandidateItem {
  outcome: {
    label_complete: boolean;
    max_return_pct: number | null;
    min_return_pct: number | null;
    close_return_pct: number | null;
    hit_10: boolean | null;
    hit_15: boolean | null;
    hit_20: boolean | null;
    hit_stop: boolean | null;
    rugged: boolean | null;
    horizon_minutes: number;
  } | null;
}

export interface LiveEvent {
  type: string;
  mint?: string;
  symbol?: string | null;
  status?: string;
  score?: number;
  [key: string]: unknown;
}

export interface TrackedWalletInput {
  address: string;
  label?: string | null;
  notes?: string | null;
  alert_on_buy?: boolean;
  alert_on_sell?: boolean;
  min_alert_sol?: number;
  auto_track_tokens?: boolean;
}

export interface TrackedWallet extends Required<Omit<TrackedWalletInput, "label" | "notes">> {
  label: string | null;
  notes: string | null;
  active: boolean;
  trades_seen: number;
  last_trade_at: string | null;
  created_at: string;
  trades_24h?: number;
  buy_sol_24h?: number;
  sell_sol_24h?: number;
  net_sol_24h?: number;
  tokens_24h?: number;
  profile?: { category: string; category_confidence: number; realized_pnl_sol: number; unrealized_pnl_sol: number; win_rate: number | null; tokens_traded: number; closed_positions: number };
}

export interface TrackedTrade {
  signature: string;
  wallet: string;
  label: string | null;
  side: "buy" | "sell";
  sol_amount: number | null;
  token_amount: number | null;
  price_sol: number | null;
  market_cap_sol: number | null;
  block_time: string;
  mint: string;
  symbol: string | null;
  name: string | null;
  token_status: string | null;
  score: number | null;
  score_status: string | null;
  rug_risk: number | null;
}
