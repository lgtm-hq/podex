import type { APIRoute } from "astro";

import { SITE_URL } from "../lib/site";

/**
 * Build the body of `/robots.txt`.
 *
 * Exposed as a pure function so unit tests can exercise the exact bytes we
 * serve without spinning up an Astro request context.
 */
export function buildRobotsTxt(siteUrl: string = SITE_URL): string {
  return [
    "User-agent: *",
    "Allow: /",
    "Disallow: /api/",
    "",
    `Sitemap: ${siteUrl.replace(/\/+$/, "")}/sitemap.xml`,
    "",
  ].join("\n");
}

export const GET: APIRoute = () =>
  new Response(buildRobotsTxt(), {
    status: 200,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
