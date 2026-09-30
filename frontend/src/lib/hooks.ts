import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Run an async loader whenever `deps` change; keeps the previous result while reloading. */
export function useAsync<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[], enabled = true): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    load(ctrl.signal)
      .then((d) => !ctrl.signal.aborted && setData(d))
      .catch((e: Error) => !ctrl.signal.aborted && setError(e.message))
      .finally(() => !ctrl.signal.aborted && setLoading(false));
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

/** Width of an element, updated on resize. */
export function useWidth<E extends HTMLElement>() {
  const ref = useRef<E>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    setWidth(node.clientWidth);
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(node);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

type Theme = "light" | "dark";

/** Light/dark toggle stored in localStorage; unset follows the OS. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme | null>(() => (document.documentElement.dataset.theme as Theme) || null);
  const toggle = useCallback(() => {
    const current = theme ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next: Theme = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("spd-theme", next);
    } catch {
      /* storage blocked */
    }
    setTheme(next);
  }, [theme]);
  return toggle;
}

/** Current "#/route" path, e.g. "/reports"; falls back to `fallback` when the hash is empty or unknown. */
export function useHashRoute<R extends string>(routes: readonly R[], fallback: R): R {
  const read = () => {
    const path = window.location.hash.replace(/^#/, "") as R;
    return routes.includes(path) ? path : fallback;
  };
  const [route, setRoute] = useState<R>(read);
  useEffect(() => {
    const onHash = () => setRoute(read());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return route;
}
