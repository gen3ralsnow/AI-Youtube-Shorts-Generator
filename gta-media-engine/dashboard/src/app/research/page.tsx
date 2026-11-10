"use client";

import { useState } from "react";
import { api, fmt, useApi } from "@/lib/api";
import { ActionButton, Badge, Button, Card, ErrorNote, Field, inputCls, PageHeader, Table, useToast } from "@/components/ui";

type Source = { name: string; configured: boolean; missing: string[] };
type Run = { id: number; agent: string; job_id: number | null; status: string; attempts: number; duration_ms: number; error: string | null; at: string; summary: Record<string, unknown> };

export default function ResearchPage() {
  const { data: sources, error } = useApi<Source[]>("/research/sources");
  const { data: runs, reload } = useApi<Run[]>("/agents/runs?limit=60", 10000);
  return (
    <>
      <PageHeader title="AI research" subtitle="Official APIs and syndication feeds only: YouTube Data API, Reddit OAuth API, RSS (robots.txt honoured) and a trends provider. No scraping, no private data.">
        <ActionButton path="/research/run" label="Run research + scoring" variant="primary" done="Research started" onDone={reload} />
      </PageHeader>
      <ErrorNote error={error} />
      <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
        <div className="space-y-6">
          <Card title="Research sources">
            <ul className="space-y-2">
              {sources?.map((s) => (
                <li key={s.name} className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] px-3 py-2.5 text-sm">
                  <span className="text-zinc-200">{s.name}</span>
                  {s.configured ? <Badge tone="good">connected</Badge> : <span className="text-right text-xs text-amber-300">needs {s.missing.join(", ")}</span>}
                </li>
              ))}
            </ul>
          </Card>
          <ManualTopic />
        </div>
        <Card title="Agent activity">
          <Table head={["Agent", "Status", "Job", "Time", "When"]}>
            {runs?.map((r) => (
              <tr key={r.id} title={r.error ?? JSON.stringify(r.summary)}>
                <td className="py-2 pr-4 text-zinc-200">{r.agent.replace(/_/g, " ")}</td>
                <td className="py-2 pr-4"><Badge>{r.status}</Badge></td>
                <td className="py-2 pr-4 text-zinc-500">{r.job_id ?? "—"}</td>
                <td className="py-2 pr-4 text-zinc-500">{r.duration_ms} ms</td>
                <td className="py-2 text-xs text-zinc-500">{fmt.date(r.at)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    </>
  );
}

function ManualTopic() {
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("A_discoveries");
  const [url, setUrl] = useState("");
  const [sourceTitle, setSourceTitle] = useState("");
  const [summary, setSummary] = useState("");
  const submit = async () => {
    try {
      const t = await api<{ id: number; classification: string; category: string }>("/topics", { method: "POST", json: { title, category, sources: [{ url, title: sourceTitle, summary }] } });
      toast("ok", `Topic #${t.id} created as ${t.classification.replace(/_/g, " ")} (${t.category})`);
      setTitle(""); setUrl(""); setSourceTitle(""); setSummary("");
    } catch (e) { toast("err", (e as Error).message); }
  };
  return (
    <Card title="Submit a topic with a source">
      <p className="mb-3 text-xs text-zinc-500">Verification status is derived from the source, not chosen here: a rumour cannot be filed as news.</p>
      <div className="space-y-3">
        <Field label="Topic title"><input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label="Category">
          <select className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}>
            {["A_discoveries", "B_news", "C_theories", "D_entertainment", "E_gta6_vs_gtav"].map((c) => <option key={c} value={c}>{fmt.label(c)}</option>)}
          </select>
        </Field>
        <Field label="Source URL"><input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.rockstargames.com/newswire/…" /></Field>
        <Field label="Source headline"><input className={inputCls} value={sourceTitle} onChange={(e) => setSourceTitle(e.target.value)} /></Field>
        <Field label="Key facts from the source" hint="Scripts may only use facts present here."><textarea className={`${inputCls} min-h-24`} value={summary} onChange={(e) => setSummary(e.target.value)} /></Field>
        <Button variant="primary" onClick={submit} disabled={!title || !url || !sourceTitle}>Create topic</Button>
      </div>
    </Card>
  );
}
