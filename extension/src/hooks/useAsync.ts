import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";
import { ApiError } from "../services/api";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  reload: () => void;
  updatedAt: number | null;
}

/** Runs an async loader on mount / when deps change, optionally refreshing on an interval. */
export function useAsync<T>(loader: () => Promise<T>, deps: DependencyList, options: { refreshMs?: number; enabled?: boolean } = {}): AsyncState<T> {
  const { refreshMs, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const generation = useRef(0);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    const gen = ++generation.current;
    let cancelled = false;
    setLoading(true);
    loader()
      .then((result) => {
        if (!cancelled && gen === generation.current) {
          setData(result);
          setError(null);
          setUpdatedAt(Date.now());
        }
      })
      .catch((err: unknown) => {
        if (!cancelled && gen === generation.current) setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (!cancelled && gen === generation.current) setLoading(false);
      });
    let timer: number | null = null;
    if (refreshMs) timer = window.setInterval(() => setTick((t) => t + 1), refreshMs);
    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled, refreshMs]);

  return { data, error, loading, reload, updatedAt };
}

export const errorMessage = (err: ApiError | Error | null): string | null => (err ? (err instanceof ApiError ? err.userMessage : err.message) : null);
