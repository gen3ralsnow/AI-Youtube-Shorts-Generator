"use client";

import { useState } from "react";
import { fmt, useApi } from "@/lib/api";
import JobDrawer from "@/components/JobDrawer";
import { Badge, Card, Empty, ErrorNote, PageHeader } from "@/components/ui";

type Script = { id: number; job_id: number; version: number; title: string; category: string; chosen_hook: string; voiceover_text: string; estimated_seconds: number; confidence: number; generator: string; validation: { passed?: boolean }; created_at: string; sources: unknown[] };

export default function ScriptsPage() {
  const { data, error, reload } = useApi<Script[]>("/scripts", 20000);
  const [open, setOpen] = useState<number | null>(null);
  return (
    <>
      <PageHeader title="Script library" subtitle="Every script version with its hook, fact-check confidence and generator. Rule-based scripts are marked as such." />
      <ErrorNote error={error} />
      {data?.length ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.map((s) => (
            <button key={s.id} onClick={() => setOpen(s.job_id)} className="card rounded-2xl p-5 text-left hover:-translate-y-0.5">
              <div className="mb-2 flex flex-wrap gap-1.5"><Badge tone="muted">{fmt.label(s.category)}</Badge><Badge tone={s.validation?.passed ? "good" : "warn"}>{s.validation?.passed ? "validated" : "unvalidated"}</Badge><Badge tone="info">{`v${s.version}`}</Badge></div>
              <h3 className="font-semibold text-white">{s.title}</h3>
              <p className="mt-2 text-sm text-pink-200">“{s.chosen_hook}”</p>
              <p className="mt-2 line-clamp-3 text-sm text-zinc-400">{s.voiceover_text}</p>
              <p className="mt-3 text-xs text-zinc-500">{fmt.secs(s.estimated_seconds)} · facts {(s.confidence * 100).toFixed(0)}% · {s.sources.length} sources · {s.generator}</p>
            </button>
          ))}
        </div>
      ) : <Card><Empty>No scripts yet.</Empty></Card>}
      <JobDrawer jobId={open} onClose={() => setOpen(null)} onChanged={reload} />
    </>
  );
}
