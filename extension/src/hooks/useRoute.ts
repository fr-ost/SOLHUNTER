import { useEffect, useState } from "react";

export interface Route {
  path: string; // e.g. "candidates"
  param: string | null; // e.g. mint or wallet address
  query: URLSearchParams;
}

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [pathPart, queryPart] = raw.split("?");
  const segments = (pathPart ?? "").split("/").filter(Boolean);
  return { path: segments[0] ?? "dashboard", param: segments[1] ? decodeURIComponent(segments[1]) : null, query: new URLSearchParams(queryPart ?? "") };
}

export function navigate(path: string, param?: string): void {
  window.location.hash = param ? `#/${path}/${encodeURIComponent(param)}` : `#/${path}`;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = (): void => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
