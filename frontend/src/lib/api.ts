import { API_BASE_URL } from "./config";
import type { Podcast } from "./types";

/** Fetch the list of podcasts from the Podex API. */
export async function fetchPodcasts(): Promise<Podcast[]> {
  const response = await fetch(`${API_BASE_URL}/podcasts`);
  if (!response.ok) {
    throw new Error(`Failed to fetch podcasts: ${response.status}`);
  }
  return (await response.json()) as Podcast[];
}
