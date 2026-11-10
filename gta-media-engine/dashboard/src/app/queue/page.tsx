"use client";

import { useState } from "react";
import { fmt, useApi } from "@/lib/api";
import JobDrawer from "@/components/JobDrawer";
import { ActionButton, Badge, Card, Empty, ErrorNote, PageHeader, Table } from "@/components/ui";

type Job = { id: number; status: string; category: string; current_stage: string | null; topic_title: string | null; title_override: string | null; classification: string | null; error: string | null; blocked_reason: string | null; created_at: string; updated_at: string; estimated_cost_usd: number };
const FILTERS = ["", "queued", "running", "ready", "blocked", "failed", "published", "cancelled"];

export default function QueuePage() {
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const { data, error, reload } = useApi<Job[]>(`/jobs${filter ? `?status=${filter}` : ""}`, 5000);
  return (
    <>
      <PageHeader title="Live production queue" subtitle="Every job and the stage it is in. Open a job to preview, edit captions or titles, regenerate, retry or delete.">
        <ActionButton path="/strategy/plan" label="Plan new jobs" variant="primary" done="Planning started" onDone={reload} />
      </PageHeader>
      <ErrorNote error={error} />
      <div className="mb-4 flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button key={f || "all"} onClick={() => setFilter(f)} className={`rounded-full px-3 py-1 text-xs capitalize transition ${filter === f ? "bg-pink-500/20 text-pink-200 ring-1 ring-pink-400/40" : "bg-white/[0.04] text-zinc-400 hover:text-zinc-200"}`}>{f || "all"}</button>
        ))}
      </div>
      <Card>
        {data?.length ? (
          <Table head={["#", "Title", "Category", "Status", "Stage", "Updated", "Est. cost", ""]}>
            {data.map((j) => (
              <tr key={j.id} className="cursor-pointer hover:bg-white/[0.02]" onClick={() => setOpen(j.id)}>
                <td className="py-2.5 pr-4 text-zinc-500">{j.id}</td>
                <td className="max-w-[340px] py-2.5 pr-4"><div className="truncate text-zinc-100">{j.title_override ?? j.topic_title ?? "—"}</div>{(j.blocked_reason || j.error) && <div className="truncate text-xs text-amber-300/80">{j.blocked_reason ?? j.error}</div>}</td>
                <td className="py-2.5 pr-4 text-zinc-400">{fmt.label(j.category)}</td>
                <td className="py-2.5 pr-4"><Badge>{j.status}</Badge></td>
                <td className="py-2.5 pr-4 text-zinc-400">{fmt.label(j.current_stage)}</td>
                <td className="py-2.5 pr-4 text-xs text-zinc-500">{fmt.date(j.updated_at)}</td>
                <td className="py-2.5 pr-4 text-zinc-400">{fmt.usd(j.estimated_cost_usd)}</td>
                <td className="py-2.5 text-right text-xs text-pink-300">Open →</td>
              </tr>
            ))}
          </Table>
        ) : <Empty>No jobs{filter ? ` with status “${filter}”` : ""}. Plan jobs from scored topics, or produce a topic directly.</Empty>}
      </Card>
      <JobDrawer jobId={open} onClose={() => setOpen(null)} onChanged={reload} />
    </>
  );
}
