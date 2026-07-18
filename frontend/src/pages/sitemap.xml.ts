import type { APIRoute } from "astro";

import { fetchPodcasts } from "../lib/api";
import { SITE_URL } from "../lib/site";
import { buildSitemapEntries, renderSitemapXml } from "../lib/sitemap";
import type { Podcast } from "../lib/types";

export const GET: APIRoute = async () => {
  let podcasts: Podcast[] = [];
  try {
    podcasts = await fetchPodcasts();
  } catch {
    /*
     * Degrade to a "static-only" sitemap when the backend is unavailable.
     * A partial sitemap is more useful for crawlers than a 5xx response.
     */
    podcasts = [];
  }

  const entries = buildSitemapEntries(podcasts, {
    staticLastmod: new Date().toISOString(),
  });
  const xml = renderSitemapXml(entries, SITE_URL);

  return new Response(xml, {
    status: 200,
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=600",
    },
  });
};
