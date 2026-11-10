"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button, Card, inputCls, useToast } from "./ui";

type Value = string | number | boolean | null | Value[] | { [k: string]: Value };

/** Renders form fields for a JSON setting (numbers, booleans, strings, string lists, nested objects). */
export default function SettingEditor({ name, title, value, onSaved, hints = {} }: {
  name: string; title: string; value: Value; onSaved?: () => void; hints?: Record<string, string>;
}) {
  const [draft, setDraft] = useState<Value>(value);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  useEffect(() => setDraft(value), [value]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(value);

  const save = async () => {
    setBusy(true);
    try {
      await api(`/settings/${name}`, { method: "PUT", json: draft });
      toast("ok", `${title} saved`);
      onSaved?.();
    } catch (e) {
      toast("err", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={title} actions={
      <>
        {dirty && <Button onClick={() => setDraft(value)}>Reset</Button>}
        <Button variant="primary" onClick={save} disabled={!dirty || busy}>{busy ? "Saving…" : "Save"}</Button>
      </>
    }>
      <Node path={[]} value={draft} onChange={setDraft} hints={hints} />
    </Card>
  );
}

function setIn(obj: Value, path: (string | number)[], v: Value): Value {
  if (!path.length) return v;
  const [k, ...rest] = path;
  if (Array.isArray(obj)) {
    const copy = [...obj];
    copy[k as number] = setIn(copy[k as number], rest, v);
    return copy;
  }
  const o = (obj ?? {}) as Record<string, Value>;
  return { ...o, [k]: setIn(o[k as string], rest, v) };
}

function Node({ path, value, onChange, hints }: { path: (string | number)[]; value: Value; onChange: (v: Value) => void; hints: Record<string, string> }) {
  const key = path.join(".");
  const label = String(path[path.length - 1] ?? "").replace(/_/g, " ");
  const update = (v: Value) => onChange(v);

  if (value && typeof value === "object" && !Array.isArray(value)) {
    return (
      <div className={path.length ? "mt-2 rounded-xl border border-white/[0.06] p-3" : ""}>
        {path.length > 0 && <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-zinc-400">{label}</div>}
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(value).map(([k, v]) => (
            <div key={k} className={v && typeof v === "object" && !Array.isArray(v) ? "sm:col-span-2" : ""}>
              <Node path={[...path, k]} value={v} hints={hints}
                onChange={(nv) => update({ ...(value as Record<string, Value>), [k]: nv })} />
            </div>
          ))}
        </div>
      </div>
    );
  }
  const hint = hints[key];
  if (typeof value === "boolean") {
    return (
      <label className="flex items-center gap-3 rounded-xl border border-white/[0.06] px-3 py-2.5">
        <input type="checkbox" checked={value} onChange={(e) => update(e.target.checked)} className="h-4 w-4 accent-pink-500" />
        <span className="text-sm capitalize text-zinc-200">{label}</span>
      </label>
    );
  }
  if (Array.isArray(value) && value.every((x) => typeof x === "string")) {
    return (
      <Labeled label={label} hint={hint ?? "comma separated"}>
        <input className={inputCls} value={(value as string[]).join(", ")}
          onChange={(e) => update(e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
      </Labeled>
    );
  }
  if (typeof value === "number") {
    return (
      <Labeled label={label} hint={hint}>
        <input className={inputCls} type="number" step="any" value={value}
          onChange={(e) => update(e.target.value === "" ? 0 : Number(e.target.value))} />
      </Labeled>
    );
  }
  return (
    <Labeled label={label} hint={hint}>
      <input className={inputCls} value={value === null ? "" : String(value)} onChange={(e) => update(e.target.value)} />
    </Labeled>
  );
}

function Labeled({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium capitalize text-zinc-400">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-zinc-500">{hint}</span>}
    </label>
  );
}

export { setIn };
