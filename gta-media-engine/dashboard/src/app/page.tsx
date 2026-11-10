"use client";

import Link from "next/link";
import { fmt, useApi } from "@/lib/api";
import { ActionButton, Badge, Card, Empty, ErrorNote, PageHeader, Stat } from "@/components/ui";

type Overview = {
  mode: string; publishing_paused: boolean; processing_paused: boolean; processing_pause_reason: string;
  videos_created: number; jobs_total: number; published_videos: number; total_views: number | null;
  total_interactions: number | null; engagement_rate: number | null; jobs_in_production: number;
  pending_publications: number; failed_jobs: number; failed_publications: number; api_calls_today: number;
  spend: { today: { estimated_usd: number; actual_usd: number }; month: { estimated_usd: number; actual_usd: number }; budgets: { daily_usd: number; monthly_usd: number } };
  schedule: { id: number; platform: string; job_id: number; scheduled_for: string | null; title: string | null }[];
};

export default function OverviewPage() {
  const { data: o, error, reload } = useApi<Overview>("/overview", 10000);
  return (
    <>
      <PageHeader title="Overview" subtitle="The state of the operation at a glance. Metrics the platforms have not reported show as — rather than zero.">
        <ActionButton path="/research/run" label="Run research" done="Research started" onDone={reload} />
        <ActionButton path="/strategy/plan" label="Plan today's videos" done="Planning started" onDone={reload} />
        <ActionButton path="/publications/process" label="Process publishing queue" variant="primary" done="Publishing pass started" onDone={reload} />
      </PageHeader>
      <ErrorNote error={error} />
      {o && (
        <>
          {o.mode === "test" && (
            <div className="mb-6 rounded-2xl border border-amber-400/20 bg-amber-500/[0.07] px-5 py-4 text-sm text-amber-100">
              <b>TEST MODE.</b> Videos are researched, written, rendered and prepared, but nothing is published. Complete the <Link className="underline" href="/settings">launch checklist</Link> to enable autonomous publishing.
            </div>
          )}
          {o.processing_paused && <ErrorNote error={`Processing paused: ${o.processing_pause_reason}`} />}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat label="Videos created" value={fmt.num(o.videos_created)} hint={`${o.jobs_total} jobs total`} />
            <Stat label="Published" value={fmt.num(o.published_videos)} accent />
            <Stat label="Total views" value={fmt.num(o.total_views)} />
            <Stat label="Engagement" value={fmt.pct(o.engagement_rate)} hint={`${fmt.num(o.total_interactions)} interactions`} />
            <Stat label="In production" value={o.jobs_in_production} />
            <Stat label="Pending publish" value={o.pending_publications} />
            <Stat label="Failed / blocked jobs" value={o.failed_jobs} />
            <Stat label="Failed publications" value={o.failed_publications} />
            <Stat label="API calls today" value={o.api_calls_today} />
            <Stat label="AI spend today" value={fmt.usd(o.spend.today.estimated_usd)} hint={`actual ${fmt.usd(o.spend.today.actual_usd)} · cap $${o.spend.budgets.daily_usd}`} />
            <Stat label="AI spend month" value={fmt.usd(o.spend.month.estimated_usd)} hint={`actual ${fmt.usd(o.spend.month.actual_usd)} · cap $${o.spend.budgets.monthly_usd}`} />
            <Stat label="Mode" value={<Badge tone={o.mode === "autonomous" ? "brand" : "warn"}>{o.mode}</Badge>} hint={o.publishing_paused ? "publishing paused" : "publishing live"} />
          </div>
          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <Card title="Publishing schedule" actions={<Link href="/publishing" className="text-xs text-pink-300 hover:underline">Open manager →</Link>}>
              {o.schedule.length ? (
                <ul className="divide-y divide-white/[0.06]">
                  {o.schedule.map((s) => (
                    <li key={s.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                      <span className="truncate text-zinc-200">{s.title ?? `Job #${s.job_id}`}</span>
                      <span className="flex shrink-0 items-center gap-2"><Badge tone="info">{s.platform}</Badge><span className="text-xs text-zinc-500">{fmt.date(s.scheduled_for)}</span></span>
                    </li>
                  ))}
                </ul>
              ) : <Empty>Nothing queued for publishing.</Empty>}
            </Card>
            <Card title="Pipeline">
              <ol className="grid grid-cols-2 gap-2 text-xs text-zinc-400 sm:grid-cols-3">
                {["Research", "Topic scoring", "Fact verification", "Script", "Script validation", "Voiceover", "Footage selection", "Editing", "Quality control", "Metadata", "Publishing queue", "Distribution", "Analytics", "Strategy"].map((s, i) => (
                  <li key={s} className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2"><span className="mr-1.5 text-pink-400">{String(i + 1).padStart(2, "0")}</span>{s}</li>
                ))}
              </ol>
            </Card>
          </div>
        </>
      )}
    </>
  );
}
