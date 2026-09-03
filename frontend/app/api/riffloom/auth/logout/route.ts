import { type NextRequest, NextResponse } from "next/server";

import {
  RIFFLOOM_SESSION_COOKIE,
  requestHasSafeOrigin,
  riffloomApiUrl,
  safeResponseHeaders,
  upstreamRequestHeaders,
} from "@/lib/riffloom-server";

export async function POST(request: NextRequest) {
  if (!requestHasSafeOrigin(request)) {
    return Response.json(
      { error: { code: "ORIGIN_FORBIDDEN", message: "请求来源不受信任", retryable: false } },
      { status: 403 },
    );
  }
  const accessToken = request.cookies.get(RIFFLOOM_SESSION_COOKIE)?.value;
  let upstreamBody: ReadableStream<Uint8Array> | null = null;
  let status = 200;
  let headers = new Headers({ "content-type": "application/json", "cache-control": "no-store" });
  if (accessToken) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    try {
      const upstream = await fetch(riffloomApiUrl("auth/logout"), {
        method: "POST",
        headers: upstreamRequestHeaders(request, accessToken),
        cache: "no-store",
        signal: controller.signal,
      });
      upstreamBody = upstream.body;
      status = upstream.status;
      headers = safeResponseHeaders(upstream);
    } catch {
      // Local sign-out must still clear the browser credential if the backend
      // is temporarily unavailable.  The server-side session expires normally.
      upstreamBody = null;
    } finally {
      clearTimeout(timeout);
    }
  }
  const response = upstreamBody
    ? new NextResponse(upstreamBody, { status, headers })
    : NextResponse.json({ status: "signed_out" }, { headers });
  response.cookies.delete(RIFFLOOM_SESSION_COOKIE);
  return response;
}
