import { type NextRequest, NextResponse } from "next/server";

import {
  RIFFLOOM_SESSION_COOKIE,
  requestHasSafeOrigin,
  riffloomApiUrl,
  safeResponseHeaders,
  upstreamRequestHeaders,
  upstreamUnavailable,
} from "@/lib/riffloom-server";
import {
  readRequestBodyWithLimit,
  RequestBodyTooLargeError,
} from "@/lib/request-body";

type ProxyContext = { params: Promise<{ path: string[] }> };

async function proxyRequest(request: NextRequest, context: ProxyContext) {
  const { path } = await context.params;
  // Authentication responses contain a one-time raw bearer token.  They may
  // only pass through the dedicated login/logout handlers below, never this
  // generic browser-visible proxy.
  if (path[0] === "auth") {
    return Response.json(
      { error: { code: "ROUTE_NOT_FOUND", message: "接口不存在", retryable: false } },
      { status: 404 },
    );
  }
  if (!requestHasSafeOrigin(request)) {
    return Response.json(
      { error: { code: "ORIGIN_FORBIDDEN", message: "请求来源不受信任", retryable: false } },
      { status: 403 },
    );
  }

  const accessToken = request.cookies.get(RIFFLOOM_SESSION_COOKIE)?.value;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);
  try {
    const hasBody = !["GET", "HEAD"].includes(request.method);
    const body = hasBody ? await readRequestBodyWithLimit(request) : undefined;
    const upstream = await fetch(riffloomApiUrl(path.join("/"), request.nextUrl.search), {
      method: request.method,
      headers: upstreamRequestHeaders(request, accessToken),
      body,
      cache: "no-store",
      redirect: "manual",
      signal: controller.signal,
    });
    const response = new NextResponse(upstream.body, {
      status: upstream.status,
      headers: safeResponseHeaders(upstream),
    });
    if (upstream.status === 401 && accessToken) {
      response.cookies.delete(RIFFLOOM_SESSION_COOKIE);
    }
    return response;
  } catch (error) {
    if (error instanceof RequestBodyTooLargeError) {
      return Response.json(
        {
          error: {
            code: "PAYLOAD_TOO_LARGE",
            message: "请求体超过服务允许的大小",
            retryable: false,
            request_id: null,
            trace_id: null,
            details: { max_body_bytes: error.maxBodyBytes },
          },
        },
        { status: 413, headers: { "cache-control": "no-store" } },
      );
    }
    return upstreamUnavailable();
  } finally {
    clearTimeout(timeout);
  }
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const PATCH = proxyRequest;
export const DELETE = proxyRequest;
