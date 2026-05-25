import type {
  AccountAlertEvaluation,
  AccountAlertRule,
  AccountAlertRuleCreateInput,
  AccountAlertRuleDeleteResponse,
  AccountAlertRuleList,
  AccountDigestList,
  AccountDigestSendResponse,
  AccountPreference,
  AccountPreferenceUpdateInput,
  AccountSubscription,
  AccountSubscriptionCheckout,
  AccountFollowedPodcast,
  AccountFollowedPodcastDeleteResponse,
  AccountFollowedPodcastList,
  AccountSavedMedia,
  AccountSavedMediaDeleteResponse,
  AccountSavedMediaList,
  AccountUser,
  AuthLogoutResponse,
  AuthMagicLinkRequestInput,
  AuthMagicLinkRequestResponse,
  AuthSession,
  EpisodeDetail,
  EpisodeWithStats,
  EditorialCollectionDetail,
  EditorialCollectionSummary,
  MediaDetail,
  MediaItem,
  MediaType,
  MentionWithMedia,
  OpsDashboard,
  OpsOperationalMetrics,
  OpsOperationalAlerts,
  OpsIngestionRun,
  OpsMediaAliasInput,
  OpsMediaDetail,
  OpsMediaExternalRefInput,
  OpsMediaMergePreview,
  OpsMediaMergeResponse,
  OpsMediaSplitInput,
  OpsMediaSplitResponse,
  OpsMediaUpdateInput,
  OpsPipelineActivity,
  OpsPodcast,
  OpsPodcastCreateInput,
  OpsPodcastList,
  OpsPodcastSourceFilter,
  OpsPodcastSort,
  OpsPodcastStatus,
  OpsPodcastUpdateInput,
  OpsReviewDecisionInput,
  OpsReviewMergeInput,
  OpsReviewPriority,
  OpsReviewQueue,
  OpsReviewQueueItem,
  OpsReviewReclassifyInput,
  OpsReviewSplitInput,
  OpsReviewSplitResponse,
  OpsReviewStatus,
  OpsRetentionSamplingInput,
  OpsRetentionSamplingReport,
  OpsTranscriptPurgeResponse,
  OpsTranscriptReacquireInput,
  OpsTranscriptReacquireResponse,
  OpsTranscriptRetentionList,
  OpsTranscriptRetentionPolicyInput,
  OpsTranscriptRetentionPreview,
  OpsTakedownDecisionInput,
  OpsTakedownRequest,
  OpsTakedownRequestList,
  OpsScheduledWork,
  OpsScheduledWorkItem,
  OpsSearchAnalytics,
  OpsSearchProjection,
  OpsSearchReindexInput,
  OpsSearchReindexResponse,
  OpsSearchTuningApplyResponse,
  OpsSearchTuningInput,
  OpsSearchTuningPreview,
  OverviewStats,
  PaginatedResponse,
  Podcast,
  TopMentioned,
  TypeStats,
} from "./types";

interface V2EpisodeMediaMention {
  id: string;
  media: {
    id: string;
    type: MediaType;
    title: string;
    author?: string;
    cover_url?: string;
  };
  timestamp_seconds?: number;
  context?: string;
  confidence: number;
  youtube_timestamp_url?: string;
}

interface V2TrendsResponse {
  overview: OverviewStats;
  by_type: TypeStats[];
  top_mentioned: TopMentioned[];
}

export interface GlobalSearchResultItem {
  id: string;
  type: "media" | "episode" | "podcast";
  title: string;
  subtitle?: string;
  cover_url?: string;
  url: string;
}

export interface GlobalSearchResultGroup {
  type: "media" | "episode" | "podcast";
  hits: GlobalSearchResultItem[];
  total: number;
}

export interface GlobalSearchResponse {
  query: string;
  results: GlobalSearchResultGroup[];
  processing_time_ms: number;
}

function getApiBase(): string {
  if (typeof process !== "undefined" && process.env?.PUBLIC_API_URL) {
    return process.env.PUBLIC_API_URL;
  }

  return import.meta.env.PUBLIC_API_URL || "http://localhost:8000/api/v2";
}

const API_BASE = getApiBase();

async function fetchAPI<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    throw new Error(`API Error: ${response.status} ${response.statusText}`);
  }

  return response.json() as Promise<T>;
}

export async function requestMagicLink(
  payload: AuthMagicLinkRequestInput,
): Promise<AuthMagicLinkRequestResponse> {
  return fetchAPI<AuthMagicLinkRequestResponse>("/auth/magic-link/request", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function verifyMagicLink(token: string): Promise<AuthSession> {
  return fetchAPI<AuthSession>("/auth/magic-link/verify", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export async function getCurrentAccount(): Promise<AccountUser> {
  return fetchAPI<AccountUser>("/me");
}

export async function logoutAccount(): Promise<AuthLogoutResponse> {
  return fetchAPI<AuthLogoutResponse>("/auth/logout", { method: "POST" });
}

export async function getSavedMedia(): Promise<AccountSavedMediaList> {
  return fetchAPI<AccountSavedMediaList>("/me/saves");
}

export async function saveMedia(mediaId: string): Promise<AccountSavedMedia> {
  return fetchAPI<AccountSavedMedia>(`/me/saves/${mediaId}`, { method: "PUT" });
}

export async function removeSavedMedia(mediaId: string): Promise<AccountSavedMediaDeleteResponse> {
  return fetchAPI<AccountSavedMediaDeleteResponse>(`/me/saves/${mediaId}`, {
    method: "DELETE",
  });
}

export async function getFollowedPodcasts(): Promise<AccountFollowedPodcastList> {
  return fetchAPI<AccountFollowedPodcastList>("/me/follows");
}

export async function followPodcast(podcastId: string): Promise<AccountFollowedPodcast> {
  return fetchAPI<AccountFollowedPodcast>(`/me/follows/${podcastId}`, {
    method: "PUT",
  });
}

export async function unfollowPodcast(
  podcastId: string,
): Promise<AccountFollowedPodcastDeleteResponse> {
  return fetchAPI<AccountFollowedPodcastDeleteResponse>(`/me/follows/${podcastId}`, {
    method: "DELETE",
  });
}

export async function getAlertRules(): Promise<AccountAlertRuleList> {
  return fetchAPI<AccountAlertRuleList>("/me/alerts");
}

export async function createAlertRule(
  payload: AccountAlertRuleCreateInput,
): Promise<AccountAlertRule> {
  return fetchAPI<AccountAlertRule>("/me/alerts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function setAlertRuleEnabled(
  ruleId: string,
  enabled: boolean,
): Promise<AccountAlertRule> {
  return fetchAPI<AccountAlertRule>(`/me/alerts/${ruleId}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

export async function deleteAlertRule(ruleId: string): Promise<AccountAlertRuleDeleteResponse> {
  return fetchAPI<AccountAlertRuleDeleteResponse>(`/me/alerts/${ruleId}`, {
    method: "DELETE",
  });
}

export async function evaluateAlertRules(): Promise<AccountAlertEvaluation> {
  return fetchAPI<AccountAlertEvaluation>("/me/alerts/evaluate", { method: "POST" });
}

export async function getDigests(): Promise<AccountDigestList> {
  return fetchAPI<AccountDigestList>("/me/digests");
}

export async function sendDigest(): Promise<AccountDigestSendResponse> {
  return fetchAPI<AccountDigestSendResponse>("/me/digests/send", { method: "POST" });
}

export async function getAccountPreferences(): Promise<AccountPreference> {
  return fetchAPI<AccountPreference>("/me/preferences");
}

export async function updateAccountPreferences(
  payload: AccountPreferenceUpdateInput,
): Promise<AccountPreference> {
  return fetchAPI<AccountPreference>("/me/preferences", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function getAccountSubscription(): Promise<AccountSubscription> {
  return fetchAPI<AccountSubscription>("/me/subscription");
}

export async function beginAccountCheckout(): Promise<AccountSubscriptionCheckout> {
  return fetchAPI<AccountSubscriptionCheckout>("/me/subscription/checkout", {
    method: "POST",
  });
}

function normalizeSearchUrl(url: string): string {
  if (url.startsWith("/podcasts/")) {
    return url.replace("/podcasts/", "/sources/");
  }

  return url;
}

function buildPaginationParams(page: number, perPage: number): URLSearchParams {
  const params = new URLSearchParams();
  params.set("page", page.toString());
  params.set("per_page", perPage.toString());
  return params;
}

function buildTypeParams(params: URLSearchParams, types?: string[]): URLSearchParams {
  if (types && types.length > 0) {
    types.forEach((type) => params.append("type", type));
  }

  return params;
}

async function getTrends(limit = 10, type?: string): Promise<V2TrendsResponse> {
  const params = new URLSearchParams();
  params.set("limit", limit.toString());
  if (type) {
    params.set("type", type);
  }

  return fetchAPI<V2TrendsResponse>(`/trends?${params.toString()}`);
}

function flattenEpisodeMention(mention: V2EpisodeMediaMention): MentionWithMedia {
  return {
    id: mention.id,
    media_id: mention.media.id,
    media_title: mention.media.title,
    media_type: mention.media.type,
    media_author: mention.media.author,
    media_cover_url: mention.media.cover_url,
    timestamp_seconds: mention.timestamp_seconds,
    context: mention.context,
    confidence: mention.confidence,
    youtube_timestamp_url: mention.youtube_timestamp_url,
  };
}

export async function getPodcasts(): Promise<Podcast[]> {
  return fetchAPI<Podcast[]>("/podcasts");
}

export async function getPodcast(slug: string): Promise<Podcast> {
  return fetchAPI<Podcast>(`/podcasts/${slug}`);
}

export async function getPodcastEpisodes(
  slug: string,
  page = 1,
  perPage = 20,
): Promise<PaginatedResponse<EpisodeWithStats>> {
  const params = buildPaginationParams(page, perPage);
  return fetchAPI<PaginatedResponse<EpisodeWithStats>>(
    `/podcasts/${slug}/episodes?${params.toString()}`,
  );
}

export async function getEpisodes(
  page = 1,
  perPage = 20,
  podcastId?: string,
): Promise<PaginatedResponse<EpisodeWithStats>> {
  const params = buildPaginationParams(page, perPage);
  if (podcastId) {
    params.set("podcast_id", podcastId);
  }

  return fetchAPI<PaginatedResponse<EpisodeWithStats>>(`/episodes?${params.toString()}`);
}

export async function getEpisode(id: string): Promise<EpisodeDetail> {
  return fetchAPI<EpisodeDetail>(`/episodes/${id}`);
}

export async function getEpisodeMentions(id: string): Promise<MentionWithMedia[]> {
  const mentions = await fetchAPI<V2EpisodeMediaMention[]>(`/episodes/${id}/mentions`);
  return mentions.map((mention) => flattenEpisodeMention(mention));
}

export async function getMedia(
  page = 1,
  perPage = 20,
  types?: string[],
  sort = "mention_count",
  order = "desc",
): Promise<PaginatedResponse<MediaItem>> {
  const params = buildTypeParams(buildPaginationParams(page, perPage), types);
  params.set("sort", sort);
  params.set("order", order);

  return fetchAPI<PaginatedResponse<MediaItem>>(`/media?${params.toString()}`);
}

export async function getMediaById(id: string): Promise<MediaDetail> {
  return fetchAPI<MediaDetail>(`/media/${id}`);
}

export async function searchMedia(
  query: string,
  page = 1,
  perPage = 20,
  types?: string[],
): Promise<PaginatedResponse<MediaItem>> {
  const params = buildTypeParams(buildPaginationParams(page, perPage), types);
  params.set("q", query);

  return fetchAPI<PaginatedResponse<MediaItem>>(`/media/search?${params.toString()}`);
}

export async function getTopMedia(limit = 10, type?: string): Promise<MediaItem[]> {
  const params = buildPaginationParams(1, limit);
  params.set("sort", "mention_count");
  params.set("order", "desc");
  if (type) {
    params.append("type", type);
  }

  const response = await fetchAPI<PaginatedResponse<MediaItem>>(`/media?${params.toString()}`);
  return response.items;
}

export async function getOverviewStats(): Promise<OverviewStats> {
  const trends = await getTrends();
  return trends.overview;
}

export async function getStatsByType(): Promise<TypeStats[]> {
  const trends = await getTrends(50);
  return trends.by_type;
}

export async function getTopMentioned(limit = 10, type?: string): Promise<TopMentioned[]> {
  const trends = await getTrends(limit, type);
  return trends.top_mentioned;
}

export async function globalSearch(query: string, limit = 10): Promise<GlobalSearchResponse> {
  const params = new URLSearchParams();
  params.set("q", query);
  params.set("limit", limit.toString());

  const response = await fetchAPI<GlobalSearchResponse>(`/search?${params.toString()}`);
  return {
    ...response,
    results: response.results.map((group) => ({
      ...group,
      hits: group.hits.map((item) => ({
        ...item,
        url: normalizeSearchUrl(item.url),
      })),
    })),
  };
}

export async function recordSearchSelection(
  query: string,
  item: GlobalSearchResultItem,
): Promise<void> {
  await fetchAPI<{ recorded: boolean }>("/search/selection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      result_type: item.type,
      result_id: item.id,
    }),
  });
}

export async function getCollections(): Promise<EditorialCollectionSummary[]> {
  return fetchAPI<EditorialCollectionSummary[]>("/collections");
}

export async function getCollection(slug: string): Promise<EditorialCollectionDetail> {
  return fetchAPI<EditorialCollectionDetail>(`/collections/${slug}`);
}

export async function getOpsDashboard(): Promise<OpsDashboard> {
  return fetchAPI<OpsDashboard>("/ops/dashboard");
}

export async function getOpsOperationalMetrics(): Promise<OpsOperationalMetrics> {
  return fetchAPI<OpsOperationalMetrics>("/ops/metrics");
}

export async function getOpsOperationalAlerts(): Promise<OpsOperationalAlerts> {
  return fetchAPI<OpsOperationalAlerts>("/ops/alerts");
}

export async function getOpsPipelineActivity(
  runLimit = 5,
  jobLimit = 6,
): Promise<OpsPipelineActivity> {
  const params = new URLSearchParams();
  params.set("run_limit", runLimit.toString());
  params.set("job_limit", jobLimit.toString());

  return fetchAPI<OpsPipelineActivity>(`/ops/pipelines?${params.toString()}`);
}

export async function getOpsReviewQueue(
  status: OpsReviewStatus | "" = "pending",
  priority: OpsReviewPriority | "" = "",
  perPage = 50,
): Promise<OpsReviewQueue> {
  const params = new URLSearchParams();
  params.set("page", "1");
  params.set("per_page", perPage.toString());
  if (status) {
    params.set("status", status);
  }
  if (priority) {
    params.set("priority", priority);
  }

  return fetchAPI<OpsReviewQueue>(`/ops/review-queue?${params.toString()}`);
}

export async function approveOpsReviewItem(
  id: string,
  payload: OpsReviewDecisionInput,
): Promise<OpsReviewQueueItem> {
  return fetchAPI<OpsReviewQueueItem>(`/ops/review-queue/${id}/approve`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function rejectOpsReviewItem(
  id: string,
  payload: OpsReviewDecisionInput,
): Promise<OpsReviewQueueItem> {
  return fetchAPI<OpsReviewQueueItem>(`/ops/review-queue/${id}/reject`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function mergeOpsReviewItem(
  id: string,
  payload: OpsReviewMergeInput,
): Promise<OpsReviewQueueItem> {
  return fetchAPI<OpsReviewQueueItem>(`/ops/review-queue/${id}/merge`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function reclassifyOpsReviewItem(
  id: string,
  payload: OpsReviewReclassifyInput,
): Promise<OpsReviewQueueItem> {
  return fetchAPI<OpsReviewQueueItem>(`/ops/review-queue/${id}/reclassify`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function splitOpsReviewItem(
  id: string,
  payload: OpsReviewSplitInput,
): Promise<OpsReviewSplitResponse> {
  return fetchAPI<OpsReviewSplitResponse>(`/ops/review-queue/${id}/split`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function previewOpsMediaMerge(
  sourceId: string,
  targetId: string,
): Promise<OpsMediaMergePreview> {
  const params = new URLSearchParams({ target_id: targetId });
  return fetchAPI<OpsMediaMergePreview>(
    `/ops/media/${sourceId}/merge-preview?${params.toString()}`,
  );
}

export async function mergeOpsMedia(
  sourceId: string,
  targetId: string,
): Promise<OpsMediaMergeResponse> {
  return fetchAPI<OpsMediaMergeResponse>(`/ops/media/${sourceId}/merge`, {
    method: "POST",
    body: JSON.stringify({ target_id: targetId }),
  });
}

export async function getOpsMediaDetail(id: string): Promise<OpsMediaDetail> {
  return fetchAPI<OpsMediaDetail>(`/ops/media/${id}`);
}

export async function updateOpsMedia(
  id: string,
  payload: OpsMediaUpdateInput,
): Promise<OpsMediaDetail> {
  return fetchAPI<OpsMediaDetail>(`/ops/media/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function addOpsMediaAlias(
  id: string,
  payload: OpsMediaAliasInput,
): Promise<OpsMediaDetail> {
  return fetchAPI<OpsMediaDetail>(`/ops/media/${id}/aliases`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function upsertOpsMediaExternalRef(
  id: string,
  payload: OpsMediaExternalRefInput,
): Promise<OpsMediaDetail> {
  return fetchAPI<OpsMediaDetail>(`/ops/media/${id}/external-refs`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function splitOpsMedia(
  id: string,
  payload: OpsMediaSplitInput,
): Promise<OpsMediaSplitResponse> {
  return fetchAPI<OpsMediaSplitResponse>(`/ops/media/${id}/split`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getOpsScheduledWork(limit = 8, status?: string): Promise<OpsScheduledWork> {
  const params = new URLSearchParams();
  params.set("limit", limit.toString());
  if (status) {
    params.set("status", status);
  }

  return fetchAPI<OpsScheduledWork>(`/ops/scheduled-work?${params.toString()}`);
}

export async function getOpsSearchProjection(): Promise<OpsSearchProjection> {
  return fetchAPI<OpsSearchProjection>("/ops/search");
}

export async function getOpsSearchAnalytics(): Promise<OpsSearchAnalytics> {
  return fetchAPI<OpsSearchAnalytics>("/ops/search/analytics");
}

export async function queueOpsSearchReindex(
  payload: OpsSearchReindexInput,
): Promise<OpsSearchReindexResponse> {
  return fetchAPI<OpsSearchReindexResponse>("/ops/search/reindex", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function previewOpsSearchTuning(
  payload: OpsSearchTuningInput,
): Promise<OpsSearchTuningPreview> {
  return fetchAPI<OpsSearchTuningPreview>("/ops/search/tuning/preview", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function applyOpsSearchTuning(
  payload: OpsSearchTuningInput,
): Promise<OpsSearchTuningApplyResponse> {
  return fetchAPI<OpsSearchTuningApplyResponse>("/ops/search/tuning", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getOpsRetentionSampling(): Promise<OpsRetentionSamplingReport> {
  return fetchAPI<OpsRetentionSamplingReport>("/ops/retention/sampling");
}

export async function recalculateOpsRetentionSampling(
  payload: OpsRetentionSamplingInput,
): Promise<OpsRetentionSamplingReport> {
  return fetchAPI<OpsRetentionSamplingReport>("/ops/retention/sampling/recalculate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getOpsTranscriptRetention(): Promise<OpsTranscriptRetentionList> {
  return fetchAPI<OpsTranscriptRetentionList>("/ops/retention/transcripts");
}

export async function previewOpsTranscriptRetention(
  id: string,
  payload: OpsTranscriptRetentionPolicyInput,
): Promise<OpsTranscriptRetentionPreview> {
  return fetchAPI<OpsTranscriptRetentionPreview>(`/ops/retention/transcripts/${id}/preview`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function evaluateOpsTranscriptRetention(
  id: string,
  payload: OpsTranscriptRetentionPolicyInput,
): Promise<OpsTranscriptRetentionPreview> {
  return fetchAPI<OpsTranscriptRetentionPreview>(`/ops/retention/transcripts/${id}/evaluate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function purgeOpsTranscript(
  id: string,
  payload: OpsTranscriptRetentionPolicyInput,
): Promise<OpsTranscriptPurgeResponse> {
  return fetchAPI<OpsTranscriptPurgeResponse>(`/ops/retention/transcripts/${id}/purge`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function reacquireOpsTranscript(
  id: string,
  payload: OpsTranscriptReacquireInput,
): Promise<OpsTranscriptReacquireResponse> {
  return fetchAPI<OpsTranscriptReacquireResponse>(`/ops/retention/transcripts/${id}/reacquire`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getOpsTakedownRequests(): Promise<OpsTakedownRequestList> {
  return fetchAPI<OpsTakedownRequestList>("/ops/takedown-requests");
}

export async function decideOpsTakedownRequest(
  id: string,
  payload: OpsTakedownDecisionInput,
): Promise<OpsTakedownRequest> {
  return fetchAPI<OpsTakedownRequest>(`/ops/takedown-requests/${id}/decision`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function runOpsPipeline(): Promise<OpsIngestionRun> {
  return fetchAPI<OpsIngestionRun>("/ops/pipelines/run", {
    method: "POST",
  });
}

export async function planOpsScheduledWork(): Promise<OpsScheduledWorkItem[]> {
  return fetchAPI<OpsScheduledWorkItem[]>("/ops/scheduled-work/plan", {
    method: "POST",
  });
}

export async function getOpsPodcasts(
  status?: OpsPodcastStatus,
  source?: OpsPodcastSourceFilter,
  sort: OpsPodcastSort = "created_at",
  order: "asc" | "desc" = "desc",
): Promise<OpsPodcastList> {
  const params = buildPaginationParams(1, 100);
  if (status) {
    params.set("status", status);
  }
  if (source) {
    params.set("source", source);
  }
  params.set("sort", sort);
  params.set("order", order);

  return fetchAPI<OpsPodcastList>(`/ops/podcasts?${params.toString()}`);
}

export async function createOpsPodcast(payload: OpsPodcastCreateInput): Promise<OpsPodcast> {
  return fetchAPI<OpsPodcast>("/ops/podcasts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateOpsPodcast(
  id: string,
  payload: OpsPodcastUpdateInput,
): Promise<OpsPodcast> {
  return fetchAPI<OpsPodcast>(`/ops/podcasts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function archiveOpsPodcast(id: string): Promise<OpsPodcast> {
  return fetchAPI<OpsPodcast>(`/ops/podcasts/${id}/archive`, {
    method: "POST",
  });
}
