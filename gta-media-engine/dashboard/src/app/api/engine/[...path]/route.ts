/**
 * Server-side proxy to the engine API.
 * The admin token lives only in the dashboard server's environment (ENGINE_ADMIN_TOKEN)
 * and is attached here, so it is never shipped to the browser.
 */
import { NextRequest } from "next/server";

const API = (process.env.ENGINE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const TOKEN = process.env.ENGINE_ADMIN_TOKEN ?? "";
const SAFE_SEGMENT = /^[A-Za-z0-9_.-]+$/;

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (!path.every((p) => SAFE_SEGMENT.test(p) && p !== "..")) {
    return Response.json({ detail: "invalid path" }, { status: 400 });
  }
  if (!TOKEN) {
    return Response.json({ detail: "ENGINE_ADMIN_TOKEN is not configured on the dashboard server" }, { status: 500 });
  }
  const url = `${API}/api/${path.join("/")}${req.nextUrl.search}`;
  const headers = new Headers({ Authorization: `Bearer ${TOKEN}` });
  const type = req.headers.get("content-type");
  if (type) headers.set("content-type", type);
  const range = req.headers.get("range");
  if (range) headers.set("range", range);

  const init: RequestInit & { duplex?: string } = { method: req.method, headers, cache: "no-store" };
  if (!["GET", "HEAD"].includes(req.method)) {
    init.body = req.body;
    init.duplex = "half";
  }
  let upstream: Response;
  try {
    upstream = await fetch(url, init);
  } catch {
    return Response.json({ detail: "engine API unreachable" }, { status: 502 });
  }
  const out = new Headers();
  for (const h of ["content-type", "content-length", "content-range", "accept-ranges", "retry-after"]) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
export const dynamic = "force-dynamic";
