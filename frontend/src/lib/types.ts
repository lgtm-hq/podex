import type { components } from "./types.gen";

/** A podcast source in the catalog. */
export type Podcast = components["schemas"]["PodcastRead"];

/** A single page of podcasts returned by the v2 list endpoint. */
export type PodcastPage = components["schemas"]["Page_PodcastRead_"];

/**
 * The RFC 9457 problem-details body returned by `/api/v2` failures, served
 * as `application/problem+json`. `type`, `title`, `status`, and `detail` are
 * standard members; `code`, `request_id`, and `errors` are podex extensions.
 */
export type ApiProblem = {
  type: string;
  title: string;
  status: number;
  detail?: string | null;
  code: string;
  request_id?: string | null;
  errors?: {
    loc: (string | number)[];
    msg: string;
    type: string;
  }[];
};
