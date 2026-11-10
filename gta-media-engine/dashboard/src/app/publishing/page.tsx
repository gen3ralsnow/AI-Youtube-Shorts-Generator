"use client";

import { useState } from "react";
import { api, fmt, useApi } from "@/lib/api";
import { ActionButton, Badge, Button, Card, Empty, ErrorNote, Field, inputCls, PageHeader, Table, useToast } from "@/components/ui";

type Pub = { id: number; job_id: number; platform: string; status: string; privacy: string; scheduled_for: string | null; external_id: string | null; url: string | null; attempts: number; last_error: string | null; is_test: boolean; job_title: string | null; consent: Record<string, unknown> };

export default function PublishingPage() {
  const { data, error, reload } = useApi<Pub[]>("/publications", 8000);
  const [consentFor, setConsentFor] = useState<Pub | null>(null);
  return (
    <>
      <PageHeader title="Publishing manager" subtitle="Platform uploads, their confirmed status and retries. Nothing is marked published until the platform confirms it.">
        <ActionButton path="/publications/process" label="Process due items now" variant="primary" done="Publishing pass started" onDone={reload} />
      </PageHeader>
      <ErrorNote error={error} />
      <Card>
        {data?.length ? (
          <Table head={["#", "Video", "Platform", "Status", "Scheduled", "Attempts", "Result", ""]}>
            {data.map((p) => (
              <tr key={p.id}>
                <td className="py-2.5 pr-4 text-zinc-500">{p.id}</td>
                <td className="max-w-[260px] truncate py-2.5 pr-4 text-zinc-100">{p.job_title ?? `Job #${p.job_id}`}{p.is_test && <span className="ml-2"><Badge tone="info">test</Badge></span>}</td>
                <td className="py-2.5 pr-4"><Badge tone="muted">{p.platform}</Badge></td>
                <td className="py-2.5 pr-4"><Badge>{p.status}</Badge></td>
                <td className="py-2.5 pr-4 text-xs text-zinc-500">{fmt.date(p.scheduled_for)}</td>
                <td className="py-2.5 pr-4 text-zinc-400">{p.attempts}</td>
                <td className="max-w-[300px] py-2.5 pr-4 text-xs">
                  {p.url ? <a className="text-cyan-300 hover:underline" href={p.url} target="_blank" rel="noreferrer noopener">{p.url}</a> : <span className="text-amber-300/80">{p.last_error}</span>}
                </td>
                <td className="whitespace-nowrap py-2.5 text-right">
                  <div className="flex justify-end gap-1.5">
                    {["failed", "blocked", "dry_run"].includes(p.status) && <ActionButton path={`/publications/${p.id}/retry`} label="Re-queue" onDone={reload} />}
                    {["processing", "drafted"].includes(p.status) && <ActionButton path={`/publications/${p.id}/refresh`} label="Check status" onDone={reload} />}
                    {p.platform === "tiktok" && p.status === "queued" && <Button onClick={() => setConsentFor(p)}>{p.consent?.confirmed_by ? "Consent ✓" : "TikTok consent"}</Button>}
                  </div>
                </td>
              </tr>
            ))}
          </Table>
        ) : <Empty>No publications yet. Jobs that pass QC are queued here automatically.</Empty>}
      </Card>
      {consentFor && <ConsentDialog pub={consentFor} onClose={() => { setConsentFor(null); reload(); }} />}
    </>
  );
}

function ConsentDialog({ pub, onClose }: { pub: Pub; onClose: () => void }) {
  const toast = useToast();
  const [privacy, setPrivacy] = useState("SELF_ONLY");
  const [music, setMusic] = useState(false);
  const [disableComment, setDisableComment] = useState(false);
  const submit = async () => {
    try {
      await api(`/publications/${pub.id}/tiktok-consent`, { method: "POST", json: { privacy_level: privacy, music_usage_confirmed: music, disable_comment: disableComment } });
      toast("ok", "Consent recorded for this post");
      onClose();
    } catch (e) { toast("err", (e as Error).message); }
  };
  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/70 p-4" onClick={onClose}>
      <div className="card w-full max-w-md rounded-2xl bg-[#0b0b12] p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="font-display text-lg font-bold text-white">TikTok direct-post consent</h2>
        <p className="mt-1 text-sm text-zinc-400">TikTok requires a person to choose the privacy level and agree to its terms for each direct post. Without this, the video goes to your TikTok drafts instead.</p>
        <div className="mt-4 space-y-4">
          <Field label="Who can view this video">
            <select className={inputCls} value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
              {["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"].map((p) => <option key={p}>{p}</option>)}
            </select>
          </Field>
          <label className="flex items-center gap-2 text-sm text-zinc-300"><input type="checkbox" checked={disableComment} onChange={(e) => setDisableComment(e.target.checked)} className="accent-pink-500" />Disable comments</label>
          <label className="flex items-start gap-2 text-sm text-zinc-300"><input type="checkbox" checked={music} onChange={(e) => setMusic(e.target.checked)} className="mt-1 accent-pink-500" />By posting, I agree to TikTok&apos;s Music Usage Confirmation.</label>
          <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={submit} disabled={!music}>Record consent</Button></div>
        </div>
      </div>
    </div>
  );
}
