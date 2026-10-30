import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiClient } from "../services/api";
import { DEFAULT_SETTINGS, loadSettings, onSettingsChanged, saveSettings, type AppSettings } from "../services/storage";

interface SettingsContextValue {
  settings: AppSettings;
  loaded: boolean;
  api: ApiClient;
  update: (next: AppSettings) => Promise<void>;
}

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }): JSX.Element {
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    void loadSettings().then((s) => {
      if (active) {
        setSettings(s);
        setLoaded(true);
      }
    });
    const off = onSettingsChanged((s) => setSettings(s));
    return () => {
      active = false;
      off();
    };
  }, []);

  const update = useCallback(async (next: AppSettings) => {
    await saveSettings(next);
    setSettings({ ...next, apiBaseUrl: next.apiBaseUrl.trim().replace(/\/+$/, ""), apiKey: next.apiKey.trim(), adminKey: next.adminKey.trim() });
  }, []);

  const api = useMemo(() => new ApiClient(settings), [settings]);
  const value = useMemo(() => ({ settings, loaded, api, update }), [settings, loaded, api, update]);
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
