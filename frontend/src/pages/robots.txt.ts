import type { APIRoute } from "astro";

export const GET: APIRoute = ({ url }) => {
  const publicOrigin = (import.meta.env.PUBLIC_WEB_URL || url.origin).replace(/\/$/, "");
  const body = [
    "User-agent: *",
    "Allow: /",
    "Disallow: /account/",
    "Disallow: /ops/",
    `Sitemap: ${publicOrigin}/sitemap.xml`,
    "",
  ].join("\n");

  return new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
