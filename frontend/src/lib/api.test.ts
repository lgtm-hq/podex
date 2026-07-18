import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchPodcastPage, fetchPodcasts } from "./api";
import type { Podcast, PodcastPage } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
});

function buildPodcast(overrides: Partial<Podcast> = {}): Podcast {
  return {
    id: 1,
    name: "The Example Show",
    slug: "example-show",
    description: null,
    created_at: "2026-01-01T00:00:00Z",
    public_id: "pod_ae",
    ...overrides,
  };
}

describe("fetchPodcastPage", () => {
  it("returns the parsed page envelope", async () => {
    const page: PodcastPage = {
      items: [buildPodcast()],
      total: 1,
      limit: 50,
      offset: 0,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(page), { status: 200 })),
    );

    await expect(fetchPodcastPage()).resolves.toEqual(page);
  });

  it("passes limit and offset as query params", async () => {
    let capturedUrl = "";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        capturedUrl = url;
        return new Response(
          JSON.stringify({ items: [], total: 0, limit: 5, offset: 10 }),
          { status: 200 },
        );
      }),
    );

    await fetchPodcastPage({ limit: 5, offset: 10 });

    expect(capturedUrl).toContain("limit=5");
    expect(capturedUrl).toContain("offset=10");
  });

  it("throws on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 500 })),
    );

    await expect(fetchPodcastPage()).rejects.toThrow(/500/);
  });
});

describe("fetchPodcasts", () => {
  it("returns just the items from the first page", async () => {
    const podcasts: Podcast[] = [buildPodcast()];
    const page: PodcastPage = {
      items: podcasts,
      total: podcasts.length,
      limit: 50,
      offset: 0,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(page), { status: 200 })),
    );

    await expect(fetchPodcasts()).resolves.toEqual(podcasts);
  });

  it("throws on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 500 })),
    );

    await expect(fetchPodcasts()).rejects.toThrow(/500/);
  });
});
