import { http, HttpResponse } from "msw";

const API_BASE = "http://localhost:8000/api/v1";

export const handlers = [
  // Podcasts
  http.get(`${API_BASE}/podcasts`, () => {
    return HttpResponse.json([
      {
        id: 1,
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
      id: 1,
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
          id: 1,
          podcast_id: 1,
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
      id: Number(params.id),
      podcast_id: 1,
      title: "Test Episode",
      episode_number: 1,
      youtube_id: "abc123",
      published_at: "2024-01-01T00:00:00Z",
      duration_seconds: 3600,
      thumbnail_url: null,
      transcript_status: "completed",
      created_at: "2024-01-01T00:00:00Z",
      mention_count: 5,
    });
  }),

  http.get(`${API_BASE}/episodes/:id/mentions`, () => {
    return HttpResponse.json([
      {
        id: 1,
        media_id: 1,
        media_title: "Test Book",
        media_type: "book",
        media_author: "Test Author",
        timestamp_seconds: 120,
        context: "Mentioned this book",
        confidence: 0.95,
        youtube_timestamp_url: "https://youtube.com/watch?v=abc123&t=120",
      },
    ]);
  }),

  // Media
  http.get(`${API_BASE}/media`, () => {
    return HttpResponse.json({
      items: [
        {
          id: 1,
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
          id: 2,
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
      ],
      total: 2,
      page: 1,
      per_page: 20,
    });
  }),

  http.get(`${API_BASE}/media/search`, ({ request }) => {
    const url = new URL(request.url);
    const query = url.searchParams.get("q") || "";

    return HttpResponse.json({
      items: [
        {
          id: 1,
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

  http.get(`${API_BASE}/media/top`, () => {
    return HttpResponse.json([
      {
        id: 1,
        type: "book",
        title: "Top Book",
        author: "Popular Author",
        cover_url: null,
        year: 2020,
        description: "The most mentioned book",
        mention_count: 100,
        episode_count: 50,
        created_at: "2024-01-01T00:00:00Z",
      },
    ]);
  }),

  http.get(`${API_BASE}/media/:id`, ({ params }) => {
    return HttpResponse.json({
      id: Number(params.id),
      type: "book",
      title: "Test Book",
      author: "Test Author",
      cover_url: null,
      year: 2020,
      description: "A test book",
      mention_count: 10,
      episode_count: 5,
      created_at: "2024-01-01T00:00:00Z",
      mentions: [],
      google_books_id: "abc123",
    });
  }),

  // Stats
  http.get(`${API_BASE}/stats/overview`, () => {
    return HttpResponse.json({
      total_podcasts: 5,
      total_episodes: 100,
      total_media: 500,
      total_mentions: 1500,
      total_books: 200,
      total_movies: 150,
    });
  }),

  http.get(`${API_BASE}/stats/by-type`, () => {
    return HttpResponse.json([
      { type: "book", count: 200, mention_count: 600 },
      { type: "movie", count: 150, mention_count: 400 },
      { type: "documentary", count: 50, mention_count: 100 },
    ]);
  }),

  http.get(`${API_BASE}/stats/top-mentioned`, () => {
    return HttpResponse.json([
      {
        id: 1,
        title: "Top Book",
        type: "book",
        author: "Popular Author",
        mention_count: 100,
      },
      {
        id: 2,
        title: "Top Movie",
        type: "movie",
        author: "Famous Director",
        mention_count: 80,
      },
    ]);
  }),
];
