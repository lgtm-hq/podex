import { http, HttpResponse } from "msw";

const API_BASE = "http://localhost:8000/api/v2";

const reviewQueueItem = {
  id: "rev_1",
  status: "pending",
  priority: "high",
  assigned_to: "reviewer@example.com",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
  episode_id: "ep_1",
  episode_title: "Test Episode",
  podcast_id: "pod_1",
  podcast_name: "Test Podcast",
  podcast_slug: "test-podcast",
  candidate: {
    id: "cand_1",
    type: "book",
    raw_title: "Test Book",
    normalized_title: "test book",
    suggested_author: "Example Author",
    timestamp_seconds: 125,
    confidence: 0.92,
    state: "pending_review",
    context: "I recommend Test Book because it makes the argument clearly.",
    extraction_source: "transcript",
    source_job_id: "job_1",
    source_job_status: "completed",
    source_job_backend: "openai",
    source_job_model: "gpt-test",
    source_job_created_at: "2024-01-01T00:00:00Z",
    created_at: "2024-01-01T00:00:00Z",
    extraction_jobs: [
      {
        id: "job_1",
        status: "completed",
        backend: "openai",
        model: "gpt-test",
        created_at: "2024-01-01T00:00:00Z",
        is_source_job: true,
      },
    ],
    provenance: [
      {
        id: "prov_1",
        event_type: "created",
        change_summary: "Candidate extracted from transcript",
        raw_title: "Test Book",
        normalized_title: "test book",
        suggested_author: "Example Author",
        timestamp_seconds: 125,
        confidence: 0.92,
        extraction_source: "transcript",
        changed_fields: [],
        created_at: "2024-01-01T00:00:00Z",
      },
    ],
  },
};

export const handlers = [
  http.post(`${API_BASE}/auth/magic-link/request`, () => {
    return HttpResponse.json({ accepted: true }, { status: 202 });
  }),

  http.post(`${API_BASE}/auth/magic-link/verify`, () => {
    return HttpResponse.json({
      user: {
        id: "usr_1",
        email: "reader@example.com",
        created_at: "2026-05-24T00:00:00Z",
        last_signed_in_at: "2026-05-24T00:01:00Z",
      },
      expires_at: "2026-06-23T00:01:00Z",
    });
  }),

  http.get(`${API_BASE}/me`, () => {
    return HttpResponse.json({
      id: "usr_1",
      email: "reader@example.com",
      created_at: "2026-05-24T00:00:00Z",
      last_signed_in_at: "2026-05-24T00:01:00Z",
    });
  }),

  http.post(`${API_BASE}/auth/logout`, () => {
    return HttpResponse.json({ signed_out: true });
  }),

  http.get(`${API_BASE}/me/saves`, () => {
    return HttpResponse.json({
      items: [
        {
          media: {
            id: "med_1",
            type: "book",
            title: "Test Book",
            author: "Test Author",
            cover_url: null,
            year: 2020,
            description: "A test book",
            mention_count: 10,
            episode_count: 5,
            created_at: "2024-01-01T00:00:00Z",
          },
          saved_at: "2026-05-24T00:02:00Z",
        },
      ],
      total: 1,
    });
  }),

  http.put(`${API_BASE}/me/saves/:mediaId`, ({ params }) => {
    return HttpResponse.json({
      media: {
        id: params.mediaId,
        type: "movie",
        title: "Test Movie",
        author: "Test Director",
        mention_count: 4,
        episode_count: 2,
        created_at: "2024-01-01T00:00:00Z",
      },
      saved_at: "2026-05-24T00:03:00Z",
    });
  }),

  http.delete(`${API_BASE}/me/saves/:mediaId`, () => {
    return HttpResponse.json({ deleted: true });
  }),

  http.get(`${API_BASE}/me/follows`, () => {
    return HttpResponse.json({
      items: [
        {
          podcast: {
            id: "pod_1",
            name: "Test Podcast",
            slug: "test-podcast",
            description: "A test podcast",
            cover_url: null,
            created_at: "2024-01-01T00:00:00Z",
            episode_count: 10,
            mention_count: 50,
          },
          followed_at: "2026-05-24T00:04:00Z",
        },
      ],
      total: 1,
    });
  }),

  http.put(`${API_BASE}/me/follows/:podcastId`, ({ params }) => {
    return HttpResponse.json({
      podcast: {
        id: params.podcastId,
        name: "Second Podcast",
        slug: "second-podcast",
        created_at: "2024-01-01T00:00:00Z",
        episode_count: 3,
        mention_count: 9,
      },
      followed_at: "2026-05-24T00:05:00Z",
    });
  }),

  http.delete(`${API_BASE}/me/follows/:podcastId`, () => {
    return HttpResponse.json({ deleted: true });
  }),

  http.get(`${API_BASE}/me/alerts`, () => {
    return HttpResponse.json({
      items: [
        {
          id: "alert_1",
          target_type: "media",
          target_id: "med_1",
          event_type: "new_mention",
          baseline_count: 10,
          enabled: true,
          created_at: "2026-05-24T00:06:00Z",
        },
      ],
      total: 1,
    });
  }),

  http.post(`${API_BASE}/me/alerts`, async ({ request }) => {
    const input = (await request.json()) as {
      target_type: "media" | "podcast";
      target_id: string;
      event_type: "new_mention" | "new_episode";
    };
    return HttpResponse.json({
      id: "alert_2",
      ...input,
      baseline_count: 0,
      enabled: true,
      created_at: "2026-05-24T00:07:00Z",
    });
  }),

  http.patch(`${API_BASE}/me/alerts/:ruleId`, async ({ params, request }) => {
    const input = (await request.json()) as { enabled: boolean };
    return HttpResponse.json({
      id: params.ruleId,
      target_type: "media",
      target_id: "med_1",
      event_type: "new_mention",
      baseline_count: 10,
      enabled: input.enabled,
      created_at: "2026-05-24T00:06:00Z",
    });
  }),

  http.delete(`${API_BASE}/me/alerts/:ruleId`, () => {
    return HttpResponse.json({ deleted: true });
  }),

  http.post(`${API_BASE}/me/alerts/evaluate`, () => {
    return HttpResponse.json({ items: [], generated: 0 });
  }),

  http.get(`${API_BASE}/me/digests`, () => {
    return HttpResponse.json({
      items: [
        {
          id: "mail_1",
          channel: "email",
          subject: "Podex digest: 1 new update",
          body_text: "New activity from your Podex alerts",
          event_count: 1,
          created_at: "2026-05-24T00:08:00Z",
          delivered_at: "2026-05-24T00:08:01Z",
        },
      ],
      total: 1,
    });
  }),

  http.post(`${API_BASE}/me/digests/send`, () => {
    return HttpResponse.json({
      delivered: true,
      digest: {
        id: "mail_2",
        channel: "email",
        subject: "Podex digest: 2 new updates",
        body_text: "New activity from your Podex alerts",
        event_count: 2,
        created_at: "2026-05-24T00:09:00Z",
        delivered_at: "2026-05-24T00:09:01Z",
      },
    });
  }),

  http.get(`${API_BASE}/me/preferences`, () => {
    return HttpResponse.json({
      digest_enabled: true,
      digest_frequency: "daily",
      updated_at: "2026-05-24T00:10:00Z",
    });
  }),

  http.patch(`${API_BASE}/me/preferences`, async ({ request }) => {
    const input = (await request.json()) as {
      digest_enabled: boolean;
      digest_frequency: "immediate" | "daily" | "weekly";
    };
    return HttpResponse.json({
      ...input,
      updated_at: "2026-05-24T00:11:00Z",
    });
  }),

  http.get(`${API_BASE}/me/subscription`, () => {
    return HttpResponse.json({
      tier: "free",
      status: "active",
      paid_access: false,
      paid_tier_enabled: false,
      paid_features_enforced: false,
      quotas: [
        {
          period: "2026-05",
          feature: "api_requests",
          limit: 500,
          used: 0,
          remaining: 500,
        },
        {
          period: "2026-05",
          feature: "llm_requests",
          limit: 25,
          used: 0,
          remaining: 25,
        },
      ],
      current_period_ends_at: null,
    });
  }),

  http.post(`${API_BASE}/me/subscription/checkout`, () => {
    return HttpResponse.json({
      provider: "billing-test",
      checkout_url: "https://billing.example/checkout",
    });
  }),

  // Podcasts
  http.get(`${API_BASE}/podcasts`, () => {
    return HttpResponse.json([
      {
        id: "pod_1",
        name: "Test Podcast",
        slug: "test-podcast",
        description: "A test podcast",
        cover_url: null,
        created_at: "2024-01-01T00:00:00Z",
        episode_count: 10,
        mention_count: 50,
      },
    ]);
  }),

  http.get(`${API_BASE}/podcasts/:slug`, ({ params }) => {
    return HttpResponse.json({
      id: "pod_1",
      name: "Test Podcast",
      slug: params.slug,
      description: "A test podcast",
      cover_url: null,
      created_at: "2024-01-01T00:00:00Z",
      episode_count: 10,
      mention_count: 50,
    });
  }),

  // Episodes
  http.get(`${API_BASE}/episodes`, () => {
    return HttpResponse.json({
      items: [
        {
          id: "ep_1",
          podcast_id: "pod_1",
          title: "Test Episode",
          episode_number: 1,
          youtube_id: "abc123",
          published_at: "2024-01-01T00:00:00Z",
          duration_seconds: 3600,
          thumbnail_url: null,
          transcript_status: "completed",
          created_at: "2024-01-01T00:00:00Z",
          mention_count: 5,
        },
      ],
      total: 1,
      page: 1,
      per_page: 20,
    });
  }),

  http.get(`${API_BASE}/episodes/:id`, ({ params }) => {
    return HttpResponse.json({
      id: params.id,
      podcast_id: "pod_1",
      title: "Test Episode",
      episode_number: 1,
      youtube_id: "abc123",
      published_at: "2024-01-01T00:00:00Z",
      duration_seconds: 3600,
      thumbnail_url: null,
      transcript_status: "completed",
      created_at: "2024-01-01T00:00:00Z",
      mention_count: 5,
      podcast_name: "Test Podcast",
      podcast_slug: "test-podcast",
      extraction_status: "completed",
      cleanup_status: "completed",
      derivative_summary: "A compact episode explanation.",
      derivative_mentioned_media_titles: [],
      derivative_evidence: [],
    });
  }),

  http.get(`${API_BASE}/episodes/:id/mentions`, () => {
    return HttpResponse.json([
      {
        id: "men_1",
        media: {
          id: "med_1",
          title: "Test Book",
          type: "book",
          author: "Test Author",
          cover_url: null,
        },
        timestamp_seconds: 120,
        context: "Mentioned this book",
        confidence: 0.95,
        youtube_timestamp_url: "https://youtube.com/watch?v=abc123&t=120",
      },
    ]);
  }),

  // Media
  http.get(`${API_BASE}/media`, ({ request }) => {
    const url = new URL(request.url);
    const type = url.searchParams.get("type");
    const perPage = Number(url.searchParams.get("per_page") || "20");
    const items = [
      {
        id: "med_1",
        type: "book",
        title: "Test Book",
        author: "Test Author",
        cover_url: null,
        year: 2020,
        description: "A test book",
        mention_count: 10,
        episode_count: 5,
        created_at: "2024-01-01T00:00:00Z",
      },
      {
        id: "med_2",
        type: "movie",
        title: "Test Movie",
        author: "Test Director",
        cover_url: null,
        year: 2021,
        description: "A test movie",
        mention_count: 8,
        episode_count: 4,
        created_at: "2024-01-02T00:00:00Z",
      },
    ];
    const filteredItems = type ? items.filter((item) => item.type === type) : items;

    return HttpResponse.json({
      items: filteredItems.slice(0, perPage),
      total: filteredItems.length,
      page: 1,
      per_page: perPage,
    });
  }),

  http.get(`${API_BASE}/media/search`, ({ request }) => {
    const url = new URL(request.url);
    const query = url.searchParams.get("q") || "";

    return HttpResponse.json({
      items: [
        {
          id: "med_1",
          type: "book",
          title: `Search result for: ${query}`,
          author: "Test Author",
          cover_url: null,
          year: 2020,
          description: "A matching book",
          mention_count: 10,
          episode_count: 5,
          created_at: "2024-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      per_page: 20,
    });
  }),

  http.get(`${API_BASE}/media/:id`, ({ params }) => {
    return HttpResponse.json({
      id: params.id,
      type: "book",
      title: "Test Book",
      author: "Test Author",
      cover_url: null,
      year: 2020,
      description: "A test book",
      mention_count: 10,
      episode_count: 5,
      created_at: "2024-01-01T00:00:00Z",
      mentions: [
        {
          id: "men_1",
          episode: {
            id: "ep_1",
            title: "Test Episode",
            episode_number: 1,
            youtube_id: "abc123",
            published_at: "2024-01-01T00:00:00Z",
            thumbnail_url: null,
          },
          timestamp_seconds: 120,
          context: "Mentioned this book",
          confidence: 0.95,
          youtube_timestamp_url: "https://youtube.com/watch?v=abc123&t=120",
        },
      ],
      derivative_summary: "A recommended introduction to its subject.",
      related_media: [
        {
          id: "med_2",
          type: "movie",
          title: "Test Adaptation",
          relation_type: "adapted_from",
          direction: "outgoing",
          source: "episode_extraction",
          confidence: 0.91,
          evidence_text: "The adaptation follows this book.",
          provenance_episode_id: "ep_1",
        },
      ],
      google_books_id: "abc123",
    });
  }),

  http.get(`${API_BASE}/ops/media/:id`, ({ params }) => {
    return HttpResponse.json({
      media: {
        id: params.id,
        type: "book",
        title: params.id === "med_1" ? "Test Book" : "Canonical Book",
        author: params.id === "med_1" ? "Test Author" : null,
        description: "A managed canonical record",
        mention_count: params.id === "med_1" ? 10 : 8,
        episode_count: 5,
      },
      google_books_id: params.id === "med_1" ? "abc123" : null,
      verification_sources: ["google_books"],
      aliases: [
        {
          alias: params.id === "med_1" ? "Test Book" : "Canonical Book",
          normalized_alias: params.id === "med_1" ? "test book" : "canonical book",
          source: "manual",
          is_primary: true,
        },
      ],
      external_refs: [],
      relations: [],
      mentions:
        params.id === "med_1"
          ? [
              {
                id: "men_1",
                episode_id: "ep_1",
                episode_title: "Test Episode",
                context: "The mention needs canonical recovery.",
                confidence: 0.95,
              },
            ]
          : [],
    });
  }),

  http.patch(`${API_BASE}/ops/media/:id`, async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      media: {
        id: params.id,
        type: body.type ?? "book",
        title: body.title ?? "Test Book",
        author: body.author ?? "Test Author",
        description: body.description ?? "A managed canonical record",
        mention_count: 10,
        episode_count: 5,
      },
      google_books_id: body.google_books_id ?? "abc123",
      verification_sources: ["google_books"],
      aliases: [
        { alias: "Test Book", normalized_alias: "test book", source: "manual", is_primary: false },
        {
          alias: body.title ?? "Test Book",
          normalized_alias: "corrected book",
          source: "manual",
          is_primary: true,
        },
      ],
      external_refs: [],
      relations: [],
      mentions: [],
    });
  }),

  http.post(`${API_BASE}/ops/media/:id/aliases`, async ({ params, request }) => {
    const body = (await request.json()) as { alias: string };
    return HttpResponse.json({
      media: {
        id: params.id,
        type: "book",
        title: "Test Book",
        author: "Test Author",
        mention_count: 10,
        episode_count: 5,
      },
      verification_sources: [],
      aliases: [
        { alias: "Test Book", normalized_alias: "test book", source: "manual", is_primary: true },
        {
          alias: body.alias,
          normalized_alias: body.alias.toLowerCase(),
          source: "manual",
          is_primary: false,
        },
      ],
      external_refs: [],
      relations: [],
      mentions: [],
    });
  }),

  http.post(`${API_BASE}/ops/media/:id/external-refs`, async ({ params, request }) => {
    const body = (await request.json()) as {
      source: string;
      external_id: string;
      url?: string;
      label?: string;
    };
    return HttpResponse.json({
      media: {
        id: params.id,
        type: "book",
        title: "Test Book",
        author: "Test Author",
        mention_count: 10,
        episode_count: 5,
      },
      verification_sources: [],
      aliases: [],
      external_refs: [body],
      relations: [],
      mentions: [],
    });
  }),

  http.post(`${API_BASE}/ops/media/:id/split`, async ({ params, request }) => {
    const body = (await request.json()) as { title: string; type: string; mention_ids: string[] };
    return HttpResponse.json({
      source: {
        media: {
          id: params.id,
          type: "book",
          title: "Test Book",
          mention_count: 9,
          episode_count: 5,
        },
        verification_sources: [],
        aliases: [],
        external_refs: [],
        relations: [],
        mentions: [],
      },
      created: {
        media: {
          id: "med_3",
          type: body.type,
          title: body.title,
          mention_count: 1,
          episode_count: 1,
        },
        verification_sources: [],
        aliases: [
          {
            alias: body.title,
            normalized_alias: body.title.toLowerCase(),
            source: "manual",
            is_primary: true,
          },
        ],
        external_refs: [],
        relations: [],
        mentions: body.mention_ids.map((id) => ({
          id,
          episode_id: "ep_1",
          episode_title: "Test Episode",
          confidence: 0.95,
        })),
      },
      mentions_moved: body.mention_ids.length,
    });
  }),

  http.get(`${API_BASE}/ops/media/:id/merge-preview`, ({ params, request }) => {
    const targetId = new URL(request.url).searchParams.get("target_id") ?? "med_2";
    return HttpResponse.json({
      source: {
        id: params.id,
        type: "book",
        title: "Test Book",
        author: "Test Author",
        mention_count: 10,
        episode_count: 5,
      },
      target: {
        id: targetId,
        type: "book",
        title: "Canonical Book",
        mention_count: 8,
        episode_count: 4,
      },
      field_changes: [
        {
          field: "author",
          source_value: "Test Author",
          target_value: null,
          merged_value: "Test Author",
        },
      ],
      alias_additions: [
        {
          alias: "Test Book",
          normalized_alias: "test book",
          source: "merge",
        },
      ],
      mentions_to_move: 10,
    });
  }),

  http.post(`${API_BASE}/ops/media/:id/merge`, ({ params }) => {
    return HttpResponse.json({
      source_id: params.id,
      target: {
        id: "med_2",
        type: "book",
        title: "Canonical Book",
        author: "Test Author",
        mention_count: 18,
        episode_count: 9,
      },
    });
  }),

  http.get(`${API_BASE}/trends`, ({ request }) => {
    const url = new URL(request.url);
    const limit = Number(url.searchParams.get("limit") || "10");

    return HttpResponse.json({
      overview: {
        total_podcasts: 5,
        total_episodes: 100,
        total_media: 500,
        total_mentions: 1500,
        total_books: 200,
        total_movies: 150,
      },
      by_type: [
        { type: "book", count: 200, mention_count: 600 },
        { type: "movie", count: 150, mention_count: 400 },
        { type: "documentary", count: 50, mention_count: 100 },
      ],
      top_mentioned: [
        {
          id: "med_1",
          title: "Top Book",
          type: "book",
          author: "Popular Author",
          mention_count: 100,
        },
        {
          id: "med_2",
          title: "Top Movie",
          type: "movie",
          author: "Famous Director",
          mention_count: 80,
        },
      ].slice(0, limit),
    });
  }),

  http.get(`${API_BASE}/search`, () => {
    return HttpResponse.json({
      query: "test",
      processing_time_ms: 4,
      results: [
        {
          type: "podcast",
          total: 1,
          hits: [
            {
              id: "pod_1",
              type: "podcast",
              title: "Test Podcast",
              subtitle: "A test podcast",
              url: "/podcasts/test-podcast",
            },
          ],
        },
      ],
    });
  }),

  http.post(`${API_BASE}/search/selection`, () => {
    return HttpResponse.json({ recorded: true });
  }),

  http.get(`${API_BASE}/collections`, () => {
    return HttpResponse.json([
      {
        slug: "essential-books",
        title: "Essential Books",
        description: "Books repeatedly discussed across the catalog.",
        curator_name: "Podex Editors",
        featured: true,
        item_count: 1,
        updated_at: "2026-05-24T00:00:00Z",
      },
    ]);
  }),

  http.get(`${API_BASE}/collections/:slug`, ({ params }) => {
    return HttpResponse.json({
      slug: params.slug,
      title: "Essential Books",
      description: "Books repeatedly discussed across the catalog.",
      curator_name: "Podex Editors",
      featured: true,
      item_count: 1,
      updated_at: "2026-05-24T00:00:00Z",
      items: [
        {
          id: "med_1",
          type: "book",
          title: "Test Book",
          author: "Test Author",
          mention_count: 10,
          episode_count: 2,
          created_at: "2024-01-01T00:00:00Z",
        },
      ],
    });
  }),

  http.get(`${API_BASE}/ops/dashboard`, () => {
    return HttpResponse.json({
      catalog: {
        total_podcasts: 5,
        active_podcasts: 3,
        watchlist_podcasts: 1,
        paused_podcasts: 1,
      },
      sources: {
        with_rss: 4,
        with_spotify: 3,
        with_podscripts: 2,
        with_youtube: 1,
      },
      episodes: {
        total_known: 100,
        transcribed: 80,
        extracted: 75,
      },
      pipelines: {
        ingestion_runs_total: 12,
        ingestion_runs_in_progress: 1,
        ingestion_runs_failed: 2,
        ingestion_runs_completed: 9,
        transcription_jobs_pending: 4,
        transcription_jobs_failed: 1,
        transcription_jobs_in_progress: 2,
        projection_repairs_pending: 3,
        projection_repairs_failed: 1,
      },
      search: {
        enabled: true,
      },
    });
  }),

  http.get(`${API_BASE}/ops/metrics`, () => {
    return HttpResponse.json({
      measured_at: "2026-05-24T08:00:00Z",
      review: {
        pending_items: 4,
        decisions_last_24h: 12,
        median_decision_minutes_last_24h: 18,
      },
      projection: {
        pending_repairs: 3,
        failed_repairs: 1,
        oldest_pending_age_seconds: 3600,
      },
      alerts: {
        generated_events_last_24h: 9,
        delivered_digests_last_24h: 3,
        delivered_events_last_24h: 8,
        pending_events: 1,
      },
    });
  }),

  http.get(`${API_BASE}/ops/alerts`, () => {
    return HttpResponse.json({
      measured_at: "2026-05-24T08:00:00Z",
      alerts: [
        {
          key: "projection_age",
          severity: "critical",
          title: "Search projection repair is stale",
          message: "At least one projection repair has waited beyond its SLA.",
          current_value: 90,
          threshold: 60,
          playbook_slug: "projection-lag",
        },
      ],
    });
  }),

  http.get(`${API_BASE}/ops/pipelines`, () => {
    return HttpResponse.json({
      summary: {
        ingestion_runs_total: 12,
        ingestion_runs_in_progress: 1,
        ingestion_runs_failed: 2,
        ingestion_runs_completed: 9,
        transcription_jobs_pending: 4,
        transcription_jobs_failed: 1,
        transcription_jobs_in_progress: 2,
        projection_repairs_pending: 3,
        projection_repairs_failed: 1,
      },
      runs: [
        {
          id: "run_1",
          status: "completed",
          created_at: "2024-01-01T00:00:00Z",
          started_at: "2024-01-01T00:01:00Z",
          completed_at: "2024-01-01T00:02:00Z",
          duration_seconds: 60,
        },
      ],
      jobs: [
        {
          id: "job_1",
          episode_id: "ep_1",
          podcast_id: "pod_1",
          podcast_name: "Test Podcast",
          podcast_slug: "test-podcast",
          episode_title: "Test Episode",
          job_type: "extract",
          status: "completed",
          created_at: "2024-01-01T00:00:00Z",
        },
      ],
    });
  }),

  http.get(`${API_BASE}/ops/review-queue`, ({ request }) => {
    const url = new URL(request.url);
    const status = url.searchParams.get("status");
    const priority = url.searchParams.get("priority");
    const items =
      (!status || status === reviewQueueItem.status) &&
      (!priority || priority === reviewQueueItem.priority)
        ? [reviewQueueItem]
        : [];
    return HttpResponse.json({
      items,
      total: items.length,
      page: 1,
      per_page: 100,
    });
  }),

  http.post(`${API_BASE}/ops/review-queue/:id/approve`, () => {
    return HttpResponse.json({
      ...reviewQueueItem,
      status: "approved",
      candidate: { ...reviewQueueItem.candidate, state: "published", media_id: "med_1" },
    });
  }),

  http.post(`${API_BASE}/ops/review-queue/:id/reject`, () => {
    return HttpResponse.json({
      ...reviewQueueItem,
      status: "rejected",
      candidate: { ...reviewQueueItem.candidate, state: "rejected" },
    });
  }),

  http.post(`${API_BASE}/ops/review-queue/:id/merge`, async ({ request }) => {
    const body = (await request.json()) as { target_id: string };
    return HttpResponse.json({
      ...reviewQueueItem,
      status: "merged",
      target_media_id: body.target_id,
      candidate: { ...reviewQueueItem.candidate, state: "merged", media_id: body.target_id },
    });
  }),

  http.post(`${API_BASE}/ops/review-queue/:id/reclassify`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      ...reviewQueueItem,
      candidate: { ...reviewQueueItem.candidate, ...body },
    });
  }),

  http.post(`${API_BASE}/ops/review-queue/:id/split`, () => {
    return HttpResponse.json({
      original: {
        ...reviewQueueItem,
        status: "split",
        candidate: { ...reviewQueueItem.candidate, state: "split" },
      },
      items: [
        { ...reviewQueueItem, id: "rev_2" },
        { ...reviewQueueItem, id: "rev_3" },
      ],
    });
  }),

  http.get(`${API_BASE}/ops/scheduled-work`, () => {
    return HttpResponse.json({
      schedules: [
        {
          id: "sch_1",
          schedule_key: "daily-ingestion",
          task_kind: "ingestion",
          interval_minutes: 1440,
          enabled: true,
          next_due_at: "2024-01-02T00:00:00Z",
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
        },
      ],
      work_items: [
        {
          id: "work_1",
          schedule_id: "sch_1",
          schedule_key: "daily-ingestion",
          work_key: "daily-ingestion:2024-01-02",
          task_kind: "ingestion",
          status: "pending",
          due_at: "2024-01-02T00:00:00Z",
          interval_minutes: 1440,
          created_at: "2024-01-01T00:00:00Z",
        },
      ],
    });
  }),

  http.post(`${API_BASE}/ops/scheduled-work/plan`, () => {
    return HttpResponse.json([
      {
        id: "work_2",
        schedule_id: "sch_1",
        schedule_key: "daily-ingestion",
        work_key: "daily-ingestion:2024-01-03",
        task_kind: "ingestion",
        status: "pending",
        due_at: "2024-01-03T00:00:00Z",
        interval_minutes: 1440,
        created_at: "2024-01-02T00:00:00Z",
      },
    ]);
  }),

  http.post(`${API_BASE}/ops/pipelines/run`, () => {
    return HttpResponse.json(
      {
        id: "run_2",
        status: "pending",
        created_at: "2024-01-02T00:00:00Z",
      },
      { status: 202 },
    );
  }),

  http.get(`${API_BASE}/ops/search`, () => {
    return HttpResponse.json({
      configured: true,
      healthy: true,
      indexes: [
        {
          name: "media",
          document_count: 100,
          is_indexing: false,
        },
      ],
      repair_summary: {
        pending: 3,
        failed: 1,
        completed: 8,
      },
      repairs: [
        {
          id: "repair_1",
          resource_type: "media",
          resource_id: "med_1",
          status: "pending",
          reason: "manual_reindex",
          created_at: "2024-01-02T00:00:00Z",
          updated_at: "2024-01-02T00:00:00Z",
        },
      ],
    });
  }),

  http.get(`${API_BASE}/ops/search/analytics`, () => {
    return HttpResponse.json({
      searches: 24,
      zero_result_searches: 3,
      selections: 11,
      queries: [
        {
          query: "sci fi",
          searches: 5,
          zero_result_searches: 1,
          selections: 3,
        },
      ],
    });
  }),

  http.post(`${API_BASE}/ops/search/reindex`, () => {
    return HttpResponse.json({
      media_queued: 4,
      episodes_queued: 2,
      total_queued: 6,
    });
  }),

  http.post(`${API_BASE}/ops/search/tuning/preview`, () => {
    return HttpResponse.json({
      index: "media",
      query: "sci fi",
      sample_hits: [{ title: "Science Fiction Essentials" }],
      proposed_settings: {
        synonyms: { "sci fi": ["science fiction"] },
      },
    });
  }),

  http.post(`${API_BASE}/ops/search/tuning`, () => {
    return HttpResponse.json({
      index: "media",
      status: "enqueued",
      task_uid: 42,
    });
  }),

  http.get(`${API_BASE}/ops/retention/sampling`, () => {
    return HttpResponse.json({
      policy_version: "retention-sample-v1",
      sample_rate: 0.075,
      eligible_count: 120,
      sampled_count: 9,
      target_count: 9,
      strata: [
        {
          source: "test-podcast",
          topic: "book",
          confidence_band: "high",
          age_bucket: "recent",
          eligible_count: 40,
          sampled_count: 3,
          target_count: 3,
        },
      ],
    });
  }),

  http.post(`${API_BASE}/ops/retention/sampling/recalculate`, () => {
    return HttpResponse.json({
      policy_version: "retention-sample-v2",
      sample_rate: 0.1,
      eligible_count: 120,
      sampled_count: 12,
      target_count: 12,
      strata: [
        {
          source: "test-podcast",
          topic: "book",
          confidence_band: "high",
          age_bucket: "recent",
          eligible_count: 40,
          sampled_count: 4,
          target_count: 4,
        },
      ],
    });
  }),

  http.get(`${API_BASE}/ops/retention/transcripts`, () => {
    return HttpResponse.json({
      items: [
        {
          id: "trn_1",
          episode_id: "ep_1",
          episode_title: "Retention Ready Episode",
          podcast_name: "Test Podcast",
          provider: "rss",
          tier: "hot",
          policy_version: null,
          retention_exempt_sample: false,
          source_retention_opt_out: false,
          has_raw_payload: true,
          has_stored_artifact: true,
          digest_id: null,
        },
      ],
    });
  }),

  http.post(`${API_BASE}/ops/retention/transcripts/:id/preview`, () => {
    return HttpResponse.json({
      transcript: {
        id: "trn_1",
        episode_id: "ep_1",
        episode_title: "Retention Ready Episode",
        podcast_name: "Test Podcast",
        provider: "rss",
        tier: "hot",
        retention_exempt_sample: false,
        source_retention_opt_out: false,
        has_raw_payload: true,
        has_stored_artifact: true,
      },
      proposed_tier: "cold",
      purge_eligible: true,
      purge_blockers: [],
      extraction_confidence: 0.97,
      derivative_coverage_ready: true,
      missing_query_classes: [],
    });
  }),

  http.post(`${API_BASE}/ops/retention/transcripts/:id/evaluate`, () => {
    return HttpResponse.json({
      transcript: {
        id: "trn_1",
        episode_id: "ep_1",
        episode_title: "Retention Ready Episode",
        podcast_name: "Test Podcast",
        provider: "rss",
        tier: "cold",
        policy_version: "retention-lifecycle-v1",
        retention_exempt_sample: false,
        source_retention_opt_out: false,
        has_raw_payload: true,
        has_stored_artifact: true,
      },
      proposed_tier: "cold",
      purge_eligible: true,
      purge_blockers: [],
      extraction_confidence: 0.97,
      derivative_coverage_ready: true,
      missing_query_classes: [],
    });
  }),

  http.post(`${API_BASE}/ops/retention/transcripts/:id/purge`, () => {
    return HttpResponse.json({
      transcript: {
        id: "trn_1",
        episode_id: "ep_1",
        episode_title: "Retention Ready Episode",
        podcast_name: "Test Podcast",
        provider: "rss",
        tier: "purged",
        policy_version: "retention-lifecycle-v1",
        retention_exempt_sample: false,
        source_retention_opt_out: false,
        has_raw_payload: false,
        has_stored_artifact: false,
        digest_id: "digest_1",
      },
      digest: {
        id: "digest_1",
        transcript_id: "trn_1",
        source_text_hash: "abcdef",
        provider: "rss",
        policy_version: "retention-lifecycle-v1",
        summary_text: "Processing proof summary.",
        extraction_versions: ["derivatives-v1"],
        purged_at: "2026-05-24T00:00:00Z",
      },
    });
  }),

  http.post(`${API_BASE}/ops/retention/transcripts/:id/reacquire`, () => {
    return HttpResponse.json({
      transcript: {
        id: "trn_2",
        episode_id: "ep_1",
        episode_title: "Retention Ready Episode",
        podcast_name: "Test Podcast",
        provider: "rss",
        tier: "hot",
        policy_version: null,
        retention_exempt_sample: false,
        source_retention_opt_out: false,
        has_raw_payload: true,
        has_stored_artifact: true,
        digest_id: null,
      },
      artifact_id: "artifact_1",
      prior_digest_id: "digest_1",
    });
  }),

  http.get(`${API_BASE}/ops/takedown-requests`, () => {
    return HttpResponse.json({
      items: [
        {
          id: "td_1",
          subject_type: "podcast",
          subject_id: "pod_1",
          requester_type: "rights_holder",
          requester_name: "Rights Holder",
          requester_email: "rights@example.com",
          basis: "I control distribution rights for this catalog source.",
          requested_actions: ["suppress_raw_transcript", "purge_search_projection"],
          status: "pending",
          submitted_at: "2026-05-24T00:00:00Z",
        },
      ],
    });
  }),

  http.post(`${API_BASE}/ops/takedown-requests/:id/decision`, async ({ request }) => {
    const payload = (await request.json()) as {
      status: "approved" | "rejected";
      actor_name?: string;
    };
    return HttpResponse.json({
      id: "td_1",
      subject_type: "podcast",
      subject_id: "pod_1",
      requester_type: "rights_holder",
      requester_name: "Rights Holder",
      requester_email: "rights@example.com",
      basis: "I control distribution rights for this catalog source.",
      requested_actions: ["suppress_raw_transcript", "purge_search_projection"],
      status: payload.status,
      decided_by: payload.actor_name ?? null,
      decision_note: "Evidence verified.",
      submitted_at: "2026-05-24T00:00:00Z",
    });
  }),

  http.get(`${API_BASE}/ops/podcasts`, ({ request }) => {
    const url = new URL(request.url);
    const status = url.searchParams.get("status");
    const items = [
      {
        id: "pod_1",
        name: "Test Podcast",
        slug: "test-podcast",
        status: "active",
        description: "A test podcast",
        cover_url: null,
        created_at: "2024-01-01T00:00:00Z",
        discovery_source: "rss",
        episode_count: 10,
        mention_count: 50,
        sources: {
          rss_url: "https://example.com/feed.xml",
          spotify_id: null,
          apple_id: null,
          youtube_channel_id: null,
          podscripts_slug: null,
        },
      },
    ].filter((podcast) => !status || podcast.status === status);

    return HttpResponse.json({
      items,
      total: items.length,
      page: 1,
      per_page: 100,
    });
  }),

  http.post(`${API_BASE}/ops/podcasts`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      {
        ...body,
        id: "pod_2",
        created_at: "2024-01-02T00:00:00Z",
        episode_count: 0,
        mention_count: 0,
      },
      { status: 201 },
    );
  }),

  http.patch(`${API_BASE}/ops/podcasts/:id`, async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      id: params.id,
      name: "Test Podcast",
      slug: "test-podcast",
      status: "active",
      description: "A test podcast",
      cover_url: null,
      created_at: "2024-01-01T00:00:00Z",
      discovery_source: "rss",
      episode_count: 10,
      mention_count: 50,
      sources: {
        rss_url: "https://example.com/feed.xml",
        spotify_id: null,
        apple_id: null,
        youtube_channel_id: null,
        podscripts_slug: null,
      },
      ...body,
    });
  }),

  http.post(`${API_BASE}/ops/podcasts/:id/archive`, ({ params }) => {
    return HttpResponse.json({
      id: params.id,
      name: "Test Podcast",
      slug: "test-podcast",
      status: "paused",
      description: "A test podcast",
      cover_url: null,
      created_at: "2024-01-01T00:00:00Z",
      discovery_source: "rss",
      episode_count: 10,
      mention_count: 50,
      sources: {
        rss_url: "https://example.com/feed.xml",
        spotify_id: null,
        apple_id: null,
        youtube_channel_id: null,
        podscripts_slug: null,
      },
    });
  }),
];
