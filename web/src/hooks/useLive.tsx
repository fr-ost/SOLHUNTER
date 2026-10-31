import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { LiveClient, type LiveStatus } from "../services/ws";
import type { LiveEvent } from "../types/api";
import { useSettings } from "./useSettings";

interface LiveContextValue {
  status: LiveStatus;
  client: LiveClient;
  lastEvent: LiveEvent | null;
  /** monotonically increasing counter of token.evaluated / gpt.analysis events - cheap trigger for refetches */
  version: number;
}

const LiveContext = createContext<LiveContextValue | null>(null);

export function LiveProvider({ children }: { children: ReactNode }): JSX.Element {
  const { api, loaded } = useSettings();
  const clientRef = useRef(new LiveClient());
  const [status, setStatus] = useState<LiveStatus>("disabled");
  const [lastEvent, setLastEvent] = useState<LiveEvent | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const client = clientRef.current;
    const offStatus = client.onStatus(setStatus);
    const offEvents = client.subscribe((event) => {
      setLastEvent(event);
      if (event.type === "token.evaluated" || event.type === "gpt.analysis" || event.type === "token.discovered") {
        setVersion((v) => v + 1);
      }
    });
    return () => {
      offStatus();
      offEvents();
    };
  }, []);

  useEffect(() => {
    if (!loaded) return;
    clientRef.current.start(api.configured ? api.wsUrl() : null);
    return () => clientRef.current.stop();
  }, [api, loaded]);

  const value = useMemo(() => ({ status, client: clientRef.current, lastEvent, version }), [status, lastEvent, version]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveContextValue {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive outside LiveProvider");
  return ctx;
}

/** Debounced version signal: changes at most once per `ms` while live events stream in. */
export function useDebouncedLiveVersion(ms = 4000): number {
  const { version } = useLive();
  const [debounced, setDebounced] = useState(version);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(version), ms);
    return () => window.clearTimeout(t);
  }, [version, ms]);
  return debounced;
}
