"use client";

import { fmt, useApi } from "@/lib/api";
import { Badge, Card, Empty, ErrorNote, PageHeader, Table } from "@/components/ui";

type Health = {
  ok: boolean; components: Record<string, { ok: boolean; detail: string }>;
  research_sources: { name: string; configured: boolean; missing: string[] }[];
  platform_apps: Record<string, string[]>; budget_alerts: string[];
  recent_agent_failures: { agent: string; job_id: number | null; status: string; error: string | null; at: string }[];
};
type Audit = { id: number; at: string; actor: string; action: string; target: string; details: Record<string, unknown> };

export default function HealthPage() {
  const { data: h, error } = useApi<Health>("/system/health", 15000);
  const { data: audit } = useApi<Audit[]>("/audit?limit=100", 30000);
  return (
    <>
      <PageHeader title="System health" subtitle="Components, missing credentials, recent agent failures and the audit log of every administrative action." />
      <ErrorNote error={error} />
      {h && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Components" actions={<Badge tone={h.ok ? "good" : "bad"}>{h.ok ? "healthy" : "degraded"}</Badge>}>
            <ul className="space-y-2">{Object.entries(h.components).map(([k, v]) => <li key={k} className="flex items-center justify-between gap-3 text-sm"><span className="capitalize text-zinc-200">{k.replace(/_/g, " ")}</span><span className="flex items-center gap-2 text-right"><span className="text-xs text-zinc-500">{v.detail}</span><Badge tone={v.ok ? "good" : "bad"}>{v.ok ? "ok" : "issue"}</Badge></span></li>)}</ul>
          </Card>
          <Card title="Integrations awaiting credentials">
            <ul className="space-y-2 text-sm">
              {h.research_sources.map((s) => <li key={s.name} className="flex justify-between gap-3"><span className="text-zinc-200">{s.name}</span>{s.configured ? <Badge tone="good">configured</Badge> : <code className="text-xs text-amber-300">{s.missing.join(", ")}</code>}</li>)}
              {Object.entries(h.platform_apps).map(([p, missing]) => <li key={p} className="flex justify-between gap-3"><span className="text-zinc-200">{p} app</span>{missing.length ? <code className="text-xs text-amber-300">{missing.join(", ")}</code> : <Badge tone="good">configured</Badge>}</li>)}
            </ul>
          </Card>
          <Card title="Recent agent failures" className="lg:col-span-2">
            {h.recent_agent_failures.length ? (
              <Table head={["Agent", "Status", "Job", "Error", "When"]}>{h.recent_agent_failures.map((f, i) => <tr key={i}><td className="py-2 pr-4 text-zinc-200">{f.agent}</td><td className="py-2 pr-4"><Badge>{f.status}</Badge></td><td className="py-2 pr-4 text-zinc-500">{f.job_id ?? "—"}</td><td className="max-w-[420px] truncate py-2 pr-4 text-xs text-zinc-400" title={f.error ?? ""}>{f.error}</td><td className="py-2 text-xs text-zinc-500">{fmt.date(f.at)}</td></tr>)}</Table>
            ) : <Empty>No recent failures.</Empty>}
          </Card>
        </div>
      )}
      <Card title="Audit log" className="mt-6">
        {audit?.length ? (
          <Table head={["When", "Actor", "Action", "Target"]}>{audit.map((a) => <tr key={a.id} title={JSON.stringify(a.details)}><td className="py-2 pr-4 text-xs text-zinc-500">{fmt.date(a.at)}</td><td className="py-2 pr-4 text-zinc-400">{a.actor}</td><td className="py-2 pr-4 text-zinc-200">{a.action}</td><td className="py-2 text-zinc-400">{a.target}</td></tr>)}</Table>
        ) : <Empty>No administrative actions yet.</Empty>}
      </Card>
    </>
  );
}
