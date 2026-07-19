import { API_BASE_URL } from "./config";
import type { components } from "./types.gen";

export type OpsPodcast = components["schemas"]["OpsPodcastRead"];
export type OpsPodcastList = components["schemas"]["OpsPodcastListRead"];
export type OpsPodcastCreateInput = components["schemas"]["OpsPodcastCreateRequest"];
export type OpsPodcastUpdateInput = components["schemas"]["OpsPodcastUpdateRequest"];
export type OpsMetrics = components["schemas"]["OpsMetricsRead"];
export type OpsPipelineActivity = components["schemas"]["OpsPipelineActivityRead"];
export type OpsRetentionItem = components["schemas"]["OpsTranscriptRetentionRead"];
export type OpsRetentionPreview = components["schemas"]["OpsRetentionPreviewRead"];
export type OpsAuditLogList = components["schemas"]["OpsAuditLogListRead"];

const OPS_KEY_STORAGE = "podex-ops-key";

/** Read the operator key from session storage (browser only). */
export function getOpsKey(): string | null {
  if (typeof sessionStorage === "undefined") return null;
  return sessionStorage.getItem(OPS_KEY_STORAGE);
}

/** Persist the operator key for this browser session. */
export function setOpsKey(key: string): void {
  sessionStorage.setItem(OPS_KEY_STORAGE, key);
}

/** Forget the operator key. */
export function clearOpsKey(): void {
  sessionStorage.removeItem(OPS_KEY_STORAGE);
}

/** Error carrying the HTTP status so callers can branch on 401/503. */
export class OpsApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function opsFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const key = getOpsKey();
  const response = await fetch(`${API_BASE_URL}/ops${path}`, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(key ? { "X-Ops-Key": key } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new OpsApiError(response.status, `Ops request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export function getOpsMetrics(): Promise<OpsMetrics> {
  return opsFetch<OpsMetrics>("/metrics");
}

export function getOpsPodcasts(
  params: {
    page?: number;
    status?: string;
    source?: string;
    sort?: string;
    order?: string;
  } = {},
): Promise<OpsPodcastList> {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value !== undefined) search.set(name, String(value));
  }
  const query = search.toString();
  return opsFetch<OpsPodcastList>(`/podcasts${query ? `?${query}` : ""}`);
}

export function createOpsPodcast(
  input: OpsPodcastCreateInput,
): Promise<OpsPodcast> {
  return opsFetch<OpsPodcast>("/podcasts", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateOpsPodcast(
  id: number,
  input: OpsPodcastUpdateInput,
): Promise<OpsPodcast> {
  return opsFetch<OpsPodcast>(`/podcasts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function archiveOpsPodcast(id: number): Promise<OpsPodcast> {
  return opsFetch<OpsPodcast>(`/podcasts/${id}/archive`, { method: "POST" });
}

export function getOpsPipelines(limit = 20): Promise<OpsPipelineActivity> {
  return opsFetch<OpsPipelineActivity>(`/pipelines?limit=${limit}`);
}

export function getOpsRetention(limit = 40): Promise<OpsRetentionItem[]> {
  return opsFetch<OpsRetentionItem[]>(`/retention?limit=${limit}`);
}

export function previewOpsRetention(
  transcriptId: number,
): Promise<OpsRetentionPreview> {
  return opsFetch<OpsRetentionPreview>(`/retention/${transcriptId}/preview`);
}

export function applyOpsRetention(
  transcriptId: number,
): Promise<OpsRetentionPreview> {
  return opsFetch<OpsRetentionPreview>(`/retention/${transcriptId}/apply`, {
    method: "POST",
  });
}

export function getOpsAuditLog(page = 1): Promise<OpsAuditLogList> {
  return opsFetch<OpsAuditLogList>(`/audit-log?page=${page}`);
}
