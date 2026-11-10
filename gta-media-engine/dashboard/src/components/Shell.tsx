"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useState } from "react";
import { useApi } from "@/lib/api";
import { ActionButton, Badge } from "./ui";

const NAV: { href: string; label: string; group: string }[] = [
  { href: "/", label: "Overview", group: "Operate" },
  { href: "/queue", label: "Production queue", group: "Operate" },
  { href: "/publishing", label: "Publishing manager", group: "Operate" },
  { href: "/calendar", label: "Content calendar", group: "Operate" },
  { href: "/topics", label: "Trending topics", group: "Discover" },
  { href: "/research", label: "AI research", group: "Discover" },
  { href: "/scripts", label: "Script library", group: "Create" },
  { href: "/voiceovers", label: "Voiceover library", group: "Create" },
  { href: "/exports", label: "Video & exports", group: "Create" },
  { href: "/footage", label: "Footage rights", group: "Create" },
  { href: "/analytics", label: "Performance analytics", group: "Grow" },
  { href: "/platforms", label: "Platform connections", group: "Grow" },
  { href: "/ai", label: "AI configuration", group: "System" },
  { href: "/costs", label: "Costs & usage", group: "System" },
  { href: "/health", label: "System health", group: "System" },
  { href: "/settings", label: "Settings & launch", group: "System" },
];

type Mode = { mode: string; publishing_paused: boolean; processing_paused: boolean; processing_pause_reason: string };

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const { data: mode, reload } = useApi<Mode>("/control/mode", 15000);
  const groups = [...new Set(NAV.map((n) => n.group))];

  return (
    <div className="min-h-screen">
      <div className="bg-orbs" aria-hidden />
      <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-[#07070b]/80 backdrop-blur-xl">
        <div className="flex items-center gap-3 px-4 py-3 lg:px-6">
          <button className="rounded-lg border border-white/10 px-2.5 py-1.5 text-zinc-300 lg:hidden" onClick={() => setOpen(!open)} aria-label="Menu">☰</button>
          <Link href="/" className="flex items-center gap-2.5">
            <span className="logo-mark grid h-8 w-8 place-items-center rounded-lg font-display text-sm font-black text-white">VI</span>
            <span className="font-display text-sm font-bold tracking-wide text-white">Media Engine</span>
          </Link>
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            {mode && (
              <>
                <Badge tone={mode.mode === "autonomous" ? "brand" : "warn"}>{mode.mode === "autonomous" ? "AUTONOMOUS" : "TEST MODE"}</Badge>
                {mode.publishing_paused ? <Badge tone="warn">publishing paused</Badge> : <Badge tone="good">publishing live</Badge>}
                {mode.processing_paused && <Badge tone="bad">processing paused</Badge>}
                {!mode.publishing_paused ? (
                  <ActionButton path="/control/pause-publishing" label="Emergency pause" variant="danger" done="Publishing paused" onDone={reload} />
                ) : null}
              </>
            )}
          </div>
        </div>
      </header>
      <div className="flex">
        <nav className={`${open ? "block" : "hidden"} fixed inset-y-0 left-0 top-[57px] z-20 w-64 overflow-y-auto border-r border-white/[0.06] bg-[#08080d]/95 px-3 py-5 backdrop-blur-xl lg:sticky lg:block lg:h-[calc(100vh-57px)]`}>
          {groups.map((g) => (
            <div key={g} className="mb-5">
              <div className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-600">{g}</div>
              {NAV.filter((n) => n.group === g).map((n) => {
                const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
                return (
                  <Link key={n.href} href={n.href} onClick={() => setOpen(false)}
                    className={`nav-link relative block rounded-lg px-3 py-2 text-sm transition ${active ? "nav-active text-white" : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100"}`}>
                    {n.label}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
        <main className="page-in min-w-0 flex-1 px-4 py-6 lg:px-8 lg:py-8">{children}</main>
      </div>
    </div>
  );
}
