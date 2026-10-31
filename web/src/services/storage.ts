// Settings persistence in localStorage. The dashboard is served by the SolHunter API itself, so the
// API base URL defaults to the page origin; an override exists for `vite dev` or a split deployment.

export interface AppSettings {
  apiBaseUrl: string; // "" means same origin
  apiKey: string;
  adminKey: string;
}

export const DEFAULT_SETTINGS: AppSettings = { apiBaseUrl: "", apiKey: "", adminKey: "" };
const STORAGE_KEY = "solhunter.settings";

export function defaultBaseUrl(): string {
  return typeof window !== "undefined" ? window.location.origin : "";
}

/** Effective base URL: explicit override or the page origin. */
export function effectiveBaseUrl(settings: AppSettings): string {
  return (settings.apiBaseUrl || defaultBaseUrl()).replace(/\/+$/, "");
}

export function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? { ...DEFAULT_SETTINGS, ...(JSON.parse(raw) as Partial<AppSettings>) } : DEFAULT_SETTINGS;
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function normalizeSettings(settings: AppSettings): AppSettings {
  return {
    apiBaseUrl: settings.apiBaseUrl.trim().replace(/\/+$/, ""),
    apiKey: settings.apiKey.trim(),
    adminKey: settings.adminKey.trim(),
  };
}

export function saveSettings(settings: AppSettings): AppSettings {
  const clean = normalizeSettings(settings);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(clean));
  } catch {
    /* private mode / blocked storage: keep in memory only */
  }
  return clean;
}

export function clearSettings(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}
