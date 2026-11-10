"use client";

import { createContext, ReactNode, useCallback, useContext, useState } from "react";
import { api } from "@/lib/api";

/* ------------------------------------------------------------------ toasts */

type Toast = { id: number; kind: "ok" | "err"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, kind, text }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === "err" ? 9000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex w-[min(420px,calc(100vw-2rem))] flex-col gap-2" role="status">
        {items.map((t) => (
          <div key={t.id} className={`toast-in rounded-xl border px-4 py-3 text-sm shadow-2xl backdrop-blur ${t.kind === "ok" ? "border-emerald-400/30 bg-emerald-500/15 text-emerald-100" : "border-rose-400/40 bg-rose-500/15 text-rose-100"}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/* ------------------------------------------------------------------ layout */

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="font-display text-2xl font-bold tracking-tight text-white sm:text-3xl">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-sm text-zinc-400">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

export function Card({ title, children, className = "", actions }: { title?: string; children: ReactNode; className?: string; actions?: ReactNode }) {
  return (
    <section className={`card min-w-0 rounded-2xl p-5 ${className}`}>
      {(title || actions) && (
        <div className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-zinc-400">{title}</h2>}
          <div className="flex flex-wrap gap-2">{actions}</div>
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, accent = false }: { label: string; value: ReactNode; hint?: string; accent?: boolean }) {
  return (
    <div className="card min-w-0 rounded-2xl p-4">
      <div className="text-xs uppercase tracking-[0.14em] text-zinc-500">{label}</div>
      <div className={`mt-2 font-display text-2xl font-bold ${accent ? "text-gradient" : "text-white"}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-zinc-500">{hint}</div>}
    </div>
  );
}

const BADGE: Record<string, string> = {
  good: "bg-emerald-500/15 text-emerald-300 ring-emerald-400/30",
  warn: "bg-amber-500/15 text-amber-300 ring-amber-400/30",
  bad: "bg-rose-500/15 text-rose-300 ring-rose-400/30",
  info: "bg-cyan-500/15 text-cyan-300 ring-cyan-400/30",
  muted: "bg-zinc-500/15 text-zinc-300 ring-zinc-400/20",
  brand: "bg-pink-500/15 text-pink-300 ring-pink-400/30",
};

const STATUS_TONE: Record<string, keyof typeof BADGE> = {
  succeeded: "good", ready: "good", published: "good", verified: "good", connected: "good", passed: "good",
  officially_confirmed: "good", drafted: "info", processing: "info", running: "info", uploading: "info",
  queued: "muted", new: "muted", pending: "muted", selected: "info", dry_run: "muted", planned: "muted",
  credibly_reported: "info", community_theory: "warn", unverified_rumour: "warn", blocked: "warn",
  authorizing: "warn", expired: "warn", superseded: "muted", failed: "bad", false_or_debunked: "bad",
  rejected: "bad", error: "bad", disconnected: "muted", duplicate: "muted", cancelled: "muted",
  urgent: "bad", high: "warn", normal: "muted", low: "muted", test: "warn", autonomous: "brand",
};

export function Badge({ children, tone }: { children: ReactNode; tone?: keyof typeof BADGE }) {
  const t = tone ?? STATUS_TONE[String(children)] ?? "muted";
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${BADGE[t]}`}>
      {String(children).replace(/_/g, " ")}
    </span>
  );
}

export function Button({ children, onClick, variant = "ghost", disabled, type = "button", className = "" }: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "ghost" | "danger"; disabled?: boolean; type?: "button" | "submit"; className?: string;
}) {
  const styles = {
    primary: "btn-primary text-white",
    ghost: "border border-white/10 bg-white/[0.04] text-zinc-200 hover:bg-white/[0.08] hover:border-white/20",
    danger: "border border-rose-500/40 bg-rose-500/10 text-rose-200 hover:bg-rose-500/20",
  }[variant];
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-xl px-3.5 py-2 text-sm font-medium transition duration-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 ${styles} ${className}`}>
      {children}
    </button>
  );
}

/** Button that calls the API, reports the result, and refreshes the caller's data. */
export function ActionButton({ path, method = "POST", body, label, done, onDone, variant = "ghost", confirm, disabled }: {
  path: string; method?: string; body?: unknown; label: ReactNode; done?: string; onDone?: (data: unknown) => void;
  variant?: "primary" | "ghost" | "danger"; confirm?: string; disabled?: boolean;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (confirm && !window.confirm(confirm)) return;
    setBusy(true);
    try {
      const data = await api(path, { method, json: body });
      toast("ok", done ?? "Done");
      onDone?.(data);
    } catch (e) {
      toast("err", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button onClick={run} variant={variant} disabled={busy || disabled}>
      {busy && <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" />}
      {label}
    </Button>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center text-sm text-zinc-500">{children}</div>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="mb-4 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">{error}</div>;
}

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="-mx-5 overflow-x-auto px-5">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead>
          <tr className="border-b border-white/10 text-[11px] uppercase tracking-[0.12em] text-zinc-500">
            {head.map((h) => <th key={h} className="whitespace-nowrap py-2 pr-4 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.06]">{children}</tbody>
      </table>
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-[0.12em] text-zinc-400">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-zinc-500">{hint}</span>}
    </label>
  );
}

export const inputCls = "w-full rounded-xl border border-white/10 bg-black/40 px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none transition focus:border-pink-400/60 focus:ring-2 focus:ring-pink-500/20";

export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="drawer-in h-full w-full max-w-3xl overflow-y-auto border-l border-white/10 bg-[#0b0b12] p-6" onClick={(e) => e.stopPropagation()}>
        <div className="mb-5 flex items-start justify-between gap-4">
          <h2 className="font-display text-xl font-bold text-white">{title}</h2>
          <Button onClick={onClose}>Close</Button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Json({ value }: { value: unknown }) {
  return <pre className="max-h-80 overflow-auto rounded-xl bg-black/50 p-3 text-xs text-zinc-300">{JSON.stringify(value, null, 2)}</pre>;
}
