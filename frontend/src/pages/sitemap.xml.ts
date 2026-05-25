import type { APIRoute } from "astro";
import { escape } from "html-escaper";
import { getCollections, getEpisodes, getMedia, getPodcasts } from "../lib/api";

interface SitemapEntry {
  path: string;
  lastModified?: string;
}

export const GET: APIRoute = async ({ url }) => {
  const publicOrigin = (import.meta.env.PUBLIC_WEB_URL || url.origin).replace(/\/$/, "");
  const entries: SitemapEntry[] = [
    { path: "/" },
    { path: "/search" },
    { path: "/media" },
    { path: "/episodes" },
    { path: "/sources" },
    { path: "/collections" },
    { path: "/stats" },
    { path: "/pricing" },
    { path: "/legal/privacy" },
    { path: "/legal/terms" },
  ];

  try {
    const [podcasts, media, episodes, collections] = await Promise.all([
      getPodcasts(),
      getMedia(1, 100),
      getEpisodes(1, 100),
      getCollections(),
    ]);
    entries.push(
      ...podcasts.map((podcast) => ({ path: `/sources/${podcast.slug}` })),
      ...media.items.map((item) => ({
        path: `/media/${item.id}`,
        lastModified: item.created_at,
      })),
      ...episodes.items.map((episode) => ({
        path: `/episodes/${episode.id}`,
        lastModified: episode.created_at,
      })),
      ...collections.map((collection) => ({
        path: `/collections/${collection.slug}`,
        lastModified: collection.updated_at,
      })),
    );
  } catch (error) {
    console.error("Unable to include dynamic records in sitemap:", error);
  }

  const urls = entries
    .map((entry) => {
      const location = escape(`${publicOrigin}${entry.path}`);
      const modified = entry.lastModified
        ? `<lastmod>${escape(entry.lastModified.slice(0, 10))}</lastmod>`
        : "";
      return `<url><loc>${location}</loc>${modified}</url>`;
    })
    .join("");
  const body = `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls}</urlset>`;

  return new Response(body, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
};
