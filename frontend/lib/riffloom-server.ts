import "server-only";

import type { NextRequest } from "next/server";

export const RIFFLOOM_SESSION_COOKIE = "riffloom_session";

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8100/api/v1";

export function riffloomApiUrl(path: string, search = "") {
  const configured = process.env.RIFFLOOM_API_BASE_URL;
  if (!configured && process.env.NODE_ENV === "production") {
    throw new Error("生产环境必须配置 RIFFLOOM_API_BASE_URL");
  }
  const baseUrl = configured ?? DEFAULT_API_BASE_URL;
  const base = new URL(baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password) {
    throw new Error("RIFFLOOM_API_BASE_URL 必须是无凭据的 http(s) 地址");
  }
  const safePath = path
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  const target = new URL(safePath, base);
  target.search = search;
  return target;
}

export function requestHasSafeOrigin(request: NextRequest) {
  if (["GET", "HEAD", "OPTIONS"].includes(request.method)) return true;
  const origin = request.headers.get("origin");
  const configuredOrigin = process.env.RIFFLOOM_PUBLIC_ORIGIN;
  if (configuredOrigin) {
    if (!origin) return false;
    try {
      return new URL(origin).origin === new URL(configuredOrigin).origin;
    } catch {
      return false;
    }
  }
  if (process.env.NODE_ENV === "production" || !origin) return false;
  try {
    const allowedHosts = new Set([
      request.nextUrl.host,
      request.headers.get("host"),
    ].filter(Boolean));
    return allowedHosts.has(new URL(origin).host);
  } catch {
    return false;
  }
}

export function upstreamRequestHeaders(
  request: NextRequest,
  accessToken?: string,
) {
  const headers = new Headers();
  for (const name of ["accept", "content-type", "idempotency-key", "x-request-id", "x-trace-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

  // These server-side values preserve the local demo workflow.  The production
  // invite_token mode ignores both headers and trusts only the bearer session.
  const demoUser = process.env.RIFFLOOM_DEMO_USER;
  const demoWorkspace = process.env.RIFFLOOM_DEMO_WORKSPACE;
  if (demoUser) headers.set("x-riffloom-user", demoUser);
  if (demoWorkspace) headers.set("x-riffloom-workspace", demoWorkspace);
  return headers;
}

export function safeResponseHeaders(upstream: Response) {
  const headers = new Headers();
  for (const name of ["content-type", "content-length", "content-disposition", "location", "x-request-id", "x-trace-id"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set("cache-control", "no-store");
  return headers;
}

export function upstreamUnavailable() {
  return Response.json(
    {
      error: {
        code: "API_UNREACHABLE",
        message: "Riffloom API 暂时无法连接",
        retryable: true,
        request_id: null,
        trace_id: null,
        details: {},
      },
    },
    { status: 502, headers: { "cache-control": "no-store" } },
  );
}
