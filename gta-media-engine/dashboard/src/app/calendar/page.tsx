"use client";

import { fmt, useApi } from "@/lib/api";
import { Badge, Card, ErrorNote, PageHeader } from "@/components/ui";

type Slot = { date: string; window: string; category: string; job_id: number | null; title: string | null; status: string; scheduled_for: string | null };

export default function CalendarPage() {
  const { data, error } = useApi<Slot[]>("/calendar?days=14", 30000);
  const days = [...new Set((data ?? []).map((s) => s.date))];
  return (
    <>
      <PageHeader title="Content calendar" subtitle="Ready videos fill the next slots; remaining slots show the category the strategist will balance toward, from the target mix." />
      <ErrorNote error={error} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {days.map((d) => (
          <Card key={d} title={new Date(d + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}>
            <ul className="space-y-2">
              {data!.filter((s) => s.date === d).map((s, i) => (
                <li key={i} className={`rounded-xl border px-3 py-2 text-sm ${s.job_id ? "border-pink-400/30 bg-pink-500/[0.06]" : "border-white/[0.06]"}`}>
                  <div className="flex items-center justify-between gap-2"><span className="text-xs text-zinc-500">{s.window}</span><Badge>{s.status}</Badge></div>
                  <div className="mt-1 text-zinc-200">{s.title ?? <span className="text-zinc-500">{fmt.label(s.category)}</span>}</div>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </>
  );
}
