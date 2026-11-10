"use client";

import { useApi } from "@/lib/api";
import SettingEditor from "@/components/SettingEditor";
import { Badge, Card, ErrorNote, PageHeader } from "@/components/ui";

type Health = { providers: Record<string, { provider: string; available: boolean; placeholder?: boolean }> };

export default function AIPage() {
  const { data: s, error, reload } = useApi<Record<string, never>>("/settings");
  const { data: h } = useApi<Health>("/system/health");
  return (
    <>
      <PageHeader title="AI configuration" subtitle="Providers are chosen with server environment variables (keys never reach the browser). Voice, thresholds and brand are tuned here." />
      <ErrorNote error={error} />
      <Card title="Providers" className="mb-6">
        <div className="grid gap-3 sm:grid-cols-3">
          {h && Object.entries(h.providers).map(([k, p]) => (
            <div key={k} className="rounded-xl border border-white/[0.06] p-3">
              <div className="text-xs uppercase tracking-[0.14em] text-zinc-500">{k}</div>
              <div className="mt-1 flex items-center justify-between"><span className="text-white">{p.provider}</span>{p.placeholder ? <Badge tone="warn">placeholder</Badge> : <Badge tone={p.available ? "good" : "bad"}>{p.available ? "available" : "not configured"}</Badge>}</div>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-zinc-500">Set LLM_PROVIDER (anthropic | openai), TTS_PROVIDER (elevenlabs | openai) and TRANSCRIPTION_PROVIDER (openai | local) with their API keys in the backend environment. Only voices you have rights to are supported; cloning an identifiable person&apos;s voice is not.</p>
      </Card>
      {s && (
        <div className="grid gap-6 xl:grid-cols-2">
          <SettingEditor name="voice" title="Voice" value={s.voice} onSaved={reload} hints={{ provider_voice_id: "Voice ID from your TTS provider (stock or designed voice you are licensed to use)", speed: "0.7 – 1.2", stability: "0 – 1 (lower = more expressive)", emotional_intensity: "0 – 1" }} />
          <SettingEditor name="processing" title="Quality thresholds & processing" value={s.processing} onSaved={reload} hints={{ fact_confidence_threshold: "0.5 – 1.0; below this a video is never published", min_topic_score: "topics below this score are not produced", stage_retry_limit: "attempts per stage before a job fails" }} />
          <SettingEditor name="categories" title="Content categories & mix" value={s.categories} onSaved={reload} />
          <SettingEditor name="brand" title="Brand style" value={s.brand} onSaved={reload} hints={{ primary_color: "active-word highlight", accent_color: "keyword colour" }} />
          <SettingEditor name="analytics" title="Analytics" value={s.analytics} onSaved={reload} hints={{ min_sample_size: "minimum videos per group before drawing conclusions" }} />
        </div>
      )}
    </>
  );
}
