import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyOpsRetention,
  clearOpsKey,
  createOpsPodcast,
  getOpsKey,
  getOpsMetrics,
  getOpsPodcasts,
  OpsApiError,
  setOpsKey,
} from "./ops";

function mockFetch(status: number, payload: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ops api client", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stores and clears the ops key in session storage", () => {
    expect(getOpsKey()).toBeNull();
    setOpsKey("secret");
    expect(getOpsKey()).toBe("secret");
    clearOpsKey();
    expect(getOpsKey()).toBeNull();
  });

  it("sends the ops key header on requests", async () => {
    setOpsKey("secret");
    const fetchMock = mockFetch(200, { review: {}, alerts: {} });

    await getOpsMetrics();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/ops/metrics");
    expect((init.headers as Record<string, string>)["X-Ops-Key"]).toBe("secret");
  });

  it("serializes list filters as query parameters", async () => {
    const fetchMock = mockFetch(200, { items: [], total: 0 });

    await getOpsPodcasts({ status: "active", sort: "name" });

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain("/ops/podcasts?");
    expect(url).toContain("status=active");
    expect(url).toContain("sort=name");
  });

  it("posts JSON bodies with a content type", async () => {
    const fetchMock = mockFetch(201, { id: 1 });

    await createOpsPodcast({
      name: "Show",
      slug: "show",
      status: "active",
      sources: {},
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
    expect(JSON.parse(init.body as string).slug).toBe("show");
  });

  it("raises a typed error carrying the response status", async () => {
    mockFetch(401, {});

    await expect(applyOpsRetention(1)).rejects.toMatchObject({
      status: 401,
    });
    await expect(applyOpsRetention(1)).rejects.toBeInstanceOf(OpsApiError);
  });
});
