export type MediaType =
  | "book"
  | "movie"
  | "documentary"
  | "tv_show"
  | "study"
  | "podcast"
  | "article"
  | "standup_special"
  | "person"
  | "place";

export type OpaqueId = string;

export interface Podcast {
  id: OpaqueId;
  name: string;
  slug: string;
  description?: string;
  cover_url?: string;
  created_at: string;
  episode_count?: number;
  mention_count?: number;
}

export interface Episode {
  id: OpaqueId;
  podcast_id: OpaqueId;
  title: string;
  episode_number?: number;
  youtube_id?: string;
  published_at?: string;
  duration_seconds?: number;
  thumbnail_url?: string;
  transcript_status: string;
  created_at: string;
}

export interface EpisodeWithStats extends Episode {
  mention_count: number;
}

export interface EpisodeDetail extends EpisodeWithStats {
  podcast_name: string;
  podcast_slug: string;
  extraction_status: string;
  cleanup_status: string;
  derivative_summary?: string;
  derivative_mentioned_media_titles: string[];
  derivative_evidence: DerivativeEvidenceChunk[];
}

export interface EpisodeBrief {
  id: OpaqueId;
  title: string;
  episode_number?: number;
  youtube_id?: string;
  published_at?: string;
  thumbnail_url?: string;
}

export interface MediaItem {
  id: OpaqueId;
  type: MediaType;
  title: string;
  author?: string;
  cover_url?: string;
  year?: number;
  description?: string;
  mention_count: number;
  episode_count: number;
  created_at: string;
}

export interface CastMember {
  name: string;
  character?: string;
}

export interface MediaDetail extends MediaItem {
  mentions: MentionWithEpisode[];
  derivative_summary?: string;
  related_media: RelatedMediaItem[];

  // External IDs
  google_books_id?: string;
  open_library_id?: string;
  imdb_id?: string;
  tmdb_id?: number;
  wikipedia_id?: string;

  // Academic IDs
  doi?: string;
  pubmed_id?: string;
  semantic_scholar_id?: string;

  // Raw metadata
  metadata_json?: Record<string, unknown>;

  // Enrichment tracking
  enriched_at?: string;
  enrichment_source?: string;

  // Multi-source verification tracking
  verification_sources?: string[];
  doi_verified?: boolean;

  // Ratings
  imdb_rating?: number;
  tmdb_rating?: number;
  rotten_tomatoes?: number;
  metacritic?: number;
  google_books_rating?: number;

  // Awards
  awards?: string;
  oscar_wins?: number;
  oscar_nominations?: number;

  // Additional metadata
  runtime_minutes?: number;
  page_count?: number;
  genres?: string[];
  cast?: CastMember[];
  directors?: string[];

  // Academic metadata
  journal?: string;
  authors?: string[];
  citation_count?: number;
  publication_date?: string;
  mesh_terms?: string[];
  fields_of_study?: string[];
  open_access_pdf_url?: string;

  // Movie/TV metadata
  tagline?: string;
  budget?: number;
  revenue?: number;
  production_countries?: string[];
  spoken_languages?: string[];
  status?: string;
  networks?: string[];
  seasons?: number;
  episodes?: number;

  // Book metadata
  isbn?: string;
  publisher?: string;
  preview_link?: string;
  language?: string;

  // Podcast metadata
  podcast_episode_count?: number;
  explicit?: boolean;
  feed_url?: string;

  // Person metadata
  biography?: string;
  birthday?: string;
  birthplace?: string;
  known_for?: string[];

  // Wikipedia metadata
  wikipedia_categories?: string[];

  // External URLs
  imdb_url?: string;
  tmdb_url?: string;
  wikipedia_url?: string;
  google_books_url?: string;
  open_library_url?: string;

  // Academic URLs
  doi_url?: string;
  pubmed_url?: string;
  semantic_scholar_url?: string;
}

export interface MentionWithEpisode {
  id: OpaqueId;
  episode: EpisodeBrief;
  timestamp_seconds?: number;
  context?: string;
  confidence: number;
  youtube_timestamp_url?: string;
}

export interface DerivativeEvidenceChunk {
  summary?: string;
  content: string;
  start_seconds?: number;
  end_seconds?: number;
}

export interface RelatedMediaItem {
  id: OpaqueId;
  type: MediaType;
  title: string;
  author?: string;
  cover_url?: string;
  relation_type: string;
  direction: "outgoing" | "incoming";
  source: string;
  confidence: number;
  evidence_text?: string;
  provenance_episode_id?: OpaqueId;
}

export interface MentionWithMedia {
  id: OpaqueId;
  media_id: OpaqueId;
  media_title: string;
  media_type: MediaType;
  media_author?: string;
  media_cover_url?: string;
  timestamp_seconds?: number;
  context?: string;
  confidence: number;
  youtube_timestamp_url?: string;
}

export interface OverviewStats {
  total_podcasts: number;
  total_episodes: number;
  total_media: number;
  total_mentions: number;
  total_books: number;
  total_movies: number;
}

export interface TypeStats {
  type: string;
  count: number;
  mention_count: number;
}

export interface TopMentioned {
  id: OpaqueId;
  title: string;
  type: string;
  author?: string;
  mention_count: number;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
}

export interface OpsCatalogSummary {
  total_podcasts: number;
  active_podcasts: number;
  watchlist_podcasts: number;
  paused_podcasts: number;
}

export interface OpsSourceCoverageSummary {
  with_rss: number;
  with_spotify: number;
  with_podscripts: number;
  with_youtube: number;
}

export interface OpsEpisodeProcessingSummary {
  total_known: number;
  transcribed: number;
  extracted: number;
}

export interface OpsPipelineSummary {
  ingestion_runs_total: number;
  ingestion_runs_in_progress: number;
  ingestion_runs_failed: number;
  ingestion_runs_completed: number;
  transcription_jobs_pending: number;
  transcription_jobs_failed: number;
  transcription_jobs_in_progress: number;
  projection_repairs_pending: number;
  projection_repairs_failed: number;
}

export interface OpsDashboard {
  catalog: OpsCatalogSummary;
  sources: OpsSourceCoverageSummary;
  episodes: OpsEpisodeProcessingSummary;
  pipelines: OpsPipelineSummary;
  search: {
    enabled: boolean;
  };
}

export interface OpsOperationalMetrics {
  measured_at: string;
  review: {
    pending_items: number;
    decisions_last_24h: number;
    median_decision_minutes_last_24h?: number | null;
  };
  projection: {
    pending_repairs: number;
    failed_repairs: number;
    oldest_pending_age_seconds?: number | null;
  };
  alerts: {
    generated_events_last_24h: number;
    delivered_digests_last_24h: number;
    delivered_events_last_24h: number;
    pending_events: number;
  };
}

export interface OpsOperationalAlerts {
  measured_at: string;
  alerts: Array<{
    key: string;
    severity: "warning" | "critical";
    title: string;
    message: string;
    current_value: number;
    threshold: number;
    playbook_slug: string;
  }>;
}

export interface OpsIngestionRun {
  id: string;
  status: string;
  error_summary?: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  duration_seconds?: number;
}

export interface OpsTranscriptionJob {
  id: string;
  episode_id: string;
  podcast_id: string;
  podcast_name: string;
  podcast_slug: string;
  episode_title: string;
  job_type: string;
  status: string;
  backend?: string;
  model?: string;
  error_message?: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  duration_seconds?: number;
}

export interface OpsPipelineActivity {
  summary: OpsPipelineSummary;
  runs: OpsIngestionRun[];
  jobs: OpsTranscriptionJob[];
}

export type OpsReviewStatus =
  | "pending"
  | "in_review"
  | "approved"
  | "rejected"
  | "merged"
  | "split";

export type OpsReviewPriority = "low" | "medium" | "high";

export interface OpsReviewQueueExtractionJob {
  id: string;
  status: string;
  backend?: string;
  model?: string;
  error_message?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  is_source_job: boolean;
}

export interface OpsReviewQueueCandidateProvenance {
  id: string;
  event_type: string;
  change_summary?: string;
  raw_title: string;
  normalized_title?: string;
  suggested_author?: string;
  timestamp_seconds?: number;
  context?: string;
  confidence: number;
  extraction_source?: string;
  source_job_id?: string;
  source_job_status?: string;
  source_job_backend?: string;
  source_job_model?: string;
  source_job_created_at?: string;
  media_id?: string;
  changed_fields: string[];
  created_at: string;
}

export interface OpsReviewQueueItem {
  id: string;
  status: OpsReviewStatus;
  priority: OpsReviewPriority;
  assigned_to?: string;
  decision_note?: string;
  created_at: string;
  updated_at: string;
  decided_at?: string;
  episode_id: string;
  episode_title: string;
  podcast_id: string;
  podcast_name: string;
  podcast_slug: string;
  target_media_id?: string;
  candidate: {
    id: string;
    type: MediaType;
    raw_title: string;
    normalized_title?: string;
    suggested_author?: string;
    timestamp_seconds?: number;
    confidence: number;
    state: string;
    context?: string;
    extraction_source?: string;
    source_job_id?: string;
    source_job_status?: string;
    source_job_backend?: string;
    source_job_model?: string;
    source_job_created_at?: string;
    media_id?: string;
    mention_id?: string;
    created_at: string;
    reviewed_at?: string;
    extraction_jobs: OpsReviewQueueExtractionJob[];
    provenance: OpsReviewQueueCandidateProvenance[];
  };
}

export interface OpsReviewQueue {
  items: OpsReviewQueueItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface OpsReviewDecisionInput {
  actor_name?: string | null;
  note?: string | null;
}

export interface OpsReviewReclassifyInput extends OpsReviewDecisionInput {
  type?: MediaType;
  raw_title?: string;
  normalized_title?: string | null;
  suggested_author?: string | null;
}

export interface OpsReviewMergeInput extends OpsReviewDecisionInput {
  target_id: string;
}

export interface OpsReviewSplitInput extends OpsReviewDecisionInput {
  candidates: Array<{
    type: MediaType;
    raw_title: string;
    suggested_author?: string | null;
  }>;
}

export interface OpsReviewSplitResponse {
  original: OpsReviewQueueItem;
  items: OpsReviewQueueItem[];
}

export interface OpsMergedMediaSummary {
  id: string;
  type: MediaType;
  title: string;
  author?: string;
  cover_url?: string;
  year?: number;
  description?: string;
  mention_count: number;
  episode_count: number;
}

export interface OpsMediaMergeFieldChange {
  field: string;
  source_value: unknown;
  target_value: unknown;
  merged_value: unknown;
}

export interface OpsMediaMergeAliasAddition {
  alias: string;
  normalized_alias: string;
  source: string;
}

export interface OpsMediaMergePreview {
  source: OpsMergedMediaSummary;
  target: OpsMergedMediaSummary;
  field_changes: OpsMediaMergeFieldChange[];
  alias_additions: OpsMediaMergeAliasAddition[];
  mentions_to_move: number;
}

export interface OpsMediaMergeResponse {
  source_id: string;
  target: OpsMergedMediaSummary;
}

export type OpsMediaExternalRefSource =
  | "doi"
  | "google_books"
  | "imdb"
  | "manual"
  | "open_library"
  | "pubmed"
  | "semantic_scholar"
  | "tmdb"
  | "wikipedia";

export interface OpsMediaAlias {
  alias: string;
  normalized_alias: string;
  source: string;
  is_primary: boolean;
}

export interface OpsMediaExternalRef {
  source: string;
  external_id: string;
  url?: string;
  label?: string;
  description?: string;
}

export interface OpsMediaRelation {
  direction: string;
  relation_type: string;
  related_media: OpsMergedMediaSummary;
  source: string;
  confidence: number;
}

export interface OpsMediaMention {
  id: string;
  episode_id: string;
  episode_title: string;
  timestamp_seconds?: number;
  context?: string;
  confidence: number;
}

export interface OpsMediaDetail {
  media: OpsMergedMediaSummary;
  google_books_id?: string;
  open_library_id?: string;
  imdb_id?: string;
  tmdb_id?: number;
  wikipedia_id?: string;
  pubmed_id?: string;
  doi?: string;
  semantic_scholar_id?: string;
  metadata_json?: Record<string, unknown>;
  verification_sources: string[];
  aliases: OpsMediaAlias[];
  external_refs: OpsMediaExternalRef[];
  relations: OpsMediaRelation[];
  mentions: OpsMediaMention[];
}

export interface OpsMediaUpdateInput {
  type?: MediaType;
  title?: string;
  author?: string | null;
  cover_url?: string | null;
  year?: number | null;
  description?: string | null;
  google_books_id?: string | null;
  open_library_id?: string | null;
  imdb_id?: string | null;
  tmdb_id?: number | null;
  wikipedia_id?: string | null;
  pubmed_id?: string | null;
  doi?: string | null;
  semantic_scholar_id?: string | null;
  metadata_json?: Record<string, unknown> | null;
  actor_name?: string | null;
  note?: string | null;
}

export interface OpsMediaAliasInput {
  alias: string;
  actor_name?: string | null;
  note?: string | null;
}

export interface OpsMediaExternalRefInput {
  source: OpsMediaExternalRefSource;
  external_id: string;
  url?: string | null;
  label?: string | null;
  description?: string | null;
  actor_name?: string | null;
  note?: string | null;
}

export interface OpsMediaSplitInput {
  mention_ids: string[];
  type: MediaType;
  title: string;
  author?: string | null;
  description?: string | null;
  actor_name?: string | null;
  note?: string | null;
}

export interface OpsMediaSplitResponse {
  source: OpsMediaDetail;
  created: OpsMediaDetail;
  mentions_moved: number;
}

export interface OpsPipelineSchedule {
  id: string;
  schedule_key: string;
  task_kind: string;
  interval_minutes: number;
  enabled: boolean;
  last_scheduled_at?: string;
  next_due_at?: string;
  created_at: string;
  updated_at: string;
}

export interface OpsScheduledWorkItem {
  id: string;
  schedule_id: string;
  ingestion_run_id?: string;
  schedule_key: string;
  work_key: string;
  task_kind: string;
  status: string;
  due_at: string;
  interval_minutes: number;
  error_message?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
}

export interface OpsScheduledWork {
  schedules: OpsPipelineSchedule[];
  work_items: OpsScheduledWorkItem[];
}

export interface OpsSearchProjection {
  configured: boolean;
  healthy: boolean;
  indexes: Array<{
    name: string;
    document_count: number;
    is_indexing: boolean;
  }>;
  repair_summary: {
    pending: number;
    failed: number;
    completed: number;
  };
  repairs: OpsSearchProjectionRepair[];
}

export interface OpsSearchProjectionRepair {
  id: string;
  resource_type: "media" | "episode";
  resource_id: string;
  status: "pending" | "failed" | "completed";
  reason: string;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export interface OpsSearchReindexInput {
  resource_type: "all" | "media" | "episode";
  podcast_id?: string | null;
  media_type?: MediaType | null;
  created_after?: string | null;
  actor_name?: string | null;
  note?: string | null;
}

export interface OpsSearchReindexResponse {
  media_queued: number;
  episodes_queued: number;
  total_queued: number;
}

export interface OpsSearchTuningInput {
  index: "media" | "episodes" | "podcasts";
  query: string;
  synonyms: Record<string, string[]>;
  ranking_rules?: string[] | null;
  actor_name?: string | null;
  note?: string | null;
}

export interface OpsSearchTuningPreview {
  index: string;
  query: string;
  sample_hits: Array<Record<string, unknown>>;
  proposed_settings: Record<string, unknown>;
}

export interface OpsSearchTuningApplyResponse {
  index: string;
  status: string;
  task_uid?: number | null;
}

export interface OpsSearchQueryMetric {
  query: string;
  searches: number;
  zero_result_searches: number;
  selections: number;
}

export interface OpsSearchAnalytics {
  searches: number;
  zero_result_searches: number;
  selections: number;
  queries: OpsSearchQueryMetric[];
}

export interface OpsRetentionSamplingStratum {
  source: string;
  topic: string;
  confidence_band: string;
  age_bucket: string;
  eligible_count: number;
  sampled_count: number;
  target_count: number;
}

export interface OpsRetentionSamplingReport {
  policy_version: string;
  sample_rate: number;
  eligible_count: number;
  sampled_count: number;
  target_count: number;
  strata: OpsRetentionSamplingStratum[];
}

export interface OpsRetentionSamplingInput {
  policy_version: string;
  sample_rate: number;
  actor_name?: string | null;
  note?: string | null;
}

export interface OpsTranscriptRetentionPolicyInput {
  policy_version: string;
  hot_days: number;
  warm_days: number;
  min_purge_confidence: number;
  source_retention_opt_out?: boolean;
  actor_name?: string | null;
  note?: string | null;
}

export interface OpsTranscriptRetentionSummary {
  id: string;
  episode_id: string;
  episode_title: string;
  podcast_name: string;
  provider: string;
  fetched_at?: string | null;
  tier: string;
  policy_version?: string | null;
  retention_exempt_sample: boolean;
  source_retention_opt_out: boolean;
  purge_eligible_at?: string | null;
  purged_at?: string | null;
  has_raw_payload: boolean;
  has_stored_artifact: boolean;
  digest_id?: string | null;
}

export interface OpsTranscriptRetentionList {
  items: OpsTranscriptRetentionSummary[];
}

export interface OpsTranscriptRetentionPreview {
  transcript: OpsTranscriptRetentionSummary;
  proposed_tier: string;
  purge_eligible: boolean;
  purge_blockers: string[];
  extraction_confidence?: number | null;
  derivative_coverage_ready: boolean;
  missing_query_classes: string[];
}

export interface OpsTranscriptDigest {
  id: string;
  transcript_id: string;
  source_text_hash: string;
  provider: string;
  policy_version?: string | null;
  summary_text: string;
  extraction_versions: string[];
  purged_at: string;
}

export interface OpsTranscriptPurgeResponse {
  transcript: OpsTranscriptRetentionSummary;
  digest: OpsTranscriptDigest;
}

export interface OpsTranscriptReacquireInput {
  actor_name?: string | null;
  note?: string | null;
}

export interface OpsTranscriptReacquireResponse {
  transcript: OpsTranscriptRetentionSummary;
  artifact_id: string;
  prior_digest_id: string;
}

export interface OpsTakedownRequest {
  id: string;
  subject_type: "podcast" | "episode" | "mention";
  subject_id: string;
  requester_type: "creator" | "rights_holder" | "operator";
  requester_name: string;
  requester_email: string;
  basis: string;
  requested_actions: string[];
  status: "pending" | "approved" | "rejected";
  decision_note?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
  submitted_at: string;
}

export interface OpsTakedownRequestList {
  items: OpsTakedownRequest[];
}

export interface OpsTakedownDecisionInput {
  status: "approved" | "rejected";
  actor_name?: string | null;
  note: string;
}

export type OpsPodcastStatus = "watchlist" | "active" | "paused";
export type OpsPodcastSourceFilter = "rss" | "spotify" | "podscripts" | "youtube";
export type OpsPodcastSort = "created_at" | "name" | "episode_count" | "mention_count";

export interface OpsPodcastSources {
  rss_url?: string | null;
  spotify_id?: string | null;
  apple_id?: string | null;
  youtube_channel_id?: string | null;
  podscripts_slug?: string | null;
}

export interface OpsPodcast {
  id: string;
  name: string;
  slug: string;
  status: OpsPodcastStatus;
  description?: string | null;
  cover_url?: string | null;
  created_at: string;
  discovery_source?: string | null;
  episode_count: number;
  mention_count: number;
  sources: OpsPodcastSources;
}

export interface OpsPodcastList {
  items: OpsPodcast[];
  total: number;
  page: number;
  per_page: number;
}

export interface OpsPodcastCreateInput {
  name: string;
  slug: string;
  status: OpsPodcastStatus;
  description?: string | null;
  cover_url?: string | null;
  discovery_source?: string | null;
  sources: OpsPodcastSources;
}

export type OpsPodcastUpdateInput = Partial<OpsPodcastCreateInput>;

export interface AccountUser {
  id: string;
  email: string;
  created_at: string;
  last_signed_in_at?: string | null;
}

export interface AuthSession {
  user: AccountUser;
  expires_at: string;
}

export interface AuthMagicLinkRequestInput {
  email: string;
  redirect_path?: string | null;
}

export interface AuthMagicLinkRequestResponse {
  accepted: boolean;
}

export interface AuthLogoutResponse {
  signed_out: boolean;
}

export interface AccountSavedMedia {
  media: MediaItem;
  saved_at: string;
}

export interface AccountSavedMediaList {
  items: AccountSavedMedia[];
  total: number;
}

export interface AccountSavedMediaDeleteResponse {
  deleted: boolean;
}

export interface AccountFollowedPodcast {
  podcast: Podcast;
  followed_at: string;
}

export interface AccountFollowedPodcastList {
  items: AccountFollowedPodcast[];
  total: number;
}

export interface AccountFollowedPodcastDeleteResponse {
  deleted: boolean;
}

export type AccountAlertTargetType = "media" | "podcast";
export type AccountAlertEventType = "new_mention" | "new_episode";

export interface AccountAlertRule {
  id: string;
  target_type: AccountAlertTargetType;
  target_id: string;
  event_type: AccountAlertEventType;
  baseline_count: number;
  enabled: boolean;
  last_evaluated_at?: string | null;
  created_at: string;
}

export interface AccountAlertRuleList {
  items: AccountAlertRule[];
  total: number;
}

export interface AccountAlertRuleCreateInput {
  target_type: AccountAlertTargetType;
  target_id: string;
  event_type: AccountAlertEventType;
}

export interface AccountAlertRuleDeleteResponse {
  deleted: boolean;
}

export interface AccountAlertEvent {
  id: number;
  rule: AccountAlertRule;
  previous_count: number;
  observed_count: number;
  created_at: string;
}

export interface AccountAlertEvaluation {
  items: AccountAlertEvent[];
  generated: number;
}

export interface AccountDigest {
  id: string;
  channel: string;
  subject: string;
  body_text: string;
  event_count: number;
  created_at: string;
  delivered_at?: string | null;
}

export interface AccountDigestList {
  items: AccountDigest[];
  total: number;
}

export interface AccountDigestSendResponse {
  digest?: AccountDigest | null;
  delivered: boolean;
}

export type AccountDigestFrequency = "immediate" | "daily" | "weekly";

export interface AccountPreference {
  digest_enabled: boolean;
  digest_frequency: AccountDigestFrequency;
  updated_at: string;
}

export interface AccountPreferenceUpdateInput {
  digest_enabled: boolean;
  digest_frequency: AccountDigestFrequency;
}

export interface AccountQuota {
  period: string;
  feature: string;
  limit: number;
  used: number;
  remaining: number;
}

export interface AccountSubscription {
  tier: "free" | "paid";
  status: string;
  paid_access: boolean;
  paid_tier_enabled: boolean;
  paid_features_enforced: boolean;
  quotas: AccountQuota[];
  current_period_ends_at?: string | null;
}

export interface AccountSubscriptionCheckout {
  provider: string;
  checkout_url: string;
}

export interface EditorialCollectionSummary {
  slug: string;
  title: string;
  description: string;
  curator_name?: string | null;
  featured: boolean;
  item_count: number;
  updated_at: string;
}

export interface EditorialCollectionDetail extends EditorialCollectionSummary {
  items: MediaItem[];
}
