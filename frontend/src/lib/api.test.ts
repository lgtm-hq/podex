import { describe, expect, it } from "vitest";
import {
  beginAccountCheckout,
  createAlertRule,
  deleteAlertRule,
  evaluateAlertRules,
  followPodcast,
  getAlertRules,
  getDigests,
  getAccountPreferences,
  getAccountSubscription,
  getFollowedPodcasts,
  getSavedMedia,
  getCurrentAccount,
  getCollection,
  getCollections,
  logoutAccount,
  removeSavedMedia,
  requestMagicLink,
  saveMedia,
  setAlertRuleEnabled,
  sendDigest,
  updateAccountPreferences,
  unfollowPodcast,
  verifyMagicLink,
  addOpsMediaAlias,
  approveOpsReviewItem,
  archiveOpsPodcast,
  createOpsPodcast,
  getEpisode,
  getEpisodeMentions,
  getEpisodes,
  getMedia,
  getMediaById,
  getOverviewStats,
  getOpsDashboard,
  getOpsOperationalMetrics,
  getOpsOperationalAlerts,
  getOpsPipelineActivity,
  getOpsPodcasts,
  getOpsRetentionSampling,
  getOpsTranscriptRetention,
  getOpsReviewQueue,
  getOpsScheduledWork,
  getOpsSearchProjection,
  getOpsSearchAnalytics,
  previewOpsSearchTuning,
  previewOpsTranscriptRetention,
  purgeOpsTranscript,
  reacquireOpsTranscript,
  queueOpsSearchReindex,
  recalculateOpsRetentionSampling,
  evaluateOpsTranscriptRetention,
  getOpsTakedownRequests,
  getOpsMediaDetail,
  getPodcast,
  getPodcasts,
  getStatsByType,
  getTopMedia,
  getTopMentioned,
  globalSearch,
  mergeOpsMedia,
  mergeOpsReviewItem,
  planOpsScheduledWork,
  previewOpsMediaMerge,
  reclassifyOpsReviewItem,
  rejectOpsReviewItem,
  runOpsPipeline,
  searchMedia,
  applyOpsSearchTuning,
  splitOpsReviewItem,
  splitOpsMedia,
  updateOpsPodcast,
  updateOpsMedia,
  upsertOpsMediaExternalRef,
  decideOpsTakedownRequest,
} from "./api";

describe("API Client", () => {
  describe("Accounts", () => {
    it("requests a link, verifies a session, and signs out", async () => {
      const requested = await requestMagicLink({
        email: "reader@example.com",
        redirect_path: "/account",
      });
      const session = await verifyMagicLink("one-time-token");
      const current = await getCurrentAccount();
      const saved = await getSavedMedia();
      const created = await saveMedia("med_2");
      const removed = await removeSavedMedia("med_1");
      const followed = await getFollowedPodcasts();
      const followedCreated = await followPodcast("pod_2");
      const unfollowed = await unfollowPodcast("pod_1");
      const alerts = await getAlertRules();
      const alert = await createAlertRule({
        target_type: "podcast",
        target_id: "pod_2",
        event_type: "new_episode",
      });
      const paused = await setAlertRuleEnabled("alert_1", false);
      const evaluated = await evaluateAlertRules();
      const removedAlert = await deleteAlertRule("alert_1");
      const digests = await getDigests();
      const delivered = await sendDigest();
      const preferences = await getAccountPreferences();
      const updatedPreferences = await updateAccountPreferences({
        digest_enabled: false,
        digest_frequency: "weekly",
      });
      const subscription = await getAccountSubscription();
      const checkout = await beginAccountCheckout();
      const logout = await logoutAccount();

      expect(requested.accepted).toBe(true);
      expect(session.user.email).toBe("reader@example.com");
      expect(current.id).toBe("usr_1");
      expect(saved.items[0].media.id).toBe("med_1");
      expect(created.media.id).toBe("med_2");
      expect(removed.deleted).toBe(true);
      expect(followed.items[0].podcast.slug).toBe("test-podcast");
      expect(followedCreated.podcast.id).toBe("pod_2");
      expect(unfollowed.deleted).toBe(true);
      expect(alerts.items[0].event_type).toBe("new_mention");
      expect(alert.target_id).toBe("pod_2");
      expect(paused.enabled).toBe(false);
      expect(evaluated.generated).toBe(0);
      expect(removedAlert.deleted).toBe(true);
      expect(digests.items[0].id).toBe("mail_1");
      expect(delivered.digest?.event_count).toBe(2);
      expect(preferences.digest_frequency).toBe("daily");
      expect(updatedPreferences.digest_enabled).toBe(false);
      expect(updatedPreferences.digest_frequency).toBe("weekly");
      expect(subscription.quotas[0].remaining).toBe(500);
      expect(checkout.provider).toBe("billing-test");
      expect(logout.signed_out).toBe(true);
    });
  });

  describe("Podcasts", () => {
    it("should fetch all podcasts", async () => {
      const podcasts = await getPodcasts();
      expect(podcasts).toHaveLength(1);
      expect(podcasts[0].id).toBe("pod_1");
      expect(podcasts[0].name).toBe("Test Podcast");
      expect(podcasts[0].slug).toBe("test-podcast");
    });

    it("should fetch a single podcast by slug", async () => {
      const podcast = await getPodcast("test-podcast");
      expect(podcast.id).toBe("pod_1");
      expect(podcast.name).toBe("Test Podcast");
      expect(podcast.episode_count).toBe(10);
    });
  });

  describe("Episodes", () => {
    it("should fetch paginated episodes", async () => {
      const response = await getEpisodes(1, 20);
      expect(response.items).toHaveLength(1);
      expect(response.items[0].id).toBe("ep_1");
      expect(response.total).toBe(1);
      expect(response.page).toBe(1);
    });

    it("should fetch a single episode by ID", async () => {
      const episode = await getEpisode("ep_1");
      expect(episode.id).toBe("ep_1");
      expect(episode.title).toBe("Test Episode");
      expect(episode.mention_count).toBe(5);
      expect(episode.podcast_slug).toBe("test-podcast");
      expect(episode.derivative_summary).toContain("episode explanation");
    });

    it("should fetch episode mentions", async () => {
      const mentions = await getEpisodeMentions("ep_1");
      expect(mentions).toHaveLength(1);
      expect(mentions[0].media_id).toBe("med_1");
      expect(mentions[0].media_title).toBe("Test Book");
      expect(mentions[0].youtube_timestamp_url).toContain("t=120");
    });
  });

  describe("Media", () => {
    it("should fetch paginated media", async () => {
      const response = await getMedia(1, 20);
      expect(response.items).toHaveLength(2);
      expect(response.items[0].id).toBe("med_1");
      expect(response.items[0].type).toBe("book");
      expect(response.items[1].type).toBe("movie");
    });

    it("should fetch media with type filter", async () => {
      const response = await getMedia(1, 20, ["book"]);
      expect(response.items).toHaveLength(1);
      expect(response.items[0].type).toBe("book");
    });

    it("should fetch media by ID", async () => {
      const media = await getMediaById("med_1");
      expect(media.id).toBe("med_1");
      expect(media.title).toBe("Test Book");
      expect(media.google_books_id).toBe("abc123");
      expect(media.mentions[0].episode.id).toBe("ep_1");
      expect(media.derivative_summary).toContain("recommended introduction");
      expect(media.related_media[0].provenance_episode_id).toBe("ep_1");
    });

    it("should search media", async () => {
      const response = await searchMedia("test query");
      expect(response.items).toHaveLength(1);
      expect(response.items[0].id).toBe("med_1");
      expect(response.items[0].title).toContain("test query");
    });

    it("should fetch top media from the media listing", async () => {
      const topMedia = await getTopMedia(1);
      expect(topMedia).toHaveLength(1);
      expect(topMedia[0].id).toBe("med_1");
      expect(topMedia[0].mention_count).toBe(10);
    });
  });

  describe("Stats", () => {
    it("should fetch overview stats", async () => {
      const stats = await getOverviewStats();
      expect(stats.total_podcasts).toBe(5);
      expect(stats.total_episodes).toBe(100);
      expect(stats.total_media).toBe(500);
      expect(stats.total_mentions).toBe(1500);
    });

    it("should fetch stats by type", async () => {
      const stats = await getStatsByType();
      expect(stats).toHaveLength(3);
      expect(stats[0].type).toBe("book");
    });

    it("should fetch top mentioned", async () => {
      const topMentioned = await getTopMentioned(2);
      expect(topMentioned).toHaveLength(2);
      expect(topMentioned[0].id).toBe("med_1");
      expect(topMentioned[0].title).toBe("Top Book");
    });
  });

  describe("Global search", () => {
    it("should normalize podcast URLs to source routes", async () => {
      const response = await globalSearch("test");
      const podcastGroup = response.results.find((group) => group.type === "podcast");

      expect(podcastGroup?.hits[0].id).toBe("pod_1");
      expect(podcastGroup?.hits[0].url).toBe("/sources/test-podcast");
    });
  });

  describe("Collections", () => {
    it("should fetch public editorial collections and their references", async () => {
      const collections = await getCollections();
      const collection = await getCollection("essential-books");

      expect(collections[0].slug).toBe("essential-books");
      expect(collection.item_count).toBe(1);
      expect(collection.items[0].id).toBe("med_1");
    });
  });

  describe("Ops", () => {
    it("should fetch ops dashboard metrics", async () => {
      const dashboard = await getOpsDashboard();

      expect(dashboard.catalog.active_podcasts).toBe(3);
      expect(dashboard.pipelines.projection_repairs_pending).toBe(3);
      expect(dashboard.search.enabled).toBe(true);
      const metrics = await getOpsOperationalMetrics();
      expect(metrics.review.decisions_last_24h).toBe(12);
      expect(metrics.alerts.pending_events).toBe(1);
      const alerts = await getOpsOperationalAlerts();
      expect(alerts.alerts[0].playbook_slug).toBe("projection-lag");
    });

    it("should fetch ops activity surfaces", async () => {
      const activity = await getOpsPipelineActivity();

      expect(activity.runs[0].id).toBe("run_1");
      expect(activity.jobs[0].episode_title).toBe("Test Episode");
    });

    it("should fetch review queue, scheduled work, and search health", async () => {
      const [queue, work, search, analytics] = await Promise.all([
        getOpsReviewQueue(),
        getOpsScheduledWork(),
        getOpsSearchProjection(),
        getOpsSearchAnalytics(),
      ]);

      expect(queue.total).toBe(1);
      expect(work.schedules[0].schedule_key).toBe("daily-ingestion");
      expect(search.healthy).toBe(true);
      expect(analytics.zero_result_searches).toBe(3);
    });

    it("queues scoped reindex work and previews then applies tuning", async () => {
      const reindex = await queueOpsSearchReindex({
        resource_type: "media",
        media_type: "book",
      });
      const tuning = {
        index: "media" as const,
        query: "sci fi",
        synonyms: { "sci fi": ["science fiction"] },
      };
      const preview = await previewOpsSearchTuning(tuning);
      const applied = await applyOpsSearchTuning(tuning);

      expect(reindex.total_queued).toBe(6);
      expect(preview.sample_hits[0].title).toBe("Science Fiction Essentials");
      expect(applied.task_uid).toBe(42);
    });

    it("loads and recalculates retention sampling coverage", async () => {
      const report = await getOpsRetentionSampling();
      const recalculated = await recalculateOpsRetentionSampling({
        policy_version: "retention-sample-v2",
        sample_rate: 0.1,
      });

      expect(report.sampled_count).toBe(9);
      expect(recalculated.policy_version).toBe("retention-sample-v2");
      expect(recalculated.sampled_count).toBe(12);
    });

    it("previews, evaluates, and purges an eligible transcript with a digest", async () => {
      const assets = await getOpsTranscriptRetention();
      const policy = {
        policy_version: "retention-lifecycle-v1",
        hot_days: 30,
        warm_days: 180,
        min_purge_confidence: 0.85,
      };
      const preview = await previewOpsTranscriptRetention("trn_1", policy);
      const evaluated = await evaluateOpsTranscriptRetention("trn_1", policy);
      const purged = await purgeOpsTranscript("trn_1", policy);
      const reacquired = await reacquireOpsTranscript("trn_1", {
        actor_name: "operator",
      });

      expect(assets.items[0].has_raw_payload).toBe(true);
      expect(preview.purge_eligible).toBe(true);
      expect(evaluated.transcript.tier).toBe("cold");
      expect(purged.digest.id).toBe("digest_1");
      expect(reacquired.transcript.tier).toBe("hot");
      expect(reacquired.prior_digest_id).toBe("digest_1");
    });

    it("loads and decides a takedown case", async () => {
      const queue = await getOpsTakedownRequests();
      const decided = await decideOpsTakedownRequest("td_1", {
        status: "approved",
        actor_name: "operator",
        note: "Evidence verified.",
      });

      expect(queue.items[0].requester_type).toBe("rights_holder");
      expect(decided.status).toBe("approved");
      expect(decided.decided_by).toBe("operator");
    });

    it("should list podcasts with management filters", async () => {
      const podcasts = await getOpsPodcasts("active", "rss", "name", "asc");

      expect(podcasts.items[0].id).toBe("pod_1");
      expect(podcasts.items[0].sources.rss_url).toContain("feed.xml");
    });

    it("should create, update, and archive managed podcasts", async () => {
      const payload = {
        name: "New Source",
        slug: "new-source",
        status: "watchlist" as const,
        sources: { rss_url: "https://new.example/feed.xml" },
      };
      const created = await createOpsPodcast(payload);
      const updated = await updateOpsPodcast("pod_1", { status: "paused" });
      const archived = await archiveOpsPodcast("pod_1");

      expect(created.name).toBe("New Source");
      expect(updated.status).toBe("paused");
      expect(archived.status).toBe("paused");
    });

    it("should trigger pipeline runs and plan due work", async () => {
      const [run, planned] = await Promise.all([runOpsPipeline(), planOpsScheduledWork()]);

      expect(run.id).toBe("run_2");
      expect(run.status).toBe("pending");
      expect(planned[0].id).toBe("work_2");
    });

    it("should submit each review queue decision contract", async () => {
      const approved = await approveOpsReviewItem("rev_1", { actor_name: "operator" });
      const rejected = await rejectOpsReviewItem("rev_1", { note: "not supported" });
      const merged = await mergeOpsReviewItem("rev_1", { target_id: "med_1" });
      const reclassified = await reclassifyOpsReviewItem("rev_1", { type: "article" });
      const split = await splitOpsReviewItem("rev_1", {
        candidates: [
          { type: "book", raw_title: "Book One" },
          { type: "article", raw_title: "Article Two" },
        ],
      });

      expect(approved.status).toBe("approved");
      expect(rejected.status).toBe("rejected");
      expect(merged.target_media_id).toBe("med_1");
      expect(reclassified.candidate.type).toBe("article");
      expect(split.original.status).toBe("split");
      expect(split.items).toHaveLength(2);
    });

    it("should preview and perform a canonical media merge", async () => {
      const preview = await previewOpsMediaMerge("med_1", "med_2");
      const merged = await mergeOpsMedia("med_1", "med_2");

      expect(preview.mentions_to_move).toBe(10);
      expect(preview.alias_additions[0].alias).toBe("Test Book");
      expect(merged.target.id).toBe("med_2");
    });

    it("should manage canonical media metadata, aliases, and references", async () => {
      const detail = await getOpsMediaDetail("med_1");
      const updated = await updateOpsMedia("med_1", { title: "Corrected Book" });
      const aliased = await addOpsMediaAlias("med_1", { alias: "Alternate Book" });
      const referenced = await upsertOpsMediaExternalRef("med_1", {
        source: "manual",
        external_id: "ref-1",
        label: "Catalog reference",
      });

      expect(detail.aliases[0].alias).toBe("Test Book");
      expect(updated.media.title).toBe("Corrected Book");
      expect(aliased.aliases[1].alias).toBe("Alternate Book");
      expect(referenced.external_refs[0].external_id).toBe("ref-1");
    });

    it("should recover selected mentions into a split canonical record", async () => {
      const split = await splitOpsMedia("med_1", {
        mention_ids: ["men_1"],
        type: "book",
        title: "Recovered Book",
      });

      expect(split.mentions_moved).toBe(1);
      expect(split.created.media.title).toBe("Recovered Book");
    });
  });
});
