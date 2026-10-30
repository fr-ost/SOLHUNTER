// Settings persistence in chrome.storage.local (falls back to localStorage when running `vite dev` in a tab).

export interface AppSettings {
  apiBaseUrl: string;
  apiKey: string;
  adminKey: string;
}

export const DEFAULT_SETTINGS: AppSettings = { apiBaseUrl: "", apiKey: "", adminKey: "" };
const STORAGE_KEY = "gemfinder.settings";

const hasChromeStorage = (): boolean => typeof chrome !== "undefined" && !!chrome.storage?.local;

export async function loadSettings(): Promise<AppSettings> {
  try {
    if (hasChromeStorage()) {
      const result = await chrome.storage.local.get(STORAGE_KEY);
      const raw = result[STORAGE_KEY] as Partial<AppSettings> | undefined;
      return { ...DEFAULT_SETTINGS, ...(raw ?? {}) };
    }
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? { ...DEFAULT_SETTINGS, ...(JSON.parse(raw) as Partial<AppSettings>) } : DEFAULT_SETTINGS;
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export async function saveSettings(settings: AppSettings): Promise<void> {
  const clean: AppSettings = {
    apiBaseUrl: settings.apiBaseUrl.trim().replace(/\/+$/, ""),
    apiKey: settings.apiKey.trim(),
    adminKey: settings.adminKey.trim(),
  };
  if (hasChromeStorage()) {
    await chrome.storage.local.set({ [STORAGE_KEY]: clean });
  } else {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(clean));
  }
}

export function onSettingsChanged(callback: (settings: AppSettings) => void): () => void {
  if (!hasChromeStorage()) return () => undefined;
  const listener = (changes: Record<string, chrome.storage.StorageChange>, area: string): void => {
    if (area === "local" && changes[STORAGE_KEY]) {
      callback({ ...DEFAULT_SETTINGS, ...((changes[STORAGE_KEY].newValue as Partial<AppSettings>) ?? {}) });
    }
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}

/** Request host permission for a non-railway API origin (must be called from a user gesture). */
export async function ensureHostPermission(baseUrl: string): Promise<boolean> {
  if (typeof chrome === "undefined" || !chrome.permissions) return true;
  let origin: string;
  try {
    const url = new URL(baseUrl);
    if (url.protocol !== "https:") return false;
    origin = `${url.origin}/*`;
  } catch {
    return false;
  }
  if (/\.railway\.app$/i.test(new URL(baseUrl).hostname)) return true;
  const wsOrigin = origin.replace(/^https:/, "wss:");
  const has = await chrome.permissions.contains({ origins: [origin, wsOrigin] });
  if (has) return true;
  return chrome.permissions.request({ origins: [origin, wsOrigin] });
}
