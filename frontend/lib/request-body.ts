export const DEFAULT_PROXY_MAX_BODY_BYTES = 24 * 1024 * 1024;

export class RequestBodyTooLargeError extends Error {
  constructor(readonly maxBodyBytes: number) {
    super("request body exceeds configured limit");
    this.name = "RequestBodyTooLargeError";
  }
}

export function proxyMaxBodyBytes() {
  const raw = process.env.RIFFLOOM_PROXY_MAX_BODY_BYTES;
  const value = raw ? Number(raw) : DEFAULT_PROXY_MAX_BODY_BYTES;
  if (!Number.isSafeInteger(value) || value < 1024 || value > 32 * 1024 * 1024) {
    throw new Error("RIFFLOOM_PROXY_MAX_BODY_BYTES 必须在 1024 到 33554432 之间");
  }
  return value;
}

type BodyRequest = {
  headers: Pick<Headers, "get">;
  body: ReadableStream<Uint8Array> | null;
};

export async function readRequestBodyWithLimit(
  request: BodyRequest,
  maxBodyBytes = proxyMaxBodyBytes(),
) {
  const declaredHeader = request.headers.get("content-length");
  if (declaredHeader !== null) {
    const declared = Number(declaredHeader);
    if (!Number.isSafeInteger(declared) || declared < 0 || declared > maxBodyBytes) {
      throw new RequestBodyTooLargeError(maxBodyBytes);
    }
  }
  if (!request.body) return undefined;

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      received += value.byteLength;
      if (received > maxBodyBytes) {
        await reader.cancel();
        throw new RequestBodyTooLargeError(maxBodyBytes);
      }
      chunks.push(Uint8Array.from(value));
    }
  } finally {
    reader.releaseLock();
  }

  const combined = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return combined.buffer;
}
