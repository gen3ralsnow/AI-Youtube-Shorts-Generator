"use client";

import { api, fmt, useApi } from "@/lib/api";
import { ActionButton, Badge, Button, Card, ErrorNote, Json, PageHeader, useToast } from "@/components/ui";

type Conn = { platform: string; status: string; account_name: string; account_id: string; scopes: string[]; expires_at: string | null; verified_at: string | null; capabilities: Record<string, unknown>; last_error: string | null; app_credentials_missing: string[] };
const NOTES: Record<string, string> = {
  youtube: "YouTube Data API v3 resumable upload + YouTube Analytics. Unaudited API projects can only upload private videos.",
  tiktok: "Content Posting API. Default is drafts to the creator inbox; direct posting needs per-post consent and TikTok app audit for public visibility.",
  instagram: "Instagram API with Instagram Login. Requires a Business or Creator account; no private/draft publishing via API.",
};

export default function PlatformsPage() {
  const { data, error, reload } = useApi<Conn[]>("/platforms", 15000);
  const toast = useToast();
  const connect = async (p: string) => {
    try {
      const { authorize_url } = await api<{ authorize_url: string }>(`/platforms/${p}/connect`, { method: "POST" });
      window.location.href = authorize_url;
    } catch (e) { toast("err", (e as Error).message); }
  };
  return (
    <>
      <PageHeader title="Platform connections" subtitle="OAuth with PKCE and single-use state. Tokens are encrypted at rest and refreshed automatically; a connection only counts once verified against the platform." />
      <ErrorNote error={error} />
      <div className="grid gap-6 lg:grid-cols-3">
        {data?.map((c) => (
          <Card key={c.platform} title={c.platform} actions={<Badge>{c.status}</Badge>}>
            <p className="mb-4 text-xs text-zinc-500">{NOTES[c.platform]}</p>
            {c.app_credentials_missing.length > 0 ? (
              <div className="rounded-xl border border-amber-400/20 bg-amber-500/[0.06] p-3 text-sm text-amber-200">App credentials missing on the server: <code>{c.app_credentials_missing.join(", ")}</code></div>
            ) : (
              <div className="space-y-2 text-sm">
                {c.account_name && <p className="text-zinc-200">Account: <b>{c.account_name}</b></p>}
                <p className="text-xs text-zinc-500">verified {fmt.date(c.verified_at)} · token expires {fmt.date(c.expires_at)}</p>
                {c.last_error && <p className="text-xs text-rose-300">{c.last_error}</p>}
                <div className="flex flex-wrap gap-2 pt-2">
                  <Button variant="primary" onClick={() => connect(c.platform)}>{c.status === "disconnected" ? "Connect" : "Reconnect"}</Button>
                  {c.status !== "disconnected" && <ActionButton path={`/platforms/${c.platform}/verify`} label="Verify" done="Verified with platform" onDone={reload} />}
                  {c.status !== "disconnected" && <ActionButton path={`/platforms/${c.platform}/disconnect`} label="Disconnect" variant="danger" confirm="Disconnect and delete stored tokens?" onDone={reload} />}
                </div>
                {Object.keys(c.capabilities ?? {}).length > 0 && <details className="pt-2"><summary className="cursor-pointer text-xs text-zinc-400">Capabilities</summary><Json value={c.capabilities} /></details>}
              </div>
            )}
          </Card>
        ))}
      </div>
    </>
  );
}
