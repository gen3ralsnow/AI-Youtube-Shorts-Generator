"use client";

import { fmt, useApi } from "@/lib/api";
import SettingEditor from "@/components/SettingEditor";
import { ActionButton, Card, Empty, ErrorNote, PageHeader, Stat, Table } from "@/components/ui";

type Costs = {
  today: { estimated_usd: number; actual_usd: number }; month: { estimated_usd: number; actual_usd: number };
  budgets: Record<string, number>; alerts: string[]; processing_paused: boolean; processing_pause_reason: string;
  by_category_month: { category: string; estimated_usd: number; actual_usd: number; calls: number }[];
  recent: { id: number; at: string; category: string; provider: string; units: number; unit: string; estimated_usd: number; actual_usd: number | null; job_id: number | null; note: string }[];
};

export default function CostsPage() {
  const { data: c, error, reload } = useApi<Costs>("/costs", 20000);
  return (
    <>
      <PageHeader title="Costs & usage" subtitle="Each paid call is estimated before it runs and refused if it would break a budget; actual cost is computed from provider-reported usage. Prices are configurable estimates, so reconcile them against invoices." />
      <ErrorNote error={error} />
      {c && (
        <>
          {c.alerts.map((a) => <ErrorNote key={a} error={`Budget alert: ${a}`} />)}
          {c.processing_paused && <div className="mb-4 flex items-center justify-between gap-3 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200"><span>Processing paused: {c.processing_pause_reason}</span><ActionButton path="/control/resume-processing" label="Resume processing" onDone={reload} /></div>}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Today · estimated" value={fmt.usd(c.today.estimated_usd)} hint={`daily cap $${c.budgets.daily_usd}`} />
            <Stat label="Today · actual" value={fmt.usd(c.today.actual_usd)} accent />
            <Stat label="Month · estimated" value={fmt.usd(c.month.estimated_usd)} hint={`monthly cap $${c.budgets.monthly_usd}`} />
            <Stat label="Month · actual" value={fmt.usd(c.month.actual_usd)} accent />
          </div>
          <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_1.4fr]">
            <div className="space-y-6">
              <SettingEditor name="budgets" title="Budgets" value={c.budgets} onSaved={reload} hints={{ alert_ratio: "alert when spend reaches this share of a cap" }} />
              <Card title="This month by category">
                {c.by_category_month.length ? (
                  <Table head={["Category", "Calls", "Estimated", "Actual"]}>{c.by_category_month.map((r) => <tr key={r.category}><td className="py-2 pr-4 text-zinc-200">{r.category.replace(/_/g, " ")}</td><td className="py-2 pr-4 text-zinc-400">{r.calls}</td><td className="py-2 pr-4 text-zinc-300">{fmt.usd(r.estimated_usd)}</td><td className="py-2 text-zinc-300">{fmt.usd(r.actual_usd)}</td></tr>)}</Table>
                ) : <Empty>No usage this month.</Empty>}
              </Card>
            </div>
            <Card title="Recent usage">
              {c.recent.length ? (
                <Table head={["When", "Category", "Provider", "Units", "Estimated", "Actual", "Job"]}>
                  {c.recent.map((r) => <tr key={r.id}><td className="py-2 pr-4 text-xs text-zinc-500">{fmt.date(r.at)}</td><td className="py-2 pr-4 text-zinc-300">{r.category.replace(/_/g, " ")}</td><td className="max-w-[160px] truncate py-2 pr-4 text-zinc-400">{r.provider}</td><td className="py-2 pr-4 text-zinc-400">{fmt.num(r.units)} {r.unit}</td><td className="py-2 pr-4 text-zinc-300">{fmt.usd(r.estimated_usd)}</td><td className="py-2 pr-4 text-zinc-300">{fmt.usd(r.actual_usd)}</td><td className="py-2 text-zinc-500">{r.job_id ?? "—"}</td></tr>)}
                </Table>
              ) : <Empty>No usage recorded.</Empty>}
            </Card>
          </div>
        </>
      )}
    </>
  );
}
