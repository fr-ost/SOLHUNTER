import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { ApiClient } from "../services/api";
import { clearSettings, loadSettings, saveSettings, type AppSettings } from "../services/storage";

interface SettingsContextValue {
  settings: AppSettings;
  loaded: boolean;
  api: ApiClient;
  /** true once an API key has been saved (the login screen is shown otherwise) */
  signedIn: boolean;
  update: (next: AppSettings) => Promise<void>;
  signOut: () => void;
}

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }): JSX.Element {
  const [settings, setSettings] = useState<AppSettings>(() => loadSettings());

  const update = useCallback(async (next: AppSettings) => {
    setSettings(saveSettings(next));
  }, []);

  const signOut = useCallback(() => {
    clearSettings();
    setSettings({ apiBaseUrl: settings.apiBaseUrl, apiKey: "", adminKey: "" });
  }, [settings.apiBaseUrl]);

  const api = useMemo(() => new ApiClient(settings), [settings]);
  const value = useMemo(
    () => ({ settings, loaded: true, api, signedIn: Boolean(settings.apiKey), update, signOut }),
    [settings, api, update, signOut],
  );
  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useSettings outside SettingsProvider");
  return ctx;
}

export function useApi(): ApiClient {
  return useSettings().api;
}
