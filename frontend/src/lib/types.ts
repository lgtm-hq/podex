import type { components } from "./types.gen";

/** A podcast source in the catalog. */
export type Podcast = components["schemas"]["PodcastRead"];

/** A single page of podcasts returned by the v2 list endpoint. */
export type PodcastPage = components["schemas"]["Page_PodcastRead_"];

/** The uniform error envelope returned by ``/api/v2`` failures. */
export type ApiError = {
  code: string;
  message: string;
  request_id?: string | null;
  details?: {
    loc: (string | number)[];
    msg: string;
    type: string;
  }[];
};

/** The wrapper carrying an :class:`ApiError` body from ``/api/v2``. */
export type ApiErrorResponse = { error: ApiError };
