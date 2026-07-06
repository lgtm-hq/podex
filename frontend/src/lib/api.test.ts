import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchPodcasts } from "./api";
import type { Podcast } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("fetchPodcasts", () => {
  it("returns the parsed podcast list", async () => {
    const podcasts: Podcast[] = [
      {
        id: 1,
        name: "The Example Show",
        slug: "example-show",
        description: null,
        created_at: "2026-01-01T00:00:00Z",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify(podcasts), { status: 200 }),
      ),
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
