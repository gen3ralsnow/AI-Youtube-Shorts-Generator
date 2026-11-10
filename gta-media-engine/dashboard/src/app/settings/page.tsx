"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import SettingEditor from "@/components/SettingEditor";
import { ActionButton, Badge, Button, Card, ErrorNote, inputCls, PageHeader, useToast } from "@/components/ui";

type Check = { items: { key: string; label: string; passed: boolean; detail: string; required: boolean }[]; ready: boolean; mode: string; publishing_paused: boolean; processing_paused: boolean; autonomous_activated_by: string; autonomous_activated_at: string | null };
const PHRASE = "ACTIVATE AUTONOMOUS MODE";

export default function SettingsPage() {
  const { data: s, error, reload } = useApi<Record<string, never>>("/settings");
  const { data: c, reload: reloadCheck } = useApi<Check>("/control/launch-checklist", 20000);
  const toast = useToast();
  const [phrase, setPhrase] = useState("");
  const refresh = () => { reload(); reloadCheck(); };
  const activate = async () => {
    try {
      const r = await api<{ note: string }>("/control/activate-autonomous", { method: "POST", json: { confirm: phrase } });
      toast("ok", r.note);
      setPhrase("");
      refresh();
    } catch (e) { toast("err", (e as Error).message); }
  };
  return (
    <>
      <PageHeader title="Settings & launch" subtitle="Schedules, platform limits and the controlled switch from TEST mode to AUTONOMOUS mode." />
      <ErrorNote error={error} />
      {c && (
        <Card title="Launch checklist" className="mb-6" actions={<Badge tone={c.mode === "autonomous" ? "brand" : "warn"}>{c.mode}</Badge>}>
          <ul className="grid gap-2 md:grid-cols-2">
            {c.items.map((i) => (
              <li key={i.key} className="flex items-start gap-3 rounded-xl border border-white/[0.06] px-3 py-2.5 text-sm">
                <span className={i.passed ? "text-emerald-400" : i.required ? "text-rose-400" : "text-amber-400"}>{i.passed ? "✓" : i.required ? "✗" : "!"}</span>
                <span className="min-w-0"><span className="text-zinc-100">{i.label}</span>{!i.required && <span className="ml-1 text-xs text-zinc-500">(recommended)</span>}<span className="block truncate text-xs text-zinc-500">{i.detail}</span></span>
              </li>
            ))}
          </ul>
          <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-white/[0.06] pt-5">
            {c.mode === "test" ? (
              <>
                <input className={`${inputCls} max-w-xs`} placeholder={`Type ${PHRASE}`} value={phrase} onChange={(e) => setPhrase(e.target.value)} />
                <Button variant="primary" onClick={activate} disabled={!c.ready || phrase !== PHRASE}>Activate autonomous mode</Button>
                {!c.ready && <span className="text-xs text-zinc-500">Complete every required item first.</span>}
              </>
            ) : (
              <>
                <span className="text-sm text-zinc-300">Activated by {c.autonomous_activated_by} at {c.autonomous_activated_at}</span>
                {c.publishing_paused ? <ActionButton path="/control/resume-publishing" label="Resume publishing" variant="primary" confirm="Start publishing to connected platforms on schedule?" onDone={refresh} /> : <ActionButton path="/control/pause-publishing" label="Pause publishing" variant="danger" onDone={refresh} />}
                <ActionButton path="/control/deactivate-autonomous" label="Return to TEST mode" confirm="Stop autonomous publishing and return to TEST mode?" onDone={refresh} />
              </>
            )}
            <span className="ml-auto flex gap-2">
              {c.processing_paused ? <ActionButton path="/control/resume-processing" label="Resume processing" onDone={refresh} /> : <ActionButton path="/control/pause-processing" label="Pause processing" onDone={refresh} />}
            </span>
          </div>
        </Card>
      )}
      {s && (
        <div className="grid gap-6 xl:grid-cols-2">
          <SettingEditor name="schedule" title="Schedule & platforms" value={s.schedule} onSaved={refresh} hints={{ publishing_windows: "HH:MM-HH:MM, comma separated (UTC)", "platforms.tiktok.mode": "inbox_draft (default) or direct_post (needs per-post consent)", "platforms.youtube.privacy_when_autonomous": "public | unlisted | private" }} />
          <SettingEditor name="budgets" title="Spending limits" value={s.budgets} onSaved={refresh} />
        </div>
      )}
    </>
  );
}
