import type { Podcast } from "./types";

/**
 * One entry in the sitemap. `path` is a leading-slash absolute path on the
 * site; `lastmod` is an optional ISO-8601 timestamp; `changefreq` and
 * `priority` are the standard sitemap hints.
 */
export interface SitemapEntry {
  path: string;
  lastmod?: string;
  changefreq?:
    | "always"
    | "hourly"
    | "daily"
    | "weekly"
    | "monthly"
    | "yearly"
    | "never";
  priority?: number;
}

const XML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&apos;",
};

function escapeXml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => XML_ESCAPES[char] ?? char);
}

function joinUrl(siteUrl: string, path: string): string {
  const origin = siteUrl.replace(/\/+$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${origin}${suffix}`;
}

/**
 * Build the entry list for a Podex sitemap: the home page, the two legal
 * pages, and one entry per podcast (by numeric id, matching
 * `/podcasts/[id].astro`).
 *
 * `lastmod` values are optional — home + legal pages use the provided
 * `staticLastmod`, and podcast entries use `podcast.created_at` if present.
 */
export function buildSitemapEntries(
  podcasts: Podcast[],
  options: { staticLastmod?: string } = {},
): SitemapEntry[] {
  const staticLastmod = options.staticLastmod;
  const entries: SitemapEntry[] = [
    {
      path: "/",
      lastmod: staticLastmod,
      changefreq: "daily",
      priority: 1.0,
    },
    {
      path: "/legal/terms",
      lastmod: staticLastmod,
      changefreq: "yearly",
      priority: 0.3,
    },
    {
      path: "/legal/privacy",
      lastmod: staticLastmod,
      changefreq: "yearly",
      priority: 0.3,
    },
  ];

  for (const podcast of podcasts) {
    entries.push({
      path: `/podcasts/${podcast.id}`,
      lastmod: podcast.created_at,
      changefreq: "weekly",
      priority: 0.7,
    });
  }

  return entries;
}

/**
 * Render a sitemap XML document. Deterministic — same inputs give the same
 * bytes, which makes it easy to unit-test the endpoint.
 */
export function renderSitemapXml(
  entries: SitemapEntry[],
  siteUrl: string,
): string {
  const body = entries
    .map((entry) => {
      const parts = [
        `    <loc>${escapeXml(joinUrl(siteUrl, entry.path))}</loc>`,
      ];
      if (entry.lastmod) {
        parts.push(`    <lastmod>${escapeXml(entry.lastmod)}</lastmod>`);
      }
      if (entry.changefreq) {
        parts.push(`    <changefreq>${entry.changefreq}</changefreq>`);
      }
      if (typeof entry.priority === "number") {
        parts.push(`    <priority>${entry.priority.toFixed(1)}</priority>`);
      }
      return `  <url>\n${parts.join("\n")}\n  </url>`;
    })
    .join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${body}
</urlset>
`;
}
