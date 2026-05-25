import { useEffect, useState, type SyntheticEvent } from "react";
import { AsyncState } from "./AsyncState";
import {
  applyOpsSearchTuning,
  getOpsSearchAnalytics,
  getOpsSearchProjection,
  previewOpsSearchTuning,
  queueOpsSearchReindex,
} from "../lib/api";
import type {
  MediaType,
  OpsSearchAnalytics,
  OpsSearchProjection,
  OpsSearchReindexInput,
  OpsSearchTuningInput,
  OpsSearchTuningPreview,
} from "../lib/types";

const MEDIA_TYPES: Array<{ label: string; value: MediaType }> = [
  { label: "Book", value: "book" },
  { label: "Movie", value: "movie" },
  { label: "Documentary", value: "documentary" },
  { label: "TV show", value: "tv_show" },
  { label: "Study", value: "study" },
  { label: "Podcast", value: "podcast" },
  { label: "Article", value: "article" },
  { label: "Standup special", value: "standup_special" },
  { label: "Person", value: "person" },
  { label: "Place", value: "place" },
];

const DEFAULT_RANKING = ["words", "typo", "proximity", "attribute", "sort", "exactness"];

function parseSynonyms(text: string): Record<string, string[]> {
  return Object.fromEntries(
    text
      .split("\n")
      .map((line) => line.split(":"))
      .filter(([term, synonyms]) => Boolean(term?.trim() && synonyms?.trim()))
      .map(([term, synonyms]) => [
        term.trim(),
        synonyms
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
      ]),
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export default function SearchOperations() {
  const [projection, setProjection] = useState<OpsSearchProjection | null>(null);
  const [analytics, setAnalytics] = useState<OpsSearchAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reindex, setReindex] = useState({
    resource_type: "all" as OpsSearchReindexInput["resource_type"],
    podcast_id: "",
    media_type: "",
    created_after: "",
    actor_name: "",
    note: "",
  });
  const [tuning, setTuning] = useState({
    index: "media" as OpsSearchTuningInput["index"],
    query: "",
    synonyms: "",
    ranking_rules: DEFAULT_RANKING.join("\n"),
    actor_name: "",
    note: "",
  });
  const [preview, setPreview] = useState<OpsSearchTuningPreview | null>(null);

  async function loadProjection() {
    try {
      const [nextProjection, nextAnalytics] = await Promise.all([
        getOpsSearchProjection(),
        getOpsSearchAnalytics(),
      ]);
      setProjection(nextProjection);
      setAnalytics(nextAnalytics);
      setError(null);
    } catch {
      setError("Unable to load search projection status.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadProjection();
  }, []);

  function tuningPayload(): OpsSearchTuningInput {
    return {
      index: tuning.index,
      query: tuning.query.trim(),
      synonyms: parseSynonyms(tuning.synonyms),
      ranking_rules: tuning.ranking_rules
        .split("\n")
        .map((rule) => rule.trim())
        .filter(Boolean),
      actor_name: tuning.actor_name || null,
      note: tuning.note || null,
    };
  }

  async function submitReindex(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const result = await queueOpsSearchReindex({
        resource_type: reindex.resource_type,
        podcast_id: reindex.podcast_id || null,
        media_type: (reindex.media_type || null) as MediaType | null,
        created_after: reindex.created_after ? `${reindex.created_after}T00:00:00Z` : null,
        actor_name: reindex.actor_name || null,
        note: reindex.note || null,
      });
      setNotice(`${result.total_queued} projection repairs queued.`);
      await loadProjection();
    } catch {
      setError("Unable to queue projection repairs.");
    }
  }

  async function generatePreview(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setPreview(await previewOpsSearchTuning(tuningPayload()));
      setNotice(null);
    } catch {
      setError("Unable to preview search tuning.");
    }
  }

  async function applyTuning() {
    try {
      const result = await applyOpsSearchTuning(tuningPayload());
      setNotice(`Tuning update for ${result.index} ${result.status}.`);
    } catch {
      setError("Unable to apply search tuning.");
    }
  }

  if (loading) {
    return <AsyncState title="Loading search projection..." variant="loading" />;
  }

  return (
    <div className="space-y-6">
      {error && <AsyncState title={error} variant="error" />}
      {notice && (
        <div
          role="status"
          className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4 text-emerald-100"
        >
          {notice}
        </div>
      )}

      {projection && (
        <>
          <section className="grid gap-4 md:grid-cols-3">
            {projection.indexes.map((index) => (
              <div
                key={index.name}
                className="rounded-lg border border-border-subtle bg-surface p-5"
              >
                <div className="flex items-center justify-between">
                  <h2 className="capitalize">{index.name}</h2>
                  <span
                    className={`rounded-full border px-2 py-1 text-xs ${index.is_indexing ? "border-amber-500/30 text-amber-200" : "border-emerald-500/30 text-emerald-200"}`}
                  >
                    {index.is_indexing ? "Indexing" : "Ready"}
                  </span>
                </div>
                <div className="mt-4 font-mono text-3xl">
                  {index.document_count.toLocaleString()}
                </div>
                <p className="mt-1 text-sm text-text-muted">Documents projected</p>
              </div>
            ))}
          </section>

          <section className="grid gap-6 xl:grid-cols-[0.92fr_1.08fr]">
            <div className="rounded-lg border border-border-subtle bg-surface p-5">
              <h2 className="text-xl">Scoped reindex</h2>
              <p className="mt-1 text-sm text-text-muted">
                Queue projection repairs for a precise catalog slice.
              </p>
              <form className="mt-5 space-y-4" onSubmit={submitReindex}>
                <label className="block text-sm text-text-secondary">
                  Resource
                  <select
                    aria-label="Reindex resource"
                    className="input mt-2"
                    value={reindex.resource_type}
                    onChange={(event) =>
                      setReindex({
                        ...reindex,
                        resource_type: event.target.value as OpsSearchReindexInput["resource_type"],
                      })
                    }
                  >
                    <option value="all">Media and episodes</option>
                    <option value="media">Media only</option>
                    <option value="episode">Episodes only</option>
                  </select>
                </label>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="block text-sm text-text-secondary">
                    Podcast ID
                    <input
                      aria-label="Reindex podcast ID"
                      className="input mt-2"
                      placeholder="pod_12"
                      value={reindex.podcast_id}
                      onChange={(event) =>
                        setReindex({ ...reindex, podcast_id: event.target.value })
                      }
                    />
                  </label>
                  <label className="block text-sm text-text-secondary">
                    Media type
                    <select
                      aria-label="Reindex media type"
                      className="input mt-2"
                      value={reindex.media_type}
                      onChange={(event) =>
                        setReindex({ ...reindex, media_type: event.target.value })
                      }
                    >
                      <option value="">All types</option>
                      {MEDIA_TYPES.map((type) => (
                        <option key={type.value} value={type.value}>
                          {type.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="block text-sm text-text-secondary">
                  Created after
                  <input
                    aria-label="Reindex created after"
                    type="date"
                    className="input mt-2"
                    value={reindex.created_after}
                    onChange={(event) =>
                      setReindex({ ...reindex, created_after: event.target.value })
                    }
                  />
                </label>
                <input
                  aria-label="Reindex operator name"
                  className="input"
                  placeholder="Operator name"
                  value={reindex.actor_name}
                  onChange={(event) => setReindex({ ...reindex, actor_name: event.target.value })}
                />
                <textarea
                  aria-label="Reindex note"
                  className="input min-h-20"
                  placeholder="Reason for reindex"
                  value={reindex.note}
                  onChange={(event) => setReindex({ ...reindex, note: event.target.value })}
                />
                <button className="btn btn-primary" type="submit">
                  Queue reindex
                </button>
              </form>
            </div>

            <div className="rounded-lg border border-border-subtle bg-surface p-5">
              <h2 className="text-xl">Synonyms and ranking</h2>
              <p className="mt-1 text-sm text-text-muted">
                Review baseline hits before applying index settings.
              </p>
              <form className="mt-5 space-y-4" onSubmit={generatePreview}>
                <div className="grid gap-4 sm:grid-cols-2">
                  <select
                    aria-label="Tuning index"
                    className="input"
                    value={tuning.index}
                    onChange={(event) => {
                      setTuning({
                        ...tuning,
                        index: event.target.value as OpsSearchTuningInput["index"],
                      });
                      setPreview(null);
                    }}
                  >
                    <option value="media">Media</option>
                    <option value="episodes">Episodes</option>
                    <option value="podcasts">Podcasts</option>
                  </select>
                  <input
                    required
                    aria-label="Tuning sample query"
                    className="input"
                    placeholder="Sample query"
                    value={tuning.query}
                    onChange={(event) => {
                      setTuning({ ...tuning, query: event.target.value });
                      setPreview(null);
                    }}
                  />
                </div>
                <textarea
                  aria-label="Tuning synonyms"
                  className="input min-h-20 font-mono text-sm"
                  placeholder={"sci fi: science fiction, sf"}
                  value={tuning.synonyms}
                  onChange={(event) => {
                    setTuning({ ...tuning, synonyms: event.target.value });
                    setPreview(null);
                  }}
                />
                <textarea
                  aria-label="Tuning ranking rules"
                  className="input min-h-32 font-mono text-sm"
                  value={tuning.ranking_rules}
                  onChange={(event) => {
                    setTuning({ ...tuning, ranking_rules: event.target.value });
                    setPreview(null);
                  }}
                />
                <div className="grid gap-4 sm:grid-cols-2">
                  <input
                    aria-label="Tuning operator name"
                    className="input"
                    placeholder="Operator name"
                    value={tuning.actor_name}
                    onChange={(event) => setTuning({ ...tuning, actor_name: event.target.value })}
                  />
                  <input
                    aria-label="Tuning note"
                    className="input"
                    placeholder="Change note"
                    value={tuning.note}
                    onChange={(event) => setTuning({ ...tuning, note: event.target.value })}
                  />
                </div>
                <button className="btn btn-secondary" type="submit">
                  Preview tuning
                </button>
              </form>
              {preview && (
                <div className="mt-5 rounded-lg border border-border-subtle bg-background p-4">
                  <h3>Baseline sample hits</h3>
                  <div className="mt-3 space-y-2 text-sm text-text-secondary">
                    {preview.sample_hits.length ? (
                      preview.sample_hits.map((hit, index) => (
                        <div key={index} className="rounded border border-border-subtle p-2">
                          {String(hit.title ?? hit.name ?? "Untitled result")}
                        </div>
                      ))
                    ) : (
                      <p>No sample hits.</p>
                    )}
                  </div>
                  <button
                    className="btn btn-primary mt-4"
                    type="button"
                    onClick={() => void applyTuning()}
                  >
                    Apply tuning
                  </button>
                </div>
              )}
            </div>
          </section>

          {analytics && (
            <section className="rounded-lg border border-border-subtle bg-surface p-5">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <h2 className="text-xl">Relevance signals</h2>
                  <p className="mt-1 text-sm text-text-muted">
                    Anonymous public queries and result selections for tuning review.
                  </p>
                </div>
                <div className="flex gap-4 font-mono text-sm">
                  <span>{analytics.searches} searches</span>
                  <span>{analytics.zero_result_searches} empty</span>
                  <span>{analytics.selections} selections</span>
                </div>
              </div>
              <div className="mt-5 divide-y divide-border-subtle border-t border-border-subtle">
                {analytics.queries.length ? (
                  analytics.queries.map((metric) => (
                    <div
                      key={metric.query}
                      className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm"
                    >
                      <span className="font-mono">{metric.query}</span>
                      <span className="text-text-muted">
                        {metric.searches} searches | {metric.zero_result_searches} empty |{" "}
                        {metric.selections} selections
                      </span>
                    </div>
                  ))
                ) : (
                  <p className="py-4 text-sm text-text-muted">
                    No public search signals recorded yet.
                  </p>
                )}
              </div>
            </section>
          )}

          <section className="rounded-lg border border-border-subtle bg-surface p-5">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 className="text-xl">Projection repairs</h2>
                <p className="mt-1 text-sm text-text-muted">
                  Pending and failed work reveals projection lag.
                </p>
              </div>
              <div className="flex gap-4 font-mono text-sm">
                <span>{projection.repair_summary.pending} pending</span>
                <span className="text-red-200">{projection.repair_summary.failed} failed</span>
              </div>
            </div>
            <div className="mt-5 divide-y divide-border-subtle border-t border-border-subtle">
              {projection.repairs.length ? (
                projection.repairs.map((repair) => (
                  <div
                    key={repair.id}
                    className="flex flex-wrap items-start justify-between gap-3 py-3"
                  >
                    <div>
                      <div className="font-mono text-sm">{repair.resource_id}</div>
                      <div className="text-sm text-text-muted">
                        {repair.reason.replaceAll("_", " ")} · {formatDate(repair.updated_at)}
                      </div>
                    </div>
                    <span className="rounded-full border border-border px-2 py-1 text-xs capitalize">
                      {repair.status}
                    </span>
                  </div>
                ))
              ) : (
                <p className="py-4 text-sm text-text-muted">No recent projection repairs.</p>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
