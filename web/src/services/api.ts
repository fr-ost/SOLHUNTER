import { effectiveBaseUrl, type AppSettings } from "./storage";
import type {
  AiStatus,
  CandidateDetail,
  CandidateItem,
  Dashboard,
  HistoryItem,
  JournalEntry,
  Json,
  LogRecord,
  ModelVersionInfo,
  Paged,
  Performance,
  PositionSize,
  ProviderStatus,
  RiskState,
  SettingsView,
  Snapshot,
  SystemStatus,
  Token,
  Trade,
  TradeCreate,
  TradeUpdate,
  WalletProfile,
  TrackedTrade,
  TrackedWallet,
  TrackedWalletInput,
} from "../types/api";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }

  get isAuth(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isRateLimited(): boolean {
    return this.status === 429;
  }

  get userMessage(): string {
    if (this.status === 0) return "Cannot reach the API. Check that the SolHunter service is running (and the base URL override in Settings, if any).";
    if (this.status === 401) return "Unauthorized: the API key is missing or invalid (Settings).";
    if (this.status === 403) return "Forbidden: this action needs an admin key (Settings).";
    if (this.status === 429) return "Rate limited by the API - slow down and retry in a moment.";
    if (this.status === 503) return `Service unavailable: ${this.detail}`;
    return `${this.status}: ${this.detail}`;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

const qs = (query?: Query): string => {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
};

export class ApiClient {
  constructor(private readonly config: AppSettings) {}

  /** Configured once an API key exists; the base URL always resolves (page origin by default). */
  get configured(): boolean {
    return Boolean(this.config.apiKey);
  }

  get baseUrl(): string {
    return effectiveBaseUrl(this.config);
  }

  wsUrl(): string | null {
    if (!this.configured) return null;
    const url = new URL(this.baseUrl);
    url.protocol = url.protocol === "http:" ? "ws:" : "wss:";
    url.pathname = `${url.pathname.replace(/\/$/, "")}/ws`;
    url.search = `?token=${encodeURIComponent(this.config.apiKey)}`;
    return url.toString();
  }

  async request<T>(method: string, path: string, body?: unknown, opts: { admin?: boolean; query?: Query } = {}): Promise<T> {
    const key = opts.admin && this.config.adminKey ? this.config.adminKey : this.config.apiKey;
    const headers: Record<string, string> = { Accept: "application/json" };
    if (key) headers["X-API-Key"] = key;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}${qs(opts.query)}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      throw new ApiError(0, err instanceof Error ? err.message : "network error");
    }
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    if (!response.ok) {
      const detail = (data as { detail?: unknown } | null)?.detail;
      throw new ApiError(response.status, typeof detail === "string" ? detail : text.slice(0, 200) || response.statusText);
    }
    return data as T;
  }

  // ---- public ---------------------------------------------------------------
  health = (): Promise<{ status: string; service: string; version: string; environment: string }> => this.request("GET", "/health");
  ready = (): Promise<{ status: string; database: boolean; redis: boolean; schema_revision: string | null }> => this.request("GET", "/ready");

  // ---- dashboard / status ---------------------------------------------------
  dashboard = (): Promise<Dashboard> => this.request("GET", "/api/v1/dashboard");
  systemStatus = (): Promise<SystemStatus> => this.request("GET", "/api/v1/status/system");
  providers = (): Promise<{ items: ProviderStatus[] }> => this.request("GET", "/api/v1/status/providers");
  costs = (): Promise<Dashboard["costs"]> => this.request("GET", "/api/v1/status/costs");
  aiStatus = (): Promise<AiStatus> => this.request("GET", "/api/v1/status/ai");
  models = (): Promise<{ items: ModelVersionInfo[] }> => this.request("GET", "/api/v1/status/models");
  logs = (query: { source?: string; level?: string; limit?: number }): Promise<{ items: LogRecord[] }> => this.request("GET", "/api/v1/logs", undefined, { query });

  // ---- candidates / tokens --------------------------------------------------
  candidates = (query: { status?: string; limit?: number; offset?: number; min_score?: number }): Promise<Paged<CandidateItem>> =>
    this.request("GET", "/api/v1/candidates", undefined, { query });
  candidate = (mint: string): Promise<CandidateDetail> => this.request("GET", `/api/v1/candidates/${encodeURIComponent(mint)}`);
  searchTokens = (q: string, limit = 20): Promise<{ items: Token[]; query: string }> => this.request("GET", "/api/v1/tokens/search", undefined, { query: { q, limit } });
  recentTokens = (query: { status?: string; limit?: number; offset?: number }): Promise<Paged<Token>> => this.request("GET", "/api/v1/tokens/recent", undefined, { query });
  token = (mint: string): Promise<{ token: Token; latest_snapshot: Snapshot | null; latest_score: CandidateItem | null; pools: Json[] }> =>
    this.request("GET", `/api/v1/tokens/${encodeURIComponent(mint)}`);
  tokenSnapshots = (mint: string, limit = 300): Promise<{ items: Snapshot[] }> => this.request("GET", `/api/v1/tokens/${encodeURIComponent(mint)}/snapshots`, undefined, { query: { limit } });
  tokenTransactions = (mint: string, limit = 100): Promise<{ items: Json[] }> => this.request("GET", `/api/v1/tokens/${encodeURIComponent(mint)}/transactions`, undefined, { query: { limit } });
  tokenRiskEvents = (mint: string): Promise<{ items: Json[] }> => this.request("GET", `/api/v1/tokens/${encodeURIComponent(mint)}/risk-events`);
  trackToken = (mint: string, reason?: string): Promise<{ queued: boolean; mint: string }> => this.request("POST", "/api/v1/tokens/track", { mint, reason: reason ?? "requested from dashboard" });

  // ---- wallets / whales -----------------------------------------------------
  topWallets = (query: { category?: string; sort?: string; limit?: number }): Promise<{ items: WalletProfile[] }> => this.request("GET", "/api/v1/wallets/top", undefined, { query });
  wallet = (address: string): Promise<{ address: string; profile: WalletProfile | null; positions: Json[]; relationships: Json[]; whale_events: Json[]; smart_money_events: Json[] }> =>
    this.request("GET", `/api/v1/wallets/${encodeURIComponent(address)}`);
  walletTransactions = (address: string, limit = 100): Promise<{ items: Json[] }> => this.request("GET", `/api/v1/wallets/${encodeURIComponent(address)}/transactions`, undefined, { query: { limit } });
  whalesRecent = (query: { limit?: number; mint?: string }): Promise<{ whale_events: Json[]; smart_money_events: Json[] }> => this.request("GET", "/api/v1/whales/recent", undefined, { query });
  whaleSignals = (limit = 50): Promise<{ items: Json[] }> => this.request("GET", "/api/v1/whales/signals", undefined, { query: { limit } });

  // ---- history --------------------------------------------------------------
  historyCandidates = (query: { status?: string; since?: string; until?: string; limit?: number; offset?: number }): Promise<Paged<HistoryItem>> =>
    this.request("GET", "/api/v1/history/candidates", undefined, { query });
  outcomeSummary = (): Promise<Record<string, Json>> => this.request("GET", "/api/v1/history/outcomes/summary");
  alertHistory = (query: { limit?: number; offset?: number }): Promise<Paged<Json>> => this.request("GET", "/api/v1/history/alerts", undefined, { query });

  // ---- trading --------------------------------------------------------------
  trades = (query: { status?: string; limit?: number; offset?: number }): Promise<Paged<Trade>> => this.request("GET", "/api/v1/trades", undefined, { query });
  createTrade = (body: TradeCreate): Promise<{ trade: Trade; risk_state: RiskState; risk_warning: string | null }> => this.request("POST", "/api/v1/trades", body);
  updateTrade = (id: number, body: TradeUpdate): Promise<{ trade: Trade }> => this.request("PATCH", `/api/v1/trades/${id}`, body);
  deleteTrade = (id: number): Promise<void> => this.request("DELETE", `/api/v1/trades/${id}`);
  performance = (): Promise<Performance> => this.request("GET", "/api/v1/trades/performance");
  riskState = (): Promise<RiskState> => this.request("GET", "/api/v1/risk/state");
  positionSize = (entryPriceUsd: number, stopPct?: number): Promise<PositionSize> =>
    this.request("GET", "/api/v1/risk/position-size", undefined, { query: { entry_price_usd: entryPriceUsd, stop_pct: stopPct } });
  journal = (query: { mint?: string; limit?: number }): Promise<{ items: JournalEntry[] }> => this.request("GET", "/api/v1/journal", undefined, { query });
  createJournal = (body: { trade_id?: number | null; mint?: string | null; entry_type?: string; title?: string | null; content: string; tags?: string[] | null }): Promise<JournalEntry> =>
    this.request("POST", "/api/v1/journal", body);
  deleteJournal = (id: number): Promise<void> => this.request("DELETE", `/api/v1/journal/${id}`);

  // ---- survivors (growth tracker) ---------------------------------------------
  survivors = (query: { min_liquidity_usd?: number; max_age_days?: number; limit?: number }): Promise<{ items: Json[]; total: number }> =>
    this.request("GET", "/api/v1/tokens/survivors", undefined, { query });

  // ---- wallet tracker -------------------------------------------------------
  trackedWallets = (): Promise<{ items: TrackedWallet[]; limit: number }> => this.request("GET", "/api/v1/tracked-wallets");
  addTrackedWallet = (body: TrackedWalletInput): Promise<{ wallet: TrackedWallet }> => this.request("POST", "/api/v1/tracked-wallets", body);
  updateTrackedWallet = (address: string, body: Partial<TrackedWalletInput> & { active?: boolean }): Promise<{ wallet: TrackedWallet }> =>
    this.request("PATCH", `/api/v1/tracked-wallets/${encodeURIComponent(address)}`, body);
  removeTrackedWallet = (address: string): Promise<void> => this.request("DELETE", `/api/v1/tracked-wallets/${encodeURIComponent(address)}`);
  trackedTrades = (limit = 100): Promise<{ items: TrackedTrade[] }> => this.request("GET", "/api/v1/tracked-wallets/trades/recent", undefined, { query: { limit } });
  trackedWalletTrades = (address: string, limit = 200): Promise<{ items: TrackedTrade[] }> =>
    this.request("GET", `/api/v1/tracked-wallets/${encodeURIComponent(address)}/trades`, undefined, { query: { limit } });

  // ---- settings -------------------------------------------------------------
  settings = (): Promise<SettingsView> => this.request("GET", "/api/v1/settings");
  updateSettings = (values: Record<string, unknown>): Promise<{ applied: Record<string, unknown> }> => this.request("PUT", "/api/v1/settings", { values }, { admin: true });
  resetSetting = (key: string): Promise<{ reset: string }> => this.request("DELETE", `/api/v1/settings/${encodeURIComponent(key)}`, undefined, { admin: true });
}
