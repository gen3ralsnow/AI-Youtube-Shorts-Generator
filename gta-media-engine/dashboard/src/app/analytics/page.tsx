"use client";

import { fmt, useApi } from "@/lib/api";
import { ActionButton, Badge, Card, Empty, ErrorNote, PageHeader, Stat, Table } from "@/components/ui";

type Group = { group: string; n: number; median: number; sufficient_sample: boolean; ci90: number[] | null; relative_engagement: number | null };
type Answer = { answer: string | null; reason?: string; median?: number; n?: number };
type Summary = { videos_with_metrics: number; min_sample_size: number; missing_metric_counts: Record<string, number>; by: Record<string, Group[]>; answers: Record<string, Answer | unknown> & { next_focus: string; oversaturated_topics: { keyword: string; videos: number }[] } };
type Video = { publication_id: number; title: string; platform: string; category: string; views: number | null; likes: number | null; comments: number | null; shares: number | null; saves: number | null; engagement_rate: number | null; avg_watch_seconds: number | null; followers_gained: number | null };
type Report = { id: number; period_start: string; period_end: string; recommendations: string[] };

const QUESTIONS: [string, string][] = [["best_topics", "Best topics"], ["best_hooks", "Strongest hooks"], ["best_durations", "Best durations"], ["most_discussion", "Most discussion"], ["subscriber_drivers", "Subscriber drivers"], ["best_publish_time", "Best publish time"]];

export default function AnalyticsPage() {
  const { data: s, error, reload } = useApi<Summary>("/analytics/summary", 60000);
  const { data: videos } = useApi<Video[]>("/analytics/videos", 60000);
  const { data: reports, reload: reloadReports } = useApi<Report[]>("/analytics/reports");
  const max = Math.max(1, ...(s?.by.category ?? []).map((g) => g.median));
  return (
    <>
      <PageHeader title="Performance analytics" subtitle={`Real platform metrics only. Groups with fewer than ${s?.min_sample_size ?? 5} videos are shown but never used for conclusions; winners need a 90% confidence interval clear of the rest.`}>
        <ActionButton path="/analytics/collect" label="Collect metrics now" done="Collection started" onDone={reload} />
        <ActionButton path="/analytics/report" label="Generate weekly report" variant="primary" done="Report generated" onDone={reloadReports} />
      </PageHeader>
      <ErrorNote error={error} />
      {s && (
        <>
          <div className="mb-6 rounded-2xl border border-pink-400/20 bg-pink-500/[0.06] px-5 py-4"><div className="text-xs uppercase tracking-[0.14em] text-pink-300">Next focus</div><p className="mt-1 text-white">{s.answers.next_focus}</p></div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {QUESTIONS.map(([k, label]) => { const a = s.answers[k] as Answer; return <Stat key={k} label={label} value={a?.answer ?? "—"} hint={a?.answer ? `median ${fmt.pct(a.median)} · n=${a.n}` : a?.reason} />; })}
          </div>
          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <Card title="Engagement by category">
              {s.by.category.length ? (
                <ul className="space-y-3">{s.by.category.map((g) => (
                  <li key={g.group}><div className="mb-1 flex justify-between text-sm"><span className="text-zinc-200">{fmt.label(g.group)}</span><span className="text-zinc-400">{fmt.pct(g.median)} · n={g.n} {!g.sufficient_sample && <Badge tone="warn">small sample</Badge>}</span></div><div className="h-1.5 rounded-full bg-white/10"><div className="bar" style={{ width: `${(g.median / max) * 100}%` }} /></div></li>
                ))}</ul>
              ) : <Empty>No published videos with metrics yet.</Empty>}
            </Card>
            <Card title="Data completeness">
              <p className="mb-3 text-xs text-zinc-500">{s.videos_with_metrics} videos with metrics. Counts below are videos where the platform did not report the metric; they are excluded, not zeroed.</p>
              <div className="grid grid-cols-2 gap-2 text-sm">{Object.entries(s.missing_metric_counts).map(([k, v]) => <div key={k} className="flex justify-between rounded-lg border border-white/[0.06] px-3 py-1.5"><span className="text-zinc-400">{k.replace(/_/g, " ")}</span><span className="text-zinc-200">{v} missing</span></div>)}</div>
              {s.answers.oversaturated_topics.length > 0 && <p className="mt-3 text-sm text-amber-300">Oversaturated: {s.answers.oversaturated_topics.map((o) => o.keyword).join(", ")}</p>}
            </Card>
          </div>
        </>
      )}
      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Card title="Videos">
          {videos?.length ? (
            <Table head={["Title", "Platform", "Views", "Likes", "Comments", "Shares", "Saves", "Avg watch", "Engagement"]}>
              {videos.map((v) => (
                <tr key={v.publication_id}><td className="max-w-[220px] truncate py-2 pr-4 text-zinc-200">{v.title}</td><td className="py-2 pr-4"><Badge tone="muted">{v.platform}</Badge></td>
                  {[v.views, v.likes, v.comments, v.shares, v.saves].map((n, i) => <td key={i} className="py-2 pr-4 text-zinc-300">{fmt.num(n)}</td>)}
                  <td className="py-2 pr-4 text-zinc-300">{fmt.secs(v.avg_watch_seconds)}</td><td className="py-2 text-zinc-300">{fmt.pct(v.engagement_rate)}</td></tr>
              ))}
            </Table>
          ) : <Empty>No metrics collected yet.</Empty>}
        </Card>
        <Card title="Weekly reports">
          {reports?.length ? reports.map((r) => (
            <div key={r.id} className="mb-3 rounded-xl border border-white/[0.06] p-3"><div className="text-xs text-zinc-500">{fmt.date(r.period_start)} → {fmt.date(r.period_end)}</div><ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-zinc-300">{r.recommendations.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
          )) : <Empty>No reports yet.</Empty>}
        </Card>
      </div>
    </>
  );
}
