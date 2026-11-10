"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export const BASE = "/api/engine";

/** Backend file URLs are absolute API paths ("/api/files/..."); route them through the proxy. */
export const media = (url: string) => BASE + url.replace(/^\/api(?=\/)/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? JSON.stringify(d)).join("; ");
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; failing?: string[] };
    return [d.message, d.failing?.join(", ")].filter(Boolean).join(": ") || JSON.stringify(detail);
  }
  return "request failed";
}

export async function api<T = unknown>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(BASE + path, {
    ...rest,
    headers: json !== undefined ? { "content-type": "application/json", ...(rest.headers ?? {}) } : rest.headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
    cache: "no-store",
  });
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) throw new ApiError(res.status, describe((data as { detail?: unknown })?.detail ?? text));
  return data as T;
}

function safeJson(text: string) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Fetch + optional polling. `reload` refetches; data stays visible while refreshing. */
export function useApi<T>(path: string | null, pollMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const alive = useRef(true);

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      const d = await api<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    reload();
    if (!pollMs) return () => void (alive.current = false);
    const id = setInterval(reload, pollMs);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [reload, pollMs]);

  return { data, error, loading, reload };
}

export const fmt = {
  num: (n: number | null | undefined) => (n === null || n === undefined ? "—" : Intl.NumberFormat("en", { notation: "compact" }).format(n)),
  usd: (n: number | null | undefined) => (n === null || n === undefined ? "—" : `$${n.toFixed(n < 1 ? 4 : 2)}`),
  pct: (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${(n * 100).toFixed(2)}%`),
  date: (s: string | null | undefined) => (s ? new Date(s).toLocaleString() : "—"),
  secs: (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${n.toFixed(1)}s`),
  label: (s: string | null | undefined) => (s ? s.replace(/^[A-E]_/, "").replace(/_/g, " ") : "—"),
};
