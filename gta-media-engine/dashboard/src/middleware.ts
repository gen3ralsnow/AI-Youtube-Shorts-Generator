/**
 * HTTP Basic auth for the whole dashboard (pages and the API proxy).
 * Set DASHBOARD_USER and DASHBOARD_PASSWORD. In production, missing credentials deny all access.
 */
import { NextRequest, NextResponse } from "next/server";

export function middleware(req: NextRequest) {
  const user = process.env.DASHBOARD_USER ?? "";
  const pass = process.env.DASHBOARD_PASSWORD ?? "";
  if (!user || !pass) {
    if (process.env.NODE_ENV === "production") {
      return new NextResponse("Dashboard credentials are not configured.", { status: 503 });
    }
    return NextResponse.next();
  }
  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    const [u, ...rest] = atob(header.slice(6)).split(":");
    if (timingSafeEqual(u, user) && timingSafeEqual(rest.join(":"), pass)) return NextResponse.next();
  }
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="GTA VI Media Engine"' },
  });
}

function timingSafeEqual(a: string, b: string) {
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
