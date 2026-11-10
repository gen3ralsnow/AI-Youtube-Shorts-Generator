"use client";

import { useState } from "react";
import { media, fmt, useApi } from "@/lib/api";
import JobDrawer from "@/components/JobDrawer";
import { Badge, Card, Empty, ErrorNote, PageHeader } from "@/components/ui";

type Render = { id: number; job_id: number; profile: string; url: string; width: number; height: number; fps: number; duration: number; size_bytes: number; qc_passed: boolean; qc: { passed?: boolean; content_blockers?: string[] }; created_at: string };

export default function ExportsPage() {
  const { data, error, reload } = useApi<Render[]>("/renders", 20000);
  const [open, setOpen] = useState<number | null>(null);
  return (
    <>
      <PageHeader title="Video editor & exports" subtitle="Platform-specific exports (1080×1920, H.264/AAC) with their automated QC result. Open one to inspect checks, edit captions or regenerate." />
      <ErrorNote error={error} />
      {data?.length ? (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
          {data.map((r) => (
            <div key={r.id} className="card overflow-hidden rounded-2xl">
              <video src={media(r.url)} controls preload="metadata" className="aspect-[9/16] w-full bg-black" />
              <div className="space-y-1.5 p-3">
                <div className="flex flex-wrap gap-1"><Badge tone="muted">{r.profile}</Badge><Badge tone={r.qc?.passed ? "good" : r.qc_passed ? "warn" : "bad"}>{r.qc?.passed ? "publishable" : r.qc_passed ? "QC ok · blocked" : "QC failed"}</Badge></div>
                <p className="text-xs text-zinc-500">{r.width}×{r.height} · {r.fps}fps · {fmt.secs(r.duration)} · {(r.size_bytes / 1e6).toFixed(1)}MB</p>
                <button onClick={() => setOpen(r.job_id)} className="text-xs text-pink-300 hover:underline">Job #{r.job_id} →</button>
              </div>
            </div>
          ))}
        </div>
      ) : <Card><Empty>No renders yet.</Empty></Card>}
      <JobDrawer jobId={open} onClose={() => setOpen(null)} onChanged={reload} />
    </>
  );
}
