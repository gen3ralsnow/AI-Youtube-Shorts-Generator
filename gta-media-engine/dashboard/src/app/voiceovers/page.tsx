"use client";

import { media, fmt, useApi } from "@/lib/api";
import { ActionButton, Badge, Card, Empty, ErrorNote, PageHeader } from "@/components/ui";

type Voice = { id: number; job_id: number; provider: string; is_placeholder: boolean; duration: number; raw_duration: number; voice_settings: Record<string, unknown>; raw_url: string; processed_url: string; created_at: string; word_count: number };

export default function VoicePage() {
  const { data, error, reload } = useApi<Voice[]>("/voiceovers", 20000);
  return (
    <>
      <PageHeader title="Voiceover library" subtitle="Original provider audio and the processed narration (pauses trimmed, loudness normalised) are stored separately." />
      <ErrorNote error={error} />
      {data?.length ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {data.map((v) => (
            <Card key={v.id} title={`Job #${v.job_id} · ${v.provider}`} actions={<>{v.is_placeholder && <Badge tone="warn">placeholder</Badge>}<ActionButton path={`/jobs/${v.job_id}/regenerate`} body={{ target: "voiceover" }} label="Regenerate" done="Regeneration queued" onDone={reload} /></>}>
              <div className="grid gap-3 sm:grid-cols-2">
                <div><div className="mb-1 text-xs text-zinc-500">Original · {fmt.secs(v.raw_duration)}</div><audio controls preload="none" src={media(v.raw_url)} className="w-full" /></div>
                <div><div className="mb-1 text-xs text-zinc-500">Processed · {fmt.secs(v.duration)}</div><audio controls preload="none" src={media(v.processed_url)} className="w-full" /></div>
              </div>
              <p className="mt-3 text-xs text-zinc-500">{v.word_count} timed words · speed {String(v.voice_settings.speed)} · {String(v.voice_settings.profile)} · {fmt.date(v.created_at)}</p>
            </Card>
          ))}
        </div>
      ) : <Card><Empty>No voiceovers yet.</Empty></Card>}
    </>
  );
}
