"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";

export function useApi<T = any>(path: string | null, opts: { poll?: number | ((d: T | undefined) => number | false) } = {}) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError | Error>();
  const [loading, setLoading] = useState(!!path);
  const pollRef = useRef(opts.poll);
  pollRef.current = opts.poll;

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      setData(d);
      setError(undefined);
      return d;
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let alive = true;
    setLoading(!!path);
    const tick = async () => {
      const d = await load();
      const p = typeof pollRef.current === "function" ? pollRef.current(d as T) : pollRef.current;
      if (alive && p) timer = setTimeout(tick, p);
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [load, path]);

  return { data, error, loading, reload: load, setData };
}
