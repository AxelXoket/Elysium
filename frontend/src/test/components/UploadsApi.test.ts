/**
 * UploadsApi.test.ts - lib/api/uploads behavior.
 *
 * Covers:
 *  - uploadImage: multipart POST (FormData, field name "file", no manual
 *    Content-Type header), success parsing
 *  - error normalization to the ApiError shape ({status, detail, message})
 *    for backend codes, network failures, and malformed response bodies
 *  - imageUrl: binary URL construction on the shared API base
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { uploadImage, imageUrl, UploadedImageSchema } from "@/lib/api/uploads";
import { isApiError } from "@/lib/api/client";

const uploadedFixture = {
  id: 42,
  mime: "image/png",
  width: 640,
  height: 480,
  byte_size: 123456,
};

function pngFile(name = "photo.png"): File {
  return new File([new Uint8Array([137, 80, 78, 71])], name, {
    type: "image/png",
  });
}

function stubFetch(handler: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> | Response) {
  const mock = vi.fn(handler);
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("uploads API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("POSTs multipart form data with field name 'file' and no manual Content-Type", async () => {
    const mock = stubFetch(
      async () =>
        new Response(JSON.stringify(uploadedFixture), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
    );

    const file = pngFile();
    const result = await uploadImage(file);

    expect(result).toEqual(uploadedFixture);
    expect(mock).toHaveBeenCalledTimes(1);

    const [url, init] = mock.mock.calls[0];
    expect(String(url)).toBe("http://127.0.0.1:8787/api/v1/uploads/images");
    expect(init?.method).toBe("POST");
    // fetch must derive the multipart boundary itself
    expect(init?.headers).toBeUndefined();
    expect(init?.body).toBeInstanceOf(FormData);
    const sent = (init?.body as FormData).get("file");
    expect(sent).toBeInstanceOf(File);
    expect((sent as File).name).toBe("photo.png");
    expect((sent as File).type).toBe("image/png");
  });

  it("normalizes backend error codes into the ApiError shape", async () => {
    stubFetch(
      async () =>
        new Response(JSON.stringify({ detail: "attachment_too_large" }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
    );

    const err = await uploadImage(pngFile()).catch((e: unknown) => e);
    expect(isApiError(err)).toBe(true);
    expect(err).toMatchObject({
      status: 400,
      detail: "attachment_too_large",
      message: "This image is too large. Please use an image under 10 MB.",
    });
  });

  it("normalizes attachment_invalid with its mapped message", async () => {
    stubFetch(
      async () =>
        new Response(JSON.stringify({ detail: "attachment_invalid" }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
    );

    const err = await uploadImage(pngFile()).catch((e: unknown) => e);
    expect(err).toMatchObject({
      status: 400,
      detail: "attachment_invalid",
      message: "This image cannot be used. Please choose a PNG, JPEG, or WebP file.",
    });
  });

  it("falls back to unknown_error when the error body has no detail code", async () => {
    stubFetch(async () => new Response("oops", { status: 500 }));

    const err = await uploadImage(pngFile()).catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 500, detail: "unknown_error" });
  });

  it("normalizes network failures to network_error with status 0", async () => {
    stubFetch(async () => {
      throw new TypeError("Failed to fetch");
    });

    const err = await uploadImage(pngFile()).catch((e: unknown) => e);
    expect(isApiError(err)).toBe(true);
    expect(err).toMatchObject({
      status: 0,
      detail: "network_error",
      message: "Could not reach the server. Please check your connection.",
    });
  });

  it("normalizes a malformed success body to invalid_response_shape with the real status", async () => {
    stubFetch(
      async () =>
        new Response(JSON.stringify({ nope: true }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
    );

    const err = await uploadImage(pngFile()).catch((e: unknown) => e);
    expect(err).toMatchObject({
      status: 201,
      detail: "invalid_response_shape",
      message: "Unexpected response format from server.",
    });
  });

  it("UploadedImageSchema matches the contract 201 body exactly", () => {
    expect(UploadedImageSchema.parse(uploadedFixture)).toEqual(uploadedFixture);
    expect(UploadedImageSchema.safeParse({ id: 1 }).success).toBe(false);
  });

  it("imageUrl builds the binary URL on the shared API base", () => {
    expect(imageUrl(7)).toBe("http://127.0.0.1:8787/api/v1/uploads/images/7");
  });
});
