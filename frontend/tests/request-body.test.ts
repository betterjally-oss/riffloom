import { describe, expect, it } from "vitest";

import {
  readRequestBodyWithLimit,
  RequestBodyTooLargeError,
} from "@/lib/request-body";


function streamedRequest(chunks: Uint8Array[], contentLength?: string) {
  const headers = new Headers();
  if (contentLength !== undefined) headers.set("content-length", contentLength);
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
  return { headers, body };
}


describe("同源代理请求体边界", () => {
  it("读取上限内的分块请求", async () => {
    const body = await readRequestBodyWithLimit(
      streamedRequest([new Uint8Array([1, 2]), new Uint8Array([3])]),
      3,
    );
    expect(body).toBeInstanceOf(ArrayBuffer);
    if (!body) throw new Error("expected request body");
    expect(Array.from(new Uint8Array(body))).toEqual([1, 2, 3]);
  });

  it("在读取未知长度分块流时实施硬上限", async () => {
    await expect(
      readRequestBodyWithLimit(
        streamedRequest([new Uint8Array(600), new Uint8Array(600)]),
        1024,
      ),
    ).rejects.toBeInstanceOf(RequestBodyTooLargeError);
  });

  it("在读取正文前拒绝过大的 Content-Length", async () => {
    await expect(
      readRequestBodyWithLimit(streamedRequest([], "2048"), 1024),
    ).rejects.toMatchObject({ maxBodyBytes: 1024 });
  });
});
