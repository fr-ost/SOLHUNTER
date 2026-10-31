"""Central configuration for SolHunter.

Every tunable value lives here and is loaded from environment variables
(or a local ``.env`` file in development). Secrets are never defaulted.

Naming convention: flat settings use their upper-cased attribute name as the
environment variable (``DATABASE_URL``). Nested groups use a prefix
(``FILTER_``, ``RANK_``, ``AI_``, ``TELEGRAM_``, ``EXEC_``, ``RISK_``...).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]


def _split_csv(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [v.strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


class _Group(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class FilterSettings(_Group):
    """Thresholds for the multi-stage filtering engine (prefix ``FILTER_``)."""

    model_config = SettingsConfigDict(env_prefix="FILTER_", env_file=".env", extra="ignore")

    # Stage 1 - data validation
    max_data_age_seconds: int = 120
    min_token_age_seconds: int = 45
    max_token_age_minutes: int = 10_080  # 7 days: survivors are re-evaluated by the growth tracker
    min_trades_for_evaluation: int = 8

    # Age-scaled limits ("age_minutes:value,..." interpolated in log-age). Early launches are structurally
    # concentrated (dev + snipers hold most of the float before organic buyers arrive), so the limits
    # tighten as the token matures instead of rejecting every 2-minute-old token.
    top10_pct_curve: str = "1:0.85,5:0.70,15:0.55,60:0.42,360:0.35,1440:0.30,10080:0.25"
    top20_pct_curve: str = "1:0.95,5:0.85,15:0.70,60:0.58,360:0.50,1440:0.45,10080:0.40"
    largest_holder_curve: str = "1:0.35,5:0.25,15:0.18,60:0.12,360:0.10,1440:0.08,10080:0.06"
    min_holders_curve: str = "1:5,3:12,5:20,15:40,60:120,360:250,1440:400,10080:600"
    min_unique_buyers_5m_curve: str = "1:4,3:8,5:12,15:12,60:8,360:5,1440:3,10080:2"
    bundle_hard_fail_share: float = 0.40  # bundled launch wallets holding more than this of circulating = hard fail

    # Stage 2 - developer analysis
    max_creator_prior_rugs: int = 0
    max_creator_prior_launches: int = 6
    max_dev_holding_pct: float = 0.10
    reject_if_dev_sold_pct_over: float = 0.50
    dev_sell_warning_pct: float = 0.20

    # Stage 3 - holder distribution
    min_holders: int = 40
    max_largest_holder_pct: float = 0.12
    max_top10_pct: float = 0.40
    max_top20_pct: float = 0.55
    min_holder_growth_5m: int = 5

    # Stage 4 - wallet relationships
    max_cluster_holding_pct: float = 0.25
    max_coordinated_wallets: int = 6

    # Stage 5 - liquidity & sellability
    min_liquidity_usd: float = 4000.0
    min_liquidity_sol: float = 15.0
    max_sell_impact_pct_at_reference_size: float = 0.08
    reference_trade_size_usd: float = 250.0
    max_liquidity_drop_pct_5m: float = 0.25
    require_sellability_evidence: bool = True

    # Stage 6 - buyer quality
    min_unique_buyers_5m: int = 12
    min_smart_money_buyers_for_bonus: int = 1
    max_buyer_concentration_pct: float = 0.45

    # Stage 7 - trading pressure
    min_buy_sell_volume_ratio: float = 1.05
    min_net_sol_flow_5m: float = 0.0
    large_sell_pct_of_liquidity: float = 0.05
    max_large_sells_5m: int = 2

    # Stage 8 - momentum
    min_momentum_5m_pct: float = -5.0
    max_momentum_5m_pct: float = 150.0
    min_volume_acceleration: float = 0.8
    max_price_volatility_pct: float = 60.0

    # Stage 9 - manipulation
    max_wash_trade_score: float = 0.60
    max_coordinated_buy_score: float = 0.65
    max_same_wallet_trade_share: float = 0.35

    # Stage 10 - ML gates
    max_rug_risk: float = 0.45
    min_breakout_p10: float = 0.25


class GrowthSettings(_Group):
    """Survivor / growth tracking of tokens from 30 minutes up to 7 days (prefix ``GROWTH_``)."""

    model_config = SettingsConfigDict(env_prefix="GROWTH_", env_file=".env", extra="ignore")

    enabled: bool = True
    cycle_seconds: int = 60
    max_tokens_per_cycle: int = 450  # 15 DexScreener batch calls per cycle (limit 300/min)
    max_age_days: int = 7
    min_tracking_trades_to_follow: int = 12  # a token must have shown some life while tracked to enter the schedule
    checkpoint_hours: str = "0.5,1,2,3,6,12,24,36,48,72,96,120,168"
    fast_interval_minutes: int = 15  # re-check interval while a token is liquid and active
    fast_min_liquidity_usd: float = 8_000.0
    retrack_min_score: float = 0.62  # growth score needed to put a survivor back into real-time tracking
    retrack_min_liquidity_usd: float = 10_000.0
    retrack_cooldown_minutes: int = 120
    max_retracks_per_cycle: int = 5
    discovery_enabled: bool = True  # DexScreener token profiles/boosts -> tokens we never saw launch
    discovery_interval_minutes: int = 5
    dead_liquidity_usd: float = 500.0
    dead_after_hours_without_pair: int = 6
    holder_refresh_hours: float = 3.0  # Helius holder count/concentration refresh for liquid survivors (credit budgeted)
    holder_refresh_min_liquidity_usd: float = 8_000.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def checkpoints(self) -> list[float]:
        return sorted({float(v) for v in _split_csv(self.checkpoint_hours)})


class WhaleSettings(_Group):
    """Whale / smart-money engine thresholds (prefix ``WHALE_``)."""

    model_config = SettingsConfigDict(env_prefix="WHALE_", env_file=".env", extra="ignore")

    min_whale_trade_sol: float = 1.0
    min_whale_pct_of_liquidity: float = 0.02
    min_whale_position_sol: float = 2.0
    smart_money_min_closed_positions: int = 8
    smart_money_min_win_rate: float = 0.55
    smart_money_min_profit_factor: float = 1.5
    smart_money_min_realized_pnl_sol: float = 5.0
    early_buyer_window_seconds: int = 120
    sniper_window_seconds: int = 3
    consensus_min_wallets: int = 3
    distribution_exit_fraction: float = 0.5
    insider_sell_min_sol: float = 0.5
    cluster_min_confidence: float = 0.5
    profile_cache_seconds: int = 300


class RankingSettings(_Group):
    """Weights and thresholds for candidate ranking (prefix ``RANK_``). Weights are normalised."""

    model_config = SettingsConfigDict(env_prefix="RANK_", env_file=".env", extra="ignore")

    weight_safety: float = 2.0
    weight_liquidity: float = 1.5
    weight_smart_money: float = 1.5
    weight_whale_accumulation: float = 1.2
    weight_buyer_quality: float = 1.0
    weight_volume_acceleration: float = 0.8
    weight_momentum: float = 0.8
    weight_ml_breakout: float = 1.2
    weight_expected_return: float = 0.8
    weight_risk_reward: float = 1.0
    weight_gpt: float = 1.0
    weight_growth: float = 1.5

    min_candidate_age_minutes: int = 15  # never promote (or alert) in the first minutes: too noisy, too manipulable
    candidate_min_score: float = 68.0
    watchlist_min_score: float = 52.0
    gpt_min_score: float = 65.0
    alert_min_score: float = 72.0
    max_candidates_per_hour: int = 6


class ScannerSettings(_Group):
    model_config = SettingsConfigDict(env_prefix="SCANNER_", env_file=".env", extra="ignore")

    max_tracked_tokens: int = 250
    track_duration_minutes: int = 90
    snapshot_schedule_minutes: str = "1,3,5,10,15"
    periodic_snapshot_interval_seconds: int = 60
    evaluation_interval_seconds: int = 30
    heartbeat_interval_seconds: int = 15
    stale_after_seconds: int = 90
    leader_lock_ttl_seconds: int = 30
    http_health_enabled: bool = True
    http_health_port: int = 8081
    min_first_minute_trades: int = 3
    outcome_horizon_minutes: int = 60
    outcome_label_delay_minutes: int = 65
    flush_interval_seconds: float = 2.0
    flush_batch_size: int = 500
    holder_refresh_interval_seconds: int = 120
    dexscreener_refresh_interval_seconds: int = 30
    creator_history_cache_seconds: int = 3600
    retention_cleanup_interval_minutes: int = 60
    # Pump.fun bonding-curve constants (verify against pump.fun docs; used for progress/impact estimates)
    pump_initial_virtual_sol: float = 30.0
    pump_initial_virtual_tokens: float = 1_073_000_000.0
    pump_curve_tokens_to_sell: float = 793_100_000.0
    pump_total_supply: float = 1_000_000_000.0
    pump_migration_real_sol: float = 85.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def snapshot_schedule(self) -> list[int]:
        return sorted({int(v) for v in _split_csv(self.snapshot_schedule_minutes)})


class AISettings(_Group):
    """GPT research analyst settings (prefix ``AI_``)."""

    model_config = SettingsConfigDict(env_prefix="AI_", env_file=".env", extra="ignore")

    enabled: bool = True
    model: str = "gpt-5.6-terra"  # best cost/quality tier for structured research; "gpt-5.6-luna" = budget
    verify_model_on_start: bool = True
    timeout_seconds: float = 90.0
    max_retries: int = 2
    max_output_tokens: int = 3000
    reasoning_effort: str | None = None  # e.g. "low" / "medium" / "high" when supported by the model
    input_cost_per_1m_usd: float = 2.00  # gpt-5.6-terra list price (verify on openai.com/api/pricing)
    output_cost_per_1m_usd: float = 12.00
    daily_spend_limit_usd: float = 1.50
    monthly_spend_limit_usd: float = 25.0
    max_analyses_per_day: int = 60
    max_analyses_per_token: int = 2
    cache_ttl_seconds: int = 900
    queue_max_attempts: int = 3


class TelegramSettings(_Group):
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_", env_file=".env", extra="ignore")

    enabled: bool = False
    bot_token: str | None = None
    chat_id: str | None = None
    api_base_url: str = "https://api.telegram.org"
    alert_min_score: float = 72.0
    send_watchlist: bool = False
    cooldown_seconds: int = 1800
    urgent_cooldown_seconds: int = 600
    max_alerts_per_hour: int = 10
    timeout_seconds: float = 15.0


class ExecutionSettings(_Group):
    model_config = SettingsConfigDict(env_prefix="EXEC_", env_file=".env", extra="ignore")

    trade_sizes_usd: str = "100,250,500"
    pump_fee_bps: int = 100  # Pump.fun bonding curve fee (1%)
    amm_fee_bps: int = 25  # PumpSwap / Raydium style pool fee (0.25%)
    network_fee_sol: float = 0.000005
    priority_fee_sol: float = 0.0005
    max_acceptable_slippage_pct: float = 8.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trade_sizes(self) -> list[float]:
        return [float(v) for v in _split_csv(self.trade_sizes_usd)]


class RiskSettings(_Group):
    model_config = SettingsConfigDict(env_prefix="RISK_", env_file=".env", extra="ignore")

    account_equity_usd: float = 500.0
    risk_per_trade_pct: float = 1.0
    daily_loss_limit_pct: float = 2.5
    max_open_positions: int = 2
    stop_loss_pct: float = 6.0
    target_pcts: str = "10,15,20"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def targets(self) -> list[float]:
        return [float(v) for v in _split_csv(self.target_pcts)]


class MLSettings(_Group):
    model_config = SettingsConfigDict(env_prefix="ML_", env_file=".env", extra="ignore")

    min_training_samples: int = 400
    min_positive_samples: int = 25
    retrain_interval_hours: int = 24
    auto_train_enabled: bool = True
    train_on_start: bool = False
    walk_forward_folds: int = 4
    experimental_until_samples: int = 1500
    fee_and_slippage_pct: float = 3.0  # round-trip execution cost assumed when labelling net outcomes


class RetentionSettings(_Group):
    model_config = SettingsConfigDict(env_prefix="RETENTION_", env_file=".env", extra="ignore")

    raw_events_days: int = 3
    transactions_days: int = 30
    wallet_transactions_days: int = 30
    alerts_days: int = 180
    logs_days: int = 14
    # Only snapshots WITHOUT a training sample or candidate score are ever pruned (training data is kept).
    # 0 disables pruning entirely.
    snapshots_days: int = 45


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- application -------------------------------------------------------------
    app_name: str = "SolHunter"
    app_env: AppEnv = "development"
    service_role: Literal["all", "api", "scanner", "cli"] = "all"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    host: str = "0.0.0.0"
    port: int = Field(default=8080)
    public_api_base_url: str | None = None  # e.g. https://solhunter-api.up.railway.app
    dashboard_base_url: str | None = None  # link target used in Telegram alerts

    # --- database / redis --------------------------------------------------------
    database_url: str = "postgresql://postgres:postgres@localhost:5432/solhunter"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_echo: bool = False
    run_migrations_on_start: bool = True  # safe: advisory-locked; set false on a split scanner service
    migration_wait_timeout_seconds: int = 180
    redis_url: str = "redis://localhost:6379/0"

    # --- api security --------------------------------------------------------------
    password: str | None = None  # simple dashboard login; also grants settings (admin) access
    api_keys: str = ""  # optional extra comma separated read/write keys (advanced)
    api_admin_keys: str = ""  # optional extra comma separated admin keys (advanced)
    cors_allowed_origins: str = ""  # comma separated extra origins (the built-in dashboard is same-origin)
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 40
    ws_max_connections: int = 50

    # --- providers -----------------------------------------------------------------
    pumpportal_enabled: bool = True
    pumpportal_ws_url: str = "wss://pumpportal.fun/api/data"
    pumpportal_max_trade_subscriptions: int = 250
    max_tracked_wallets: int = 100
    wallet_alert_cooldown_seconds: int = 300
    dexscreener_enabled: bool = True
    dexscreener_base_url: str = "https://api.dexscreener.com"
    dexscreener_rpm_pairs: int = 300
    dexscreener_rpm_other: int = 60
    helius_enabled: bool = True
    helius_api_key: str | None = None
    helius_rpc_url: str = "https://mainnet.helius-rpc.com"
    helius_api_base_url: str = "https://api.helius.xyz"
    helius_rpc_rps: float = 10.0
    helius_enhanced_rps: float = 2.0
    helius_daily_credit_budget: int = 30000
    helius_monthly_credit_budget: int = 1000000
    solana_fallback_rpc_url: str = "https://api.mainnet-beta.solana.com"
    sol_usd_fallback_price: float | None = None  # used only if no live SOL price is available
    sol_usd_price_refresh_seconds: int = 60
    provider_http_timeout_seconds: float = 20.0

    # --- openai ----------------------------------------------------------------------
    openai_api_key: str | None = None
    openai_base_url: str | None = None

    # --- cost model (display only) ------------------------------------------------------
    cost_railway_estimate_usd_month: float = 20.0
    cost_helius_plan_usd_month: float = 0.0

    # --- nested groups ---------------------------------------------------------------
    filters: FilterSettings = Field(default_factory=FilterSettings)
    ranking: RankingSettings = Field(default_factory=RankingSettings)
    whale: WhaleSettings = Field(default_factory=WhaleSettings)
    growth: GrowthSettings = Field(default_factory=GrowthSettings)
    scanner: ScannerSettings = Field(default_factory=ScannerSettings)
    ai: AISettings = Field(default_factory=AISettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    ml: MLSettings = Field(default_factory=MLSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, v: str) -> str:
        return v.upper()

    # --- derived helpers -------------------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_database_url(self) -> str:
        """Railway injects ``postgresql://``; SQLAlchemy async needs the asyncpg driver."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url

    @computed_field  # type: ignore[prop-decorator]
    @property
    def api_key_list(self) -> list[str]:
        keys = _split_csv(self.api_keys)
        if self.password and self.password.strip():
            keys.append(self.password.strip())
        return keys

    @computed_field  # type: ignore[prop-decorator]
    @property
    def admin_key_list(self) -> list[str]:
        keys = _split_csv(self.api_admin_keys)
        if self.password and self.password.strip():
            keys.append(self.password.strip())
        return keys

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_origins(self) -> list[str]:
        return sorted(set(_split_csv(self.cors_allowed_origins)))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def helius_rpc_endpoint(self) -> str | None:
        if not self.helius_api_key:
            return None
        sep = "&" if "?" in self.helius_rpc_url else "?"
        return f"{self.helius_rpc_url}{sep}api-key={self.helius_api_key}"

    def validate_for_production(self) -> list[str]:
        """Return a list of configuration problems that make production unsafe."""
        problems: list[str] = []
        if not self.is_production:
            return problems
        if not self.api_key_list and not self.admin_key_list:
            problems.append("PASSWORD must be set in production (dashboard login)")
        if "localhost" in self.database_url or "127.0.0.1" in self.database_url:
            problems.append("DATABASE_URL points at localhost in production")
        if "localhost" in self.redis_url or "127.0.0.1" in self.redis_url:
            problems.append("REDIS_URL points at localhost in production")
        if self.ai.enabled and not self.openai_api_key:
            problems.append("AI_ENABLED is true but OPENAI_API_KEY is missing (GPT analysis will be disabled)")
        if self.telegram.enabled and not (self.telegram.bot_token and self.telegram.chat_id):
            problems.append("TELEGRAM_ENABLED is true but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are missing")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Clear the cached settings (used by tests)."""
    get_settings.cache_clear()
    return get_settings()
