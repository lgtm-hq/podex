/**
 * Site-wide constants used for SEO, structured data, and legal pages.
 *
 * Keeping these here (rather than in `astro.config`) lets endpoints and layout
 * components share the same source of truth without importing the config.
 */

/** Default site name and wordmark. */
export const SITE_NAME = "Podex";

/** Short tagline used in the default title/description meta tags. */
export const SITE_TAGLINE = "Discover podcasts worth your time.";

/**
 * Long description used for OpenGraph, Twitter cards, and JSON-LD.
 *
 * Written in one sentence to keep meta descriptions under ~160 chars.
 */
export const SITE_DESCRIPTION =
  "Podex is a curated podcast discovery site. Browse shows, follow episodes, and find the mentions that matter.";

/**
 * Canonical public origin for the site. Overridable via `PUBLIC_SITE_URL`
 * so preview/staging deploys can emit correct canonical + sitemap URLs.
 * The value never has a trailing slash.
 */
export const SITE_URL: string = (
  import.meta.env.PUBLIC_SITE_URL ?? "http://localhost:4321"
).replace(/\/+$/, "");

/**
 * Legal document version + effective date shared by the Terms and Privacy
 * pages. Bump the version and effective date together whenever the policy
 * content materially changes; the same identifiers should be persisted with
 * per-user acceptance records once #82 lands.
 *
 * Content is currently marked as draft pending legal review (#82).
 */
export const LEGAL_VERSION = "0.1.0-draft";
export const LEGAL_EFFECTIVE_DATE = "2026-07-18";

/**
 * Absolute URL helper. Guarantees exactly one slash between origin and path.
 */
export function absoluteUrl(path: string): string {
  const normalised = path.startsWith("/") ? path : `/${path}`;
  return `${SITE_URL}${normalised}`;
}
