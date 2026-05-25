import { type SyntheticEvent, useCallback, useEffect, useMemo, useState } from "react";
import { archiveOpsPodcast, createOpsPodcast, getOpsPodcasts, updateOpsPodcast } from "../lib/api";
import type {
  OpsPodcast,
  OpsPodcastCreateInput,
  OpsPodcastSourceFilter,
  OpsPodcastSort,
  OpsPodcastStatus,
} from "../lib/types";

interface FormState {
  name: string;
  slug: string;
  status: OpsPodcastStatus;
  description: string;
  cover_url: string;
  discovery_source: string;
  rss_url: string;
  spotify_id: string;
  apple_id: string;
  youtube_channel_id: string;
  podscripts_slug: string;
}

const emptyForm: FormState = {
  name: "",
  slug: "",
  status: "watchlist",
  description: "",
  cover_url: "",
  discovery_source: "",
  rss_url: "",
  spotify_id: "",
  apple_id: "",
  youtube_channel_id: "",
  podscripts_slug: "",
};

const statusOptions: Array<{ value: OpsPodcastStatus | ""; label: string }> = [
  { value: "", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "watchlist", label: "Watchlist" },
  { value: "paused", label: "Paused" },
];

const sourceOptions: Array<{ value: OpsPodcastSourceFilter | ""; label: string }> = [
  { value: "", label: "All sources" },
  { value: "rss", label: "RSS" },
  { value: "spotify", label: "Spotify" },
  { value: "podscripts", label: "Podscripts" },
  { value: "youtube", label: "YouTube" },
];

function statusClass(status: OpsPodcastStatus): string {
  if (status === "active") {
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
  }
  if (status === "paused") {
    return "border-red-500/30 bg-red-500/10 text-red-200";
  }
  return "border-amber-500/30 bg-amber-500/10 text-amber-200";
}

function formFromPodcast(podcast: OpsPodcast): FormState {
  return {
    name: podcast.name,
    slug: podcast.slug,
    status: podcast.status,
    description: podcast.description ?? "",
    cover_url: podcast.cover_url ?? "",
    discovery_source: podcast.discovery_source ?? "",
    rss_url: podcast.sources.rss_url ?? "",
    spotify_id: podcast.sources.spotify_id ?? "",
    apple_id: podcast.sources.apple_id ?? "",
    youtube_channel_id: podcast.sources.youtube_channel_id ?? "",
    podscripts_slug: podcast.sources.podscripts_slug ?? "",
  };
}

function toPayload(form: FormState): OpsPodcastCreateInput {
  const nullable = (value: string): string | null => value.trim() || null;
  return {
    name: form.name.trim(),
    slug: form.slug.trim(),
    status: form.status,
    description: nullable(form.description),
    cover_url: nullable(form.cover_url),
    discovery_source: nullable(form.discovery_source),
    sources: {
      rss_url: nullable(form.rss_url),
      spotify_id: nullable(form.spotify_id),
      apple_id: nullable(form.apple_id),
      youtube_channel_id: nullable(form.youtube_channel_id),
      podscripts_slug: nullable(form.podscripts_slug),
    },
  };
}

function sourceTags(podcast: OpsPodcast): string[] {
  const tags: string[] = [];
  if (podcast.sources.rss_url) tags.push("RSS");
  if (podcast.sources.spotify_id) tags.push("Spotify");
  if (podcast.sources.podscripts_slug) tags.push("Podscripts");
  if (podcast.sources.youtube_channel_id) tags.push("YouTube");
  if (podcast.sources.apple_id) tags.push("Apple");
  return tags;
}

export default function PodcastManager() {
  const [podcasts, setPodcasts] = useState<OpsPodcast[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<OpsPodcastStatus | "">("");
  const [source, setSource] = useState<OpsPodcastSourceFilter | "">("");
  const [sort, setSort] = useState<OpsPodcastSort>("created_at");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<OpsPodcast | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [formOpen, setFormOpen] = useState(false);

  const loadPodcasts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getOpsPodcasts(
        status || undefined,
        source || undefined,
        sort,
        sort === "name" ? "asc" : "desc",
      );
      setPodcasts(result.items);
      setTotal(result.total);
    } catch {
      setError("Unable to load podcast inventory.");
    } finally {
      setLoading(false);
    }
  }, [source, sort, status]);

  useEffect(() => {
    void loadPodcasts();
  }, [loadPodcasts]);

  const filteredPodcasts = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) {
      return podcasts;
    }

    return podcasts.filter(
      (podcast) =>
        podcast.name.toLowerCase().includes(normalizedQuery) ||
        podcast.slug.toLowerCase().includes(normalizedQuery),
    );
  }, [podcasts, query]);

  const openNewPodcast = () => {
    setEditing(null);
    setForm(emptyForm);
    setFormOpen(true);
    setError(null);
    setNotice(null);
  };

  const openEditPodcast = (podcast: OpsPodcast) => {
    setEditing(podcast);
    setForm(formFromPodcast(podcast));
    setFormOpen(true);
    setError(null);
    setNotice(null);
  };

  const closeForm = () => {
    setFormOpen(false);
    setEditing(null);
    setForm(emptyForm);
  };

  const submitPodcast = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setNotice(null);
    const payload = toPayload(form);
    if (!payload.name || !payload.slug) {
      setError("Name and slug are required.");
      return;
    }

    setPendingId(editing?.id ?? "create");
    try {
      const saved = editing
        ? await updateOpsPodcast(editing.id, payload)
        : await createOpsPodcast(payload);
      setNotice(editing ? `${saved.name} updated.` : `${saved.name} added to the catalog.`);
      closeForm();
      await loadPodcasts();
    } catch {
      setError("Unable to save podcast. Check the slug and source details.");
    } finally {
      setPendingId(null);
    }
  };

  const togglePaused = async (podcast: OpsPodcast) => {
    setPendingId(podcast.id);
    setError(null);
    setNotice(null);
    try {
      const nextStatus: OpsPodcastStatus = podcast.status === "paused" ? "active" : "paused";
      const updated = await updateOpsPodcast(podcast.id, { status: nextStatus });
      setNotice(`${updated.name} is now ${updated.status}.`);
      await loadPodcasts();
    } catch {
      setError("Unable to update podcast status.");
    } finally {
      setPendingId(null);
    }
  };

  const archivePodcast = async (podcast: OpsPodcast) => {
    if (!window.confirm(`Archive ${podcast.name}? Discovery will be paused.`)) {
      return;
    }

    setPendingId(podcast.id);
    setError(null);
    setNotice(null);
    try {
      const archived = await archiveOpsPodcast(podcast.id);
      setNotice(`${archived.name} archived and paused.`);
      await loadPodcasts();
    } catch {
      setError("Unable to archive podcast.");
    } finally {
      setPendingId(null);
    }
  };

  const field = (key: keyof FormState, label: string, placeholder: string, required = false) => (
    <label className="space-y-1 text-sm text-text-secondary">
      <span>{label}</span>
      <input
        className="input w-full"
        value={form[key]}
        placeholder={placeholder}
        required={required}
        onChange={(event) => setForm((current) => ({ ...current, [key]: event.target.value }))}
      />
    </label>
  );

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border-subtle bg-surface p-4 md:p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="grid flex-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="space-y-1 text-sm text-text-secondary">
              <span>Find podcast</span>
              <input
                type="search"
                className="input w-full"
                placeholder="Name or slug"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label className="space-y-1 text-sm text-text-secondary">
              <span>Status</span>
              <select
                className="input w-full"
                value={status}
                onChange={(event) => setStatus(event.target.value as OpsPodcastStatus | "")}
              >
                {statusOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-sm text-text-secondary">
              <span>Source</span>
              <select
                className="input w-full"
                value={source}
                onChange={(event) => setSource(event.target.value as OpsPodcastSourceFilter | "")}
              >
                {sourceOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-sm text-text-secondary">
              <span>Sort</span>
              <select
                className="input w-full"
                value={sort}
                onChange={(event) => setSort(event.target.value as OpsPodcastSort)}
              >
                <option value="created_at">Recently added</option>
                <option value="name">Name</option>
                <option value="episode_count">Episode count</option>
                <option value="mention_count">Mention count</option>
              </select>
            </label>
          </div>
          <button type="button" className="btn btn-primary shrink-0" onClick={openNewPodcast}>
            Add podcast
          </button>
        </div>
      </section>

      {(error || notice) && (
        <div
          role="status"
          className={`rounded-lg border p-4 text-sm ${
            error
              ? "border-red-500/30 bg-red-500/10 text-red-100"
              : "border-emerald-500/30 bg-emerald-500/10 text-emerald-100"
          }`}
        >
          {error ?? notice}
        </div>
      )}

      <section className="overflow-hidden rounded-lg border border-border-subtle bg-surface">
        <div className="flex items-center justify-between border-b border-border-subtle px-5 py-4">
          <h2 className="text-lg">Catalog inventory</h2>
          <span className="font-mono text-xs uppercase tracking-wider text-text-muted">
            {filteredPodcasts.length} shown / {total} tracked
          </span>
        </div>
        {loading ? (
          <div className="p-8 text-sm text-text-muted">Loading podcast inventory...</div>
        ) : filteredPodcasts.length === 0 ? (
          <div className="p-8 text-sm text-text-muted">No podcasts match these filters.</div>
        ) : (
          <div className="divide-y divide-border-subtle">
            {filteredPodcasts.map((podcast) => {
              const tags = sourceTags(podcast);
              const isPending = pendingId === podcast.id;
              return (
                <article
                  key={podcast.id}
                  className="grid gap-4 p-5 lg:grid-cols-[minmax(220px,1.4fr)_minmax(180px,1fr)_160px_230px] lg:items-center"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="truncate text-base font-medium text-text">{podcast.name}</h3>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs capitalize ${statusClass(podcast.status)}`}
                      >
                        {podcast.status}
                      </span>
                    </div>
                    <div className="mt-1 font-mono text-xs text-text-muted">/{podcast.slug}</div>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {tags.length ? (
                      tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded border border-border bg-background px-2 py-1 text-xs text-text-secondary"
                        >
                          {tag}
                        </span>
                      ))
                    ) : (
                      <span className="text-sm text-text-muted">No sources configured</span>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <div className="font-mono text-text">{podcast.episode_count}</div>
                      <div className="text-xs text-text-muted">Episodes</div>
                    </div>
                    <div>
                      <div className="font-mono text-text">{podcast.mention_count}</div>
                      <div className="text-xs text-text-muted">Mentions</div>
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-start gap-2 lg:justify-end">
                    <button
                      type="button"
                      className="btn btn-secondary px-3 py-2"
                      disabled={isPending}
                      onClick={() => openEditPodcast(podcast)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary px-3 py-2"
                      disabled={isPending}
                      onClick={() => void togglePaused(podcast)}
                    >
                      {podcast.status === "paused" ? "Resume" : "Pause"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost px-3 py-2 text-red-200"
                      disabled={isPending}
                      onClick={() => void archivePodcast(podcast)}
                    >
                      Archive
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      {formOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm">
          <section
            role="dialog"
            aria-modal="true"
            aria-label={editing ? "Edit podcast" : "Add podcast"}
            className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-lg border border-border bg-surface shadow-2xl"
          >
            <header className="flex items-start justify-between border-b border-border-subtle p-5">
              <div>
                <h2 className="text-xl">{editing ? "Edit podcast" : "Add podcast"}</h2>
                <p className="mt-1 text-sm text-text-muted">
                  Configure discovery status and provider identifiers.
                </p>
              </div>
              <button type="button" className="btn btn-ghost px-3" onClick={closeForm}>
                Close
              </button>
            </header>
            <form className="space-y-6 p-5" onSubmit={(event) => void submitPodcast(event)}>
              <div className="grid gap-4 sm:grid-cols-2">
                {field("name", "Podcast name", "The Podcast Name", true)}
                {field("slug", "Public slug", "the-podcast-name", true)}
                <label className="space-y-1 text-sm text-text-secondary">
                  <span>Status</span>
                  <select
                    className="input w-full"
                    value={form.status}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        status: event.target.value as OpsPodcastStatus,
                      }))
                    }
                  >
                    <option value="watchlist">Watchlist</option>
                    <option value="active">Active</option>
                    <option value="paused">Paused</option>
                  </select>
                </label>
                {field("discovery_source", "Discovery source", "editorial")}
                {field("cover_url", "Cover URL", "https://...")}
              </div>
              <label className="block space-y-1 text-sm text-text-secondary">
                <span>Description</span>
                <textarea
                  className="input min-h-20 w-full resize-y"
                  value={form.description}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, description: event.target.value }))
                  }
                />
              </label>
              <div>
                <h3 className="mb-3 text-sm uppercase tracking-wider text-text-muted">
                  Provider identifiers
                </h3>
                <div className="grid gap-4 sm:grid-cols-2">
                  {field("rss_url", "RSS URL", "https://.../feed.xml")}
                  {field("spotify_id", "Spotify ID", "spotify-show-id")}
                  {field("apple_id", "Apple ID", "apple-show-id")}
                  {field("youtube_channel_id", "YouTube Channel ID", "UC...")}
                  {field("podscripts_slug", "Podscripts slug", "podcast-slug")}
                </div>
              </div>
              <footer className="flex justify-end gap-3 border-t border-border-subtle pt-5">
                <button type="button" className="btn btn-secondary" onClick={closeForm}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={pendingId !== null}>
                  {pendingId ? "Saving..." : editing ? "Save changes" : "Add podcast"}
                </button>
              </footer>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}
