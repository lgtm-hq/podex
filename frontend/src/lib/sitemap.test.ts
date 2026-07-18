import { describe, expect, it } from "vitest";

import { buildSitemapEntries, renderSitemapXml } from "./sitemap";
import type { Podcast } from "./types";

function makePodcast(overrides: Partial<Podcast> = {}): Podcast {
  return {
    id: 1,
    name: "Example Show",
    slug: "example-show",
    description: null,
    created_at: "2026-01-15T00:00:00Z",
    public_id: "pod_ae",
    ...overrides,
  };
}

describe("buildSitemapEntries", () => {
  it("always includes the home page", () => {
    const entries = buildSitemapEntries([]);
    const paths = entries.map((entry) => entry.path);
    expect(paths).toEqual(["/"]);
  });

  it("appends one entry per podcast, keyed by id", () => {
    const entries = buildSitemapEntries([
      makePodcast({ id: 7, name: "Seven" }),
      makePodcast({ id: 42, name: "Forty two" }),
    ]);
    const podcastPaths = entries
      .map((entry) => entry.path)
      .filter((path) => path.startsWith("/podcasts/"));
    expect(podcastPaths).toEqual(["/podcasts/7", "/podcasts/42"]);
  });

  it("uses podcast.created_at as lastmod for podcast entries", () => {
    const [, podcastEntry] = buildSitemapEntries([
      makePodcast({ id: 3, created_at: "2026-05-10T12:34:56Z" }),
    ]);
    expect(podcastEntry).toBeDefined();
    expect(podcastEntry!.lastmod).toBe("2026-05-10T12:34:56Z");
  });

  it("applies staticLastmod only to static routes", () => {
    const entries = buildSitemapEntries(
      [makePodcast({ id: 1, created_at: "2026-01-15T00:00:00Z" })],
      { staticLastmod: "2026-07-18T00:00:00Z" },
    );
    expect(entries[0]!.lastmod).toBe("2026-07-18T00:00:00Z");
    expect(entries[1]!.lastmod).toBe("2026-01-15T00:00:00Z");
  });
});

describe("renderSitemapXml", () => {
  it("emits a valid urlset with the schema xmlns", () => {
    const xml = renderSitemapXml(
      [{ path: "/", changefreq: "daily", priority: 1.0 }],
      "https://example.test",
    );
    expect(xml).toContain('<?xml version="1.0" encoding="UTF-8"?>');
    expect(xml).toContain(
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    );
    expect(xml).toContain("<loc>https://example.test/</loc>");
    expect(xml).toContain("<changefreq>daily</changefreq>");
    expect(xml).toContain("<priority>1.0</priority>");
  });

  it("joins paths cleanly regardless of trailing slash on site URL", () => {
    const xml = renderSitemapXml(
      [{ path: "/legal/terms" }],
      "https://example.test/",
    );
    expect(xml).toContain("<loc>https://example.test/legal/terms</loc>");
    expect(xml).not.toContain("example.test//legal");
  });

  it("escapes XML-special characters in URLs", () => {
    const xml = renderSitemapXml(
      [{ path: "/podcasts/1?q=a&b=c" }],
      "https://example.test",
    );
    expect(xml).toContain("q=a&amp;b=c");
    expect(xml).not.toContain("q=a&b=c");
  });

  it("omits optional fields when not provided", () => {
    const xml = renderSitemapXml([{ path: "/" }], "https://example.test");
    expect(xml).not.toContain("<lastmod>");
    expect(xml).not.toContain("<changefreq>");
    expect(xml).not.toContain("<priority>");
  });
});
