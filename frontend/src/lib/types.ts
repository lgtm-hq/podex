/** A podcast source in the catalog. */
export interface Podcast {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  created_at: string;
}
