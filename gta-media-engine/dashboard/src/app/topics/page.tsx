"use client";

import { useState } from "react";
import { fmt, useApi } from "@/lib/api";
import { ActionButton, Badge, Card, Empty, ErrorNote, Json, Modal, PageHeader, Table } from "@/components/ui";

type Topic = { id: number; title: string; explanation: string; category: string; classification: string; score: number; urgency: string; recommended_length: string; status: string; hook: string; angle: string; source_count: number; created_at: string; score_breakdown: Record<string, unknown>; sources?: { id: number; title: string; url: string; credibility: string; connector: string; engagement: Record<string, number | null> }[] };

export default function TopicsPage() {
  const [status, setStatus] = useState("new");
  const [minScore, setMinScore] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const { data, error, reload } = useApi<Topic[]>(`/topics?min_score=${minScore}${status ? `&status=${status}` : ""}`, 15000);
  return (
    <>
      <PageHeader title="Trending topics" subtitle="Scored 0–100 opportunities from research. Missing signals (no trends provider, no saturation search) are excluded from the score, never counted as zero.">
        <ActionButton path="/research/run" label="Run research now" variant="primary" done="Research started" onDone={reload} />
      </PageHeader>
      <ErrorNote error={error} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {["new", "selected", "used", "rejected", "duplicate", ""].map((s) => (
          <button key={s || "all"} onClick={() => setStatus(s)} className={`rounded-full px-3 py-1 text-xs capitalize ${status === s ? "bg-pink-500/20 text-pink-200 ring-1 ring-pink-400/40" : "bg-white/[0.04] text-zinc-400"}`}>{s || "all"}</button>
        ))}
        <label className="ml-auto flex items-center gap-2 text-xs text-zinc-400">min score
          <input type="range" min={0} max={100} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} className="accent-pink-500" /><span className="w-6 text-zinc-200">{minScore}</span>
        </label>
      </div>
      <Card>
        {data?.length ? (
          <Table head={["Score", "Topic", "Verification", "Category", "Urgency", "Length", "Sources", ""]}>
            {data.map((t) => (
              <tr key={t.id} className="hover:bg-white/[0.02]">
                <td className="py-2.5 pr-4"><div className="flex items-center gap-2"><span className="w-8 font-display font-bold text-white">{t.score.toFixed(0)}</span><div className="h-1.5 w-16 rounded-full bg-white/10"><div className="bar" style={{ width: `${t.score}%`, height: 6 }} /></div></div></td>
                <td className="max-w-[360px] py-2.5 pr-4"><button onClick={() => setOpen(t.id)} className="truncate text-left text-zinc-100 hover:text-pink-200">{t.title}</button><div className="truncate text-xs text-zinc-500">{t.hook}</div></td>
                <td className="py-2.5 pr-4"><Badge>{t.classification}</Badge></td>
                <td className="py-2.5 pr-4 text-zinc-400">{fmt.label(t.category)}</td>
                <td className="py-2.5 pr-4"><Badge>{t.urgency}</Badge></td>
                <td className="py-2.5 pr-4 text-zinc-400">{t.recommended_length}s</td>
                <td className="py-2.5 pr-4 text-zinc-400">{t.source_count}</td>
                <td className="whitespace-nowrap py-2.5 text-right"><div className="flex justify-end gap-1.5">
                  {t.status === "new" && <ActionButton path={`/topics/${t.id}/produce`} label="Produce" variant="primary" done="Job created" onDone={reload} />}
                  {t.status === "new" && <ActionButton path={`/topics/${t.id}/reject`} label="Reject" onDone={reload} />}
                </div></td>
              </tr>
            ))}
          </Table>
        ) : <Empty>No topics match. Connect research sources (System health shows what is missing) and run research.</Empty>}
      </Card>
      <TopicModal id={open} onClose={() => setOpen(null)} />
    </>
  );
}

function TopicModal({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { data: t } = useApi<Topic>(id ? `/topics/${id}` : null);
  return (
    <Modal open={id !== null} onClose={onClose} title={t?.title ?? "Topic"}>
      {t && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2"><Badge>{t.classification}</Badge><Badge tone="muted">{fmt.label(t.category)}</Badge><Badge>{t.urgency}</Badge><Badge tone="info">{`score ${t.score}`}</Badge></div>
          <Card title="Explanation"><p className="text-sm text-zinc-300">{t.explanation || "—"}</p></Card>
          <Card title="Suggested angle & hook"><p className="text-sm text-zinc-300">{t.angle}</p><p className="mt-2 font-semibold text-white">“{t.hook}”</p><p className="mt-1 text-xs text-zinc-500">Recommended length {t.recommended_length}s</p></Card>
          <Card title="Source references">
            <ul className="space-y-2 text-sm">{t.sources?.map((s) => <li key={s.id}><Badge>{s.credibility}</Badge> <a href={s.url} target="_blank" rel="noreferrer noopener" className="text-cyan-300 hover:underline">{s.title}</a> <span className="text-xs text-zinc-500">({s.connector})</span></li>)}</ul>
          </Card>
          <Card title="Score breakdown"><Json value={t.score_breakdown} /></Card>
        </div>
      )}
    </Modal>
  );
}
