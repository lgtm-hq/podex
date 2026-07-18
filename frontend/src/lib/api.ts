import { API_BASE_URL } from "./config";
import type { Podcast, PodcastPage } from "./types";

/**
 * Fetch a page of podcasts from the Podex API.
 *
 * @param params - Optional pagination overrides. ``limit`` clamps to the
 *   backend's maximum page size; ``offset`` skips that many items.
 * @returns The parsed page envelope containing items plus paging metadata.
 * @throws {Error} When the API responds with a non-OK status.
 */
export async function fetchPodcastPage(
  params: { limit?: number; offset?: number } = {},
): Promise<PodcastPage> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const query = search.toString();
  const url = `${API_BASE_URL}/podcasts${query ? `?${query}` : ""}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch podcasts: ${response.status}`);
  }
  return (await response.json()) as PodcastPage;
}

/**
 * Convenience wrapper that returns only the items from the first page.
 *
 * Prefer :func:`fetchPodcastPage` when the caller needs paging metadata.
 */
export async function fetchPodcasts(): Promise<Podcast[]> {
  const page = await fetchPodcastPage();
  return page.items;
}

/**
 * Fetch a single podcast by its integer id. Resolves to `null` when the API
 * returns 404 so callers can render a proper "not found" state; other
 * non-2xx responses throw so upstream error handling can decide how loud to
 * be.
 */
export async function fetchPodcast(id: number): Promise<Podcast | null> {
  const response = await fetch(`${API_BASE_URL}/podcasts/${id}`);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`Failed to fetch podcast ${id}: ${response.status}`);
  }
  return (await response.json()) as Podcast;
}
