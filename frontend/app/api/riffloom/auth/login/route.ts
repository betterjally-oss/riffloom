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

type RedeemResponse = {
  access_token: string;
  expires_at: string;
  session: {
    user_id: string;
    user_name: string;
    workspace_id: string;
    workspace_name: string;
    role: string;
    auth_mode: "invite_token";
  };
};

export async function POST(request: NextRequest) {
  if (!requestHasSafeOrigin(request)) {
    return Response.json(
      { error: { code: "ORIGIN_FORBIDDEN", message: "请求来源不受信任", retryable: false } },
      { status: 403 },
    );
  }
  let invitationCode = "";
  try {
    const encoded = await readRequestBodyWithLimit(request, 1024);
    const body = JSON.parse(
      new TextDecoder().decode(encoded),
    ) as { invitation_code?: unknown };
    invitationCode = typeof body.invitation_code === "string" ? body.invitation_code.trim() : "";
  } catch (error) {
    if (error instanceof RequestBodyTooLargeError) {
      return Response.json(
        { error: { code: "PAYLOAD_TOO_LARGE", message: "邀请码请求过大", retryable: false } },
        { status: 413, headers: { "cache-control": "no-store" } },
      );
    }
    // Return the same local validation envelope for malformed JSON and values.
  }
  if (invitationCode.length < 12 || invitationCode.length > 200) {
    return Response.json(
      { error: { code: "VALIDATION_ERROR", message: "请输入有效的邀请码", retryable: false } },
      { status: 422 },
    );
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
  try {
    const upstream = await fetch(riffloomApiUrl("auth/invitations/redeem"), {
      method: "POST",
      headers: upstreamRequestHeaders(request),
      body: JSON.stringify({ invitation_code: invitationCode }),
      cache: "no-store",
      signal: controller.signal,
    });
    if (!upstream.ok) {
      return new Response(upstream.body, {
        status: upstream.status,
        headers: safeResponseHeaders(upstream),
      });
    }
    const payload = await upstream.json() as RedeemResponse;
    const expires = new Date(payload.expires_at);
    if (!payload.access_token?.startsWith("rfs_") || Number.isNaN(expires.valueOf())) {
      return upstreamUnavailable();
    }
    const response = NextResponse.json(
      { session: payload.session, expires_at: payload.expires_at },
      { headers: { "cache-control": "no-store" } },
    );
    response.cookies.set(RIFFLOOM_SESSION_COOKIE, payload.access_token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      expires,
    });
    return response;
  } catch {
    return upstreamUnavailable();
  } finally {
    clearTimeout(timeout);
  }
}
