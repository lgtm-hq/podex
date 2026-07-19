import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import OpsDashboard from "./OpsDashboard";
import OpsPipelineRuns from "./OpsPipelineRuns";
import OpsPodcastManager from "./OpsPodcastManager";
import OpsRetentionManager from "./OpsRetentionManager";
import OpsShell from "./OpsShell";

const METRICS = {
  measured_at: "2026-07-19T12:00:00Z",
  review: {
    pending_items: 3,
    decisions_last_24h: 5,
    median_decision_minutes_last_24h: 42.5,
  },
  alerts: {
    generated_events_last_24h: 2,
    delivered_digests_last_24h: 1,
    delivered_events_last_24h: 1,
    pending_events: 4,
  },
};

const PODCAST = {
  id: 1,
  name: "The Example Show",
  slug: "example-show",
  status: "active",
  description: null,
  cover_url: null,
  created_at: "2026-07-19T00:00:00Z",
  discovery_source: null,
  episode_count: 2,
  mention_count: 5,
  sources: {
    rss_url: "https://example.com/feed.xml",
    spotify_id: null,
    apple_id: null,
    youtube_channel_id: null,
    podscripts_slug: null,
  },
};

const RETENTION_ITEM = {
  id: 7,
  episode_id: 1,
  episode_title: "Pilot",
  podcast_name: "The Example Show",
  provider: "podscripts",
  fetched_at: null,
  tier: "hot",
  policy_version: null,
  retention_exempt_sample: false,
  source_retention_opt_out: false,
  purge_eligible_at: null,
  purged_at: null,
  has_raw_payload: true,
  has_stored_artifact: false,
  digest_id: null,
};

const PREVIEW = {
  transcript: RETENTION_ITEM,
  decision: {
    tier: "warm",
    purge_eligible: false,
    purge_blockers: ["derivatives_missing"],
    retention_suppressed: false,
    age_days: 12,
  },
  extraction_confidence: null,
  derivative_coverage_ready: false,
  missing_query_classes: ["semantic_chunks"],
};

function mockJsonResponses(
  handler: (url: string, init?: RequestInit) => unknown,
) {
  const fetchMock = vi.fn(
    (url: string, init?: RequestInit) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(handler(url, init)),
      }) as unknown as Promise<Response>,
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ops console components", () => {
  beforeEach(() => {
    sessionStorage.setItem("podex-ops-key", "test-key");
  });

  afterEach(() => {
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("OpsShell prompts for a key and stores it", async () => {
    sessionStorage.clear();
    render(
      <OpsShell active="/ops">
        <p>console body</p>
      </OpsShell>,
    );

    const input = await screen.findByLabelText("Ops key");
    fireEvent.change(input, { target: { value: "fresh-key" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);

    expect(sessionStorage.getItem("podex-ops-key")).toBe("fresh-key");
    expect(await screen.findByText("console body")).toBeDefined();
  });

  it("OpsShell forgets the key via change key", async () => {
    render(
      <OpsShell active="/ops">
        <p>console body</p>
      </OpsShell>,
    );

    fireEvent.click(await screen.findByText("Change key"));

    expect(sessionStorage.getItem("podex-ops-key")).toBeNull();
    expect(await screen.findByLabelText("Ops key")).toBeDefined();
  });

  it("OpsDashboard renders metrics", async () => {
    mockJsonResponses(() => METRICS);

    render(<OpsDashboard />);

    expect(await screen.findByText("Pending items")).toBeDefined();
    expect(screen.getByText("42.5")).toBeDefined();
  });

  it("OpsDashboard explains a rejected key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 401, json: () => ({}) }),
    );

    render(<OpsDashboard />);

    expect(
      await screen.findByText(/ops key was rejected/i),
    ).toBeDefined();
  });

  it("OpsPodcastManager lists, creates, renames, and archives", async () => {
    const fetchMock = mockJsonResponses((url, init) => {
      if (url.includes("/ops/podcasts") && init?.method === "POST") {
        return PODCAST;
      }
      if (init?.method === "PATCH" || url.endsWith("/archive")) return PODCAST;
      return { items: [PODCAST], total: 1, page: 1, per_page: 25 };
    });

    render(<OpsPodcastManager />);
    expect(await screen.findByText("The Example Show")).toBeDefined();

    fireEvent.change(screen.getByLabelText("Podcast name"), {
      target: { value: "New Show" },
    });
    fireEvent.change(screen.getByLabelText("Podcast slug"), {
      target: { value: "new-show" },
    });
    fireEvent.click(screen.getByText("Create"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "POST"),
      ).toBe(true);
    });

    fireEvent.click(screen.getByText("Rename"));
    fireEvent.change(await screen.findByLabelText("New name"), {
      target: { value: "Renamed Show" },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH"),
      ).toBe(true);
    });

    fireEvent.click(screen.getByText("Archive"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith("/archive"),
        ),
      ).toBe(true);
    });
  });

  it("OpsPodcastManager surfaces slug conflicts", async () => {
    let first = true;
    const responses = vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Promise.resolve({ ok: false, status: 409, json: () => ({}) });
      }
      void url;
      void first;
      first = false;
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({ items: [], total: 0, page: 1, per_page: 25 }),
      });
    });
    vi.stubGlobal("fetch", responses);

    render(<OpsPodcastManager />);
    expect(await screen.findByText("No podcasts yet.")).toBeDefined();

    fireEvent.change(screen.getByLabelText("Podcast name"), {
      target: { value: "Show" },
    });
    fireEvent.change(screen.getByLabelText("Podcast slug"), {
      target: { value: "taken" },
    });
    fireEvent.click(screen.getByText("Create"));

    expect(await screen.findByText("That slug already exists.")).toBeDefined();
  });

  it("OpsPipelineRuns renders run rows and empty state", async () => {
    mockJsonResponses(() => ({
      runs: [
        {
          id: 12,
          status: "completed",
          error_summary: null,
          started_at: "2026-07-19T11:00:00Z",
          completed_at: "2026-07-19T11:05:00Z",
          created_at: "2026-07-19T11:00:00Z",
          duration_seconds: 300,
        },
      ],
    }));

    render(<OpsPipelineRuns />);

    expect(await screen.findByText("#12")).toBeDefined();
    expect(screen.getByText("300s")).toBeDefined();
  });

  it("OpsPipelineRuns shows the empty state", async () => {
    mockJsonResponses(() => ({ runs: [] }));

    render(<OpsPipelineRuns />);

    expect(
      await screen.findByText("No ingestion runs recorded yet."),
    ).toBeDefined();
  });

  it("OpsPipelineRuns renders null timestamps and errors", async () => {
    mockJsonResponses(() => ({
      runs: [
        {
          id: 13,
          status: "running",
          error_summary: "boom",
          started_at: null,
          completed_at: null,
          created_at: "2026-07-19T11:00:00Z",
          duration_seconds: null,
        },
      ],
    }));

    render(<OpsPipelineRuns />);

    expect(await screen.findByText("#13")).toBeDefined();
    expect(screen.getByText("boom")).toBeDefined();
  });

  it("OpsPipelineRuns explains a rejected key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 401, json: () => ({}) }),
    );

    render(<OpsPipelineRuns />);

    expect(await screen.findByText(/ops key was rejected/i)).toBeDefined();
  });

  it("OpsPipelineRuns reports generic failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("net")));

    render(<OpsPipelineRuns />);

    expect(
      await screen.findByText("Unable to load pipeline activity."),
    ).toBeDefined();
  });

  it("OpsRetentionManager shows empty state and errors", async () => {
    mockJsonResponses(() => []);
    const { unmount } = render(<OpsRetentionManager />);
    expect(
      await screen.findByText("No transcripts recorded yet."),
    ).toBeDefined();
    unmount();

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 401, json: () => ({}) }),
    );
    render(<OpsRetentionManager />);
    expect(await screen.findByText(/ops key was rejected/i)).toBeDefined();
  });

  it("OpsPodcastManager explains a rejected key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 401, json: () => ({}) }),
    );

    render(<OpsPodcastManager />);

    expect(await screen.findByText(/ops key was rejected/i)).toBeDefined();
  });

  it("OpsDashboard shows a generic load failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("net")));

    render(<OpsDashboard />);

    expect(await screen.findByText("Unable to load metrics.")).toBeDefined();
  });

  it("OpsDashboard renders missing median as a dash", async () => {
    mockJsonResponses(() => ({
      ...METRICS,
      review: { ...METRICS.review, median_decision_minutes_last_24h: null },
    }));

    render(<OpsDashboard />);

    expect(await screen.findByText("—")).toBeDefined();
  });

  it("OpsRetentionManager previews and applies policy", async () => {
    const fetchMock = mockJsonResponses((url) => {
      if (url.includes("/preview")) return PREVIEW;
      if (url.includes("/apply")) return PREVIEW;
      return [RETENTION_ITEM];
    });

    render(<OpsRetentionManager />);
    expect(await screen.findByText(/Pilot/)).toBeDefined();

    fireEvent.click(screen.getByText("Preview"));
    expect(await screen.findByText("Transcript #7")).toBeDefined();
    expect(screen.getByText("derivatives_missing")).toBeDefined();

    fireEvent.click(screen.getByText("Apply policy"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("/apply")),
      ).toBe(true);
    });
  });
});
