"use client";

import { useEffect, useState } from "react";
import { api, media, fmt, useApi } from "@/lib/api";
import { ActionButton, Badge, Button, Card, ErrorNote, inputCls, Json, Modal, useToast } from "./ui";

type Stage = { id: number; stage: string; status: string; attempt: number; error: string | null; output: Record<string, unknown>; finished_at: string | null };
type Scene = { index: number; section: string; narration: string; caption: string; start: number; end: number; transition: string; sfx: string | null; visual: { keywords: string[] } };
type Render = { id: number; profile: string; url: string; duration: number; size_bytes: number; qc_passed: boolean; qc: { passed?: boolean; checks?: { name: string; passed: boolean; detail: string; severity: string }[]; content_blockers?: string[] } };
type Pub = { id: number; platform: string; status: string; url: string | null; last_error: string | null; is_test: boolean; scheduled_for: string | null };
export type JobDetail = {
  id: number; status: string; category: string; classification: string | null; current_stage: string | null; next_stage: string | null;
  error: string | null; blocked_reason: string | null; topic_title: string | null; title_override: string | null; duration_bucket: string;
  estimated_cost_usd: number; actual_cost_usd: number;
  stages: Stage[];
  script: null | { title: string; chosen_hook: string; hooks: string[]; voiceover_text: string; scenes: Scene[]; claims: { text: string; source_ids: number[] }[]; sources: { id: number; title: string; url: string; credibility: string }[]; validation: { errors?: string[]; warnings?: string[] }; fact_check: { issues?: string[]; checker?: string }; confidence: number; generator: string; estimated_seconds: number; call_to_action: string };
  voiceover: null | { provider: string; is_placeholder: boolean; duration: number; raw_url: string; processed_url: string };
  renders: Render[];
  thumbnails: { id: number; platform: string; variant: string; url: string; selected: boolean }[];
  metadata: { platform: string; title: string; title_variations: string[]; hook_variations: string[]; description: string; hashtags: string[]; pinned_comment: string }[];
  publications: Pub[];
};

export default function JobDrawer({ jobId, onClose, onChanged }: { jobId: number | null; onClose: () => void; onChanged?: () => void }) {
  const { data: job, error, reload } = useApi<JobDetail>(jobId ? `/jobs/${jobId}` : null, 5000);
  const refresh = () => { reload(); onChanged?.(); };
  return (
    <Modal open={jobId !== null} onClose={onClose} title={job ? `Job #${job.id} · ${job.script?.title ?? job.topic_title ?? ""}` : "Loading…"}>
      <ErrorNote error={error} />
      {job && <JobBody job={job} refresh={refresh} onClose={onClose} />}
    </Modal>
  );
}

function JobBody({ job, refresh, onClose }: { job: JobDetail; refresh: () => void; onClose: () => void }) {
  const [tab, setTab] = useState("overview");
  const tabs = ["overview", "script", "media", "metadata", "publishing", "stages"];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge>{job.status}</Badge>
        <Badge tone="muted">{fmt.label(job.category)}</Badge>
        {job.classification && <Badge>{job.classification}</Badge>}
        {job.next_stage && <span className="text-xs text-zinc-500">next: {fmt.label(job.next_stage)}</span>}
        <span className="ml-auto text-xs text-zinc-500">cost est {fmt.usd(job.estimated_cost_usd)} · actual {fmt.usd(job.actual_cost_usd)}</span>
      </div>
      {(job.error || job.blocked_reason) && <ErrorNote error={job.blocked_reason ?? job.error} />}
      <div className="flex flex-wrap gap-2">
        {job.status === "queued" && <ActionButton path={`/jobs/${job.id}/run`} label="Run now" variant="primary" done="Job started" onDone={refresh} />}
        {["failed", "blocked", "queued"].includes(job.status) && <ActionButton path={`/jobs/${job.id}/retry`} label="Retry" done="Retrying" onDone={refresh} />}
        <ActionButton path={`/jobs/${job.id}/regenerate`} body={{ target: "script" }} label="Regenerate script" done="Script regeneration queued" onDone={refresh} confirm="Discard the script and everything after it?" />
        <ActionButton path={`/jobs/${job.id}/regenerate`} body={{ target: "voiceover" }} label="Regenerate voiceover" done="Voiceover regeneration queued" onDone={refresh} />
        <ActionButton path={`/jobs/${job.id}/regenerate`} body={{ target: "video" }} label="Regenerate video" done="Video regeneration queued" onDone={refresh} />
        <ActionButton path={`/jobs/${job.id}/cancel`} label="Cancel" onDone={refresh} confirm="Cancel this job?" />
        <ActionButton path={`/jobs/${job.id}`} method="DELETE" label="Delete draft" variant="danger" done="Deleted" onDone={() => { refresh(); onClose(); }} confirm="Delete this draft and its files permanently?" />
      </div>
      <div className="flex gap-1 overflow-x-auto border-b border-white/10">
        {tabs.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`whitespace-nowrap px-3 py-2 text-sm capitalize ${tab === t ? "border-b-2 border-pink-400 text-white" : "text-zinc-500 hover:text-zinc-300"}`}>{t}</button>
        ))}
      </div>
      {tab === "overview" && <Overview job={job} />}
      {tab === "script" && <ScriptTab job={job} refresh={refresh} />}
      {tab === "media" && <MediaTab job={job} />}
      {tab === "metadata" && <MetadataTab job={job} refresh={refresh} />}
      {tab === "publishing" && <PublishingTab job={job} refresh={refresh} />}
      {tab === "stages" && <StagesTab job={job} />}
    </div>
  );
}

function Overview({ job }: { job: JobDetail }) {
  const yt = job.renders.find((r) => r.profile === "youtube_shorts") ?? job.renders[0];
  return (
    <div className="grid gap-5 md:grid-cols-[260px_1fr]">
      <div>
        {yt ? <video key={yt.url} src={media(yt.url)} controls className="aspect-[9/16] w-full rounded-xl bg-black" /> :
          <div className="grid aspect-[9/16] place-items-center rounded-xl border border-dashed border-white/10 text-sm text-zinc-500">No render yet</div>}
      </div>
      <div className="space-y-4">
        {job.script && (
          <Card title="Hook">
            <p className="text-lg font-semibold text-white">“{job.script.chosen_hook}”</p>
            <p className="mt-2 text-xs text-zinc-500">{job.script.generator} · {fmt.secs(job.script.estimated_seconds)} est. · fact confidence {(job.script.confidence * 100).toFixed(0)}%</p>
          </Card>
        )}
        <Card title="Pipeline">
          <ol className="space-y-1.5">
            {job.stages.filter((s) => s.status !== "superseded").map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-3 text-sm">
                <span className="text-zinc-300">{fmt.label(s.stage)}</span>
                <span className="flex items-center gap-2"><span className="text-xs text-zinc-600">try {s.attempt}</span><Badge>{s.status}</Badge></span>
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </div>
  );
}

function ScriptTab({ job, refresh }: { job: JobDetail; refresh: () => void }) {
  const toast = useToast();
  const [caps, setCaps] = useState<string[]>([]);
  useEffect(() => setCaps(job.script?.scenes.map((s) => s.caption) ?? []), [job.script]);
  if (!job.script) return <p className="text-sm text-zinc-500">No script yet.</p>;
  const s = job.script;
  const saveCaptions = async () => {
    try {
      await api(`/jobs/${job.id}`, { method: "PATCH", json: { captions: caps } });
      toast("ok", "Captions saved; video will re-render");
      refresh();
    } catch (e) { toast("err", (e as Error).message); }
  };
  return (
    <div className="space-y-4">
      <Card title="Hook variants">
        <ul className="space-y-1 text-sm">{s.hooks.map((h) => <li key={h} className={h === s.chosen_hook ? "text-pink-300" : "text-zinc-400"}>{h === s.chosen_hook ? "★ " : "· "}{h}</li>)}</ul>
      </Card>
      <Card title="Scenes & on-screen captions" actions={<Button variant="primary" onClick={saveCaptions}>Save captions</Button>}>
        <div className="space-y-3">
          {s.scenes.map((sc, i) => (
            <div key={i} className="rounded-xl border border-white/[0.06] p-3">
              <div className="mb-1 flex items-center gap-2 text-xs text-zinc-500"><Badge tone="info">{sc.section}</Badge>{sc.start.toFixed(1)}s–{sc.end.toFixed(1)}s · {sc.transition}{sc.sfx ? ` · sfx ${sc.sfx}` : ""}</div>
              <p className="text-sm text-zinc-200">{sc.narration}</p>
              <input className={`${inputCls} mt-2`} value={caps[i] ?? ""} maxLength={60} onChange={(e) => setCaps(caps.map((c, j) => (j === i ? e.target.value : c)))} />
              <p className="mt-1 text-[11px] text-zinc-600">visual: {sc.visual?.keywords?.join(", ")}</p>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Claims & sources">
        <ul className="space-y-2 text-sm">
          {s.claims.map((c, i) => <li key={i} className="text-zinc-300">“{c.text}” <span className="text-xs text-zinc-500">→ sources {c.source_ids.join(", ")}</span></li>)}
        </ul>
        <ul className="mt-3 space-y-1 text-xs">
          {s.sources.map((src) => <li key={src.id}><Badge>{src.credibility}</Badge> <a className="text-cyan-300 hover:underline" href={src.url} target="_blank" rel="noreferrer noopener">[{src.id}] {src.title}</a></li>)}
        </ul>
        {!!s.fact_check?.issues?.length && <div className="mt-3 text-xs text-amber-300">Fact issues: {s.fact_check.issues.join("; ")}</div>}
        {!!s.validation?.warnings?.length && <div className="mt-2 text-xs text-zinc-400">Warnings: {s.validation.warnings.join("; ")}</div>}
      </Card>
    </div>
  );
}

function MediaTab({ job }: { job: JobDetail }) {
  return (
    <div className="space-y-4">
      {job.voiceover && (
        <Card title={`Voiceover · ${job.voiceover.provider} · ${fmt.secs(job.voiceover.duration)}`}>
          {job.voiceover.is_placeholder && <p className="mb-2 text-xs text-amber-300">Placeholder guide tone. Configure a TTS provider; QC will not allow publishing.</p>}
          <div className="grid gap-2 sm:grid-cols-2">
            <div><div className="mb-1 text-xs text-zinc-500">Original</div><audio controls src={media(job.voiceover.raw_url)} className="w-full" /></div>
            <div><div className="mb-1 text-xs text-zinc-500">Processed</div><audio controls src={media(job.voiceover.processed_url)} className="w-full" /></div>
          </div>
        </Card>
      )}
      {job.renders.map((r) => (
        <Card key={r.id} title={`${r.profile} · ${fmt.secs(r.duration)} · ${(r.size_bytes / 1e6).toFixed(1)} MB`}
          actions={<><Badge tone={r.qc_passed ? "good" : "bad"}>{r.qc_passed ? "technical QC passed" : "QC failed"}</Badge><Badge tone={r.qc?.passed ? "good" : "warn"}>{r.qc?.passed ? "publishable" : "not publishable"}</Badge></>}>
          <div className="grid gap-4 md:grid-cols-[180px_1fr]">
            <video src={media(r.url)} controls preload="metadata" className="aspect-[9/16] w-full rounded-lg bg-black" />
            <ul className="space-y-1 text-xs">
              {r.qc?.checks?.map((c) => (
                <li key={c.name} className="flex gap-2"><span className={c.passed ? "text-emerald-400" : c.severity === "warning" ? "text-amber-400" : "text-rose-400"}>{c.passed ? "✓" : "✗"}</span><span className="text-zinc-300">{c.name.replace(/_/g, " ")}</span><span className="text-zinc-500">{c.detail}</span></li>
              ))}
              {r.qc?.content_blockers?.map((b) => <li key={b} className="text-amber-300">⚠ {b}</li>)}
            </ul>
          </div>
        </Card>
      ))}
      {job.thumbnails.length > 0 && (
        <Card title="Thumbnails">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {job.thumbnails.map((t) => (
              <figure key={t.id}><img src={media(t.url)} alt={`${t.platform} thumbnail ${t.variant}`} className={`w-full rounded-lg ${t.selected ? "ring-2 ring-pink-400" : ""}`} /><figcaption className="mt-1 text-xs text-zinc-500">{t.platform} · {t.variant}{t.selected ? " · selected" : ""}</figcaption></figure>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function MetadataTab({ job, refresh }: { job: JobDetail; refresh: () => void }) {
  const toast = useToast();
  const [title, setTitle] = useState(job.title_override ?? "");
  const save = async () => {
    try {
      await api(`/jobs/${job.id}`, { method: "PATCH", json: { title_override: title } });
      toast("ok", "Title updated; metadata will regenerate");
      refresh();
    } catch (e) { toast("err", (e as Error).message); }
  };
  return (
    <div className="space-y-4">
      <Card title="Title override" actions={<Button variant="primary" onClick={save}>Save title</Button>}>
        <input className={inputCls} value={title} maxLength={100} placeholder="Leave empty to use generated titles" onChange={(e) => setTitle(e.target.value)} />
        <p className="mt-2 text-xs text-zinc-500">Misleading words (official/confirmed) are removed automatically unless the topic is officially confirmed.</p>
      </Card>
      {job.metadata.map((m) => (
        <Card key={m.platform} title={m.platform}>
          <p className="font-semibold text-white">{m.title}</p>
          <details className="mt-2 text-sm text-zinc-400"><summary className="cursor-pointer text-zinc-300">Title variations</summary><ul className="mt-1 list-disc pl-5">{m.title_variations.map((t) => <li key={t}>{t}</li>)}</ul></details>
          <pre className="mt-3 whitespace-pre-wrap rounded-xl bg-black/40 p-3 text-xs text-zinc-300">{m.description}</pre>
          <p className="mt-2 text-xs text-cyan-300">{m.hashtags.join(" ")}</p>
          <p className="mt-2 text-xs text-zinc-500">Pinned comment (post manually where the API cannot pin): {m.pinned_comment.split("\n")[0]}</p>
        </Card>
      ))}
    </div>
  );
}

function PublishingTab({ job, refresh }: { job: JobDetail; refresh: () => void }) {
  return (
    <div className="space-y-4">
      <Card title="Non-public test uploads">
        <p className="mb-3 text-sm text-zinc-400">YouTube uploads as private; TikTok sends to the creator&apos;s inbox as a draft. Instagram has no private API mode. Requires a publishable render and a connected account.</p>
        <div className="flex flex-wrap gap-2">
          <ActionButton path={`/jobs/${job.id}/test-upload`} body={{ platform: "youtube" }} label="Test upload to YouTube (private)" done="Test upload sent" onDone={refresh} />
          <ActionButton path={`/jobs/${job.id}/test-upload`} body={{ platform: "tiktok" }} label="Send to TikTok drafts" done="Draft sent" onDone={refresh} />
        </div>
      </Card>
      {job.publications.map((p) => (
        <Card key={p.id} title={`${p.platform}${p.is_test ? " · test" : ""}`} actions={<Badge>{p.status}</Badge>}>
          <p className="text-xs text-zinc-500">scheduled {fmt.date(p.scheduled_for)}</p>
          {p.url && <a href={p.url} target="_blank" rel="noreferrer noopener" className="text-sm text-cyan-300 hover:underline">{p.url}</a>}
          {p.last_error && <p className="mt-2 text-xs text-amber-300">{p.last_error}</p>}
        </Card>
      ))}
    </div>
  );
}

function StagesTab({ job }: { job: JobDetail }) {
  return (
    <div className="space-y-3">
      {job.stages.map((s) => (
        <details key={s.id} className="card rounded-xl p-3">
          <summary className="flex cursor-pointer items-center justify-between gap-3 text-sm"><span>{fmt.label(s.stage)} · attempt {s.attempt}</span><Badge>{s.status}</Badge></summary>
          {s.error && <p className="mt-2 text-xs text-rose-300">{s.error}</p>}
          <div className="mt-2"><Json value={s.output} /></div>
        </details>
      ))}
    </div>
  );
}
