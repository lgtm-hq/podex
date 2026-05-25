import { type SyntheticEvent, useEffect, useState } from "react";
import {
  addOpsMediaAlias,
  getMedia,
  getOpsMediaDetail,
  mergeOpsMedia,
  previewOpsMediaMerge,
  searchMedia,
  splitOpsMedia,
  updateOpsMedia,
  upsertOpsMediaExternalRef,
} from "../lib/api";
import type {
  MediaItem,
  MediaType,
  OpsMediaDetail,
  OpsMediaExternalRefSource,
  OpsMediaMergePreview,
} from "../lib/types";

interface MetadataForm {
  type: MediaType;
  title: string;
  author: string;
  description: string;
  google_books_id: string;
  open_library_id: string;
  imdb_id: string;
  wikipedia_id: string;
}

interface ReferenceForm {
  source: OpsMediaExternalRefSource;
  external_id: string;
  url: string;
  label: string;
}

interface SplitForm {
  type: MediaType;
  title: string;
  author: string;
  description: string;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function externalIdentifiers(detail: OpsMediaDetail): Array<[string, string | number | undefined]> {
  return [
    ["Google Books", detail.google_books_id],
    ["Open Library", detail.open_library_id],
    ["IMDb", detail.imdb_id],
    ["TMDB", detail.tmdb_id],
    ["Wikipedia", detail.wikipedia_id],
    ["DOI", detail.doi],
    ["PubMed", detail.pubmed_id],
    ["Semantic Scholar", detail.semantic_scholar_id],
  ];
}

function metadataFromDetail(detail: OpsMediaDetail): MetadataForm {
  return {
    type: detail.media.type,
    title: detail.media.title,
    author: detail.media.author ?? "",
    description: detail.media.description ?? "",
    google_books_id: detail.google_books_id ?? "",
    open_library_id: detail.open_library_id ?? "",
    imdb_id: detail.imdb_id ?? "",
    wikipedia_id: detail.wikipedia_id ?? "",
  };
}

const emptyMetadata: MetadataForm = {
  type: "book",
  title: "",
  author: "",
  description: "",
  google_books_id: "",
  open_library_id: "",
  imdb_id: "",
  wikipedia_id: "",
};

const emptyReference: ReferenceForm = {
  source: "manual",
  external_id: "",
  url: "",
  label: "",
};

const emptySplit: SplitForm = {
  type: "book",
  title: "",
  author: "",
  description: "",
};

function RecordButton({
  item,
  active,
  onSelect,
}: {
  item: MediaItem;
  active: boolean;
  onSelect: (item: MediaItem) => void;
}) {
  return (
    <button
      className={`w-full rounded-lg border p-3 text-left transition-colors ${
        active
          ? "border-accent bg-accent/10"
          : "border-border-subtle bg-background hover:border-border"
      }`}
      type="button"
      onClick={() => onSelect(item)}
    >
      <div className="flex justify-between gap-3">
        <span className="truncate font-medium">{item.title}</span>
        <span className="rounded-full border border-border px-2 py-1 text-xs capitalize text-text-muted">
          {item.type.replaceAll("_", " ")}
        </span>
      </div>
      <div className="mt-2 text-sm text-text-muted">
        {item.author ?? "Unknown creator"} / {item.mention_count} mentions
      </div>
      <div className="mt-2 font-mono text-xs text-text-muted">{item.id}</div>
    </button>
  );
}

export default function MediaManager() {
  const [records, setRecords] = useState<MediaItem[]>([]);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<OpsMediaDetail | null>(null);
  const [target, setTarget] = useState<OpsMediaDetail | null>(null);
  const [preview, setPreview] = useState<OpsMediaMergePreview | null>(null);
  const [metadata, setMetadata] = useState<MetadataForm>(emptyMetadata);
  const [alias, setAlias] = useState("");
  const [reference, setReference] = useState<ReferenceForm>(emptyReference);
  const [split, setSplit] = useState<SplitForm>(emptySplit);
  const [splitMentionIds, setSplitMentionIds] = useState<string[]>([]);
  const [actorName, setActorName] = useState("");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadRecords = async (searchQuery = "") => {
    setLoading(true);
    setError(null);
    try {
      const response = searchQuery.trim()
        ? await searchMedia(searchQuery.trim(), 1, 50)
        : await getMedia(1, 50);
      setRecords(response.items);
    } catch {
      setError("Unable to load canonical media records.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadRecords();
  }, []);

  const search = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    void loadRecords(query);
  };

  const selectSource = async (item: MediaItem) => {
    setError(null);
    setNotice(null);
    setPreview(null);
    try {
      const detail = await getOpsMediaDetail(item.id);
      setSource(detail);
      setMetadata(metadataFromDetail(detail));
      setAlias("");
      setReference(emptyReference);
      setSplit({ ...emptySplit, type: detail.media.type });
      setSplitMentionIds([]);
      if (target?.media.id === item.id) {
        setTarget(null);
      }
    } catch {
      setError("Unable to load the source media record.");
    }
  };

  const selectTarget = async (item: MediaItem) => {
    if (item.id === source?.media.id) {
      setError("Source and survivor must be different records.");
      return;
    }
    setError(null);
    setNotice(null);
    setPreview(null);
    try {
      setTarget(await getOpsMediaDetail(item.id));
    } catch {
      setError("Unable to load the surviving media record.");
    }
  };

  const createPreview = async () => {
    if (!source || !target) {
      setError("Select both a duplicate source and surviving target record.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setPreview(await previewOpsMediaMerge(source.media.id, target.media.id));
    } catch {
      setError("Unable to preview this merge. Confirm that the records are compatible.");
    } finally {
      setBusy(false);
    }
  };

  const syncSavedRecord = (saved: OpsMediaDetail) => {
    setSource(saved);
    setMetadata(metadataFromDetail(saved));
    setRecords((current) =>
      current.map((item) =>
        item.id === saved.media.id
          ? {
              ...item,
              ...saved.media,
            }
          : item,
      ),
    );
  };

  const auditContext = {
    actor_name: actorName.trim() || null,
    note: note.trim() || null,
  };

  const saveMetadata = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!source) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const nullable = (value: string): string | null => value.trim() || null;
      const saved = await updateOpsMedia(source.media.id, {
        ...auditContext,
        type: metadata.type,
        title: metadata.title.trim(),
        author: nullable(metadata.author),
        description: nullable(metadata.description),
        google_books_id: nullable(metadata.google_books_id),
        open_library_id: nullable(metadata.open_library_id),
        imdb_id: nullable(metadata.imdb_id),
        wikipedia_id: nullable(metadata.wikipedia_id),
      });
      syncSavedRecord(saved);
      setNotice(`${saved.media.title} metadata updated.`);
    } catch {
      setError("Unable to save canonical media metadata.");
    } finally {
      setBusy(false);
    }
  };

  const addAlias = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!source || !alias.trim()) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await addOpsMediaAlias(source.media.id, {
        ...auditContext,
        alias: alias.trim(),
      });
      syncSavedRecord(saved);
      setAlias("");
      setNotice(`Alias added to ${saved.media.title}.`);
    } catch {
      setError("Unable to add this alias.");
    } finally {
      setBusy(false);
    }
  };

  const saveReference = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!source || !reference.external_id.trim()) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await upsertOpsMediaExternalRef(source.media.id, {
        ...auditContext,
        source: reference.source,
        external_id: reference.external_id.trim(),
        url: reference.url.trim() || null,
        label: reference.label.trim() || null,
      });
      syncSavedRecord(saved);
      setReference(emptyReference);
      setNotice(`Reference added to ${saved.media.title}.`);
    } catch {
      setError("Unable to save this external reference.");
    } finally {
      setBusy(false);
    }
  };

  const recoverSplit = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!source || !split.title.trim() || !splitMentionIds.length) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await splitOpsMedia(source.media.id, {
        ...auditContext,
        mention_ids: splitMentionIds,
        type: split.type,
        title: split.title.trim(),
        author: split.author.trim() || null,
        description: split.description.trim() || null,
      });
      syncSavedRecord(result.source);
      setSplit({ ...emptySplit, type: result.source.media.type });
      setSplitMentionIds([]);
      setNotice(
        `${result.mentions_moved} mention${result.mentions_moved === 1 ? "" : "s"} recovered into ${result.created.media.title}.`,
      );
      await loadRecords(query);
    } catch {
      setError("Unable to split selected mentions into a recovered record.");
    } finally {
      setBusy(false);
    }
  };

  const executeMerge = async () => {
    if (
      !preview ||
      !window.confirm(`Merge ${preview.source.title} into ${preview.target.title}?`)
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await mergeOpsMedia(preview.source.id, preview.target.id);
      setNotice(`${preview.source.title} merged into ${result.target.title}.`);
      setSource(null);
      setTarget(null);
      setPreview(null);
      await loadRecords(query);
    } catch {
      setError("Unable to merge these records.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border-subtle bg-surface p-5">
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
          <div>
            <h2 className="text-xl">Canonical records</h2>
            <p className="mt-1 text-sm text-text-muted">
              Choose a duplicate source and a surviving canonical target before generating a merge
              preview.
            </p>
          </div>
          <form className="flex w-full max-w-md gap-2" onSubmit={search}>
            <input
              className="input min-w-0 flex-1"
              aria-label="Search canonical media"
              placeholder="Find title or creator"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <button className="btn btn-secondary" type="submit">
              Search
            </button>
          </form>
        </div>
      </section>

      {(error || notice) && (
        <div
          role="status"
          className={`rounded-lg border p-4 text-sm ${error ? "border-red-500/30 bg-red-500/10 text-red-100" : "border-emerald-500/30 bg-emerald-500/10 text-emerald-100"}`}
        >
          {error ?? notice}
        </div>
      )}

      <section className="grid gap-6 xl:grid-cols-[350px_1fr]">
        <div className="rounded-lg border border-border-subtle bg-surface p-4">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-lg">Catalog</h3>
            <span className="font-mono text-xs text-text-muted">{records.length} records</span>
          </div>
          {loading ? (
            <p className="py-8 text-sm text-text-muted">Loading media...</p>
          ) : (
            <div className="space-y-3">
              {records.map((item) => (
                <div key={item.id} className="space-y-2">
                  <RecordButton
                    item={item}
                    active={source?.media.id === item.id}
                    onSelect={selectSource}
                  />
                  {source && source.media.id !== item.id && (
                    <button
                      className={`w-full rounded border px-3 py-2 text-xs transition-colors ${
                        target?.media.id === item.id
                          ? "border-accent text-accent"
                          : "border-border-subtle text-text-muted hover:text-text"
                      }`}
                      type="button"
                      onClick={() => void selectTarget(item)}
                    >
                      {target?.media.id === item.id ? "Selected survivor" : "Choose as survivor"}
                    </button>
                  )}
                </div>
              ))}
              {!records.length && (
                <p className="text-sm text-text-muted">No matching media records.</p>
              )}
            </div>
          )}
        </div>

        <div className="space-y-6">
          <section className="grid gap-5 lg:grid-cols-2">
            {[
              ["Duplicate source", source],
              ["Surviving target", target],
            ].map(([heading, media]) => (
              <div
                key={heading as string}
                className="rounded-lg border border-border-subtle bg-surface p-5"
              >
                <p className="text-xs uppercase text-text-muted">{heading as string}</p>
                {media ? (
                  <>
                    <h3 className="mt-3 text-xl">{(media as OpsMediaDetail).media.title}</h3>
                    <p className="mt-2 text-sm text-text-secondary">
                      {(media as OpsMediaDetail).media.author ?? "Unknown creator"} /{" "}
                      {(media as OpsMediaDetail).media.mention_count} mentions
                    </p>
                    <dl className="mt-5 space-y-2 text-sm">
                      {externalIdentifiers(media as OpsMediaDetail)
                        .filter(([, value]) => value !== undefined)
                        .map(([name, value]) => (
                          <div key={name} className="flex justify-between gap-4">
                            <dt className="text-text-muted">{name}</dt>
                            <dd className="font-mono text-text-secondary">{formatValue(value)}</dd>
                          </div>
                        ))}
                    </dl>
                    {(media as OpsMediaDetail).media.description && (
                      <p className="mt-5 line-clamp-4 text-sm leading-6 text-text-secondary">
                        {(media as OpsMediaDetail).media.description}
                      </p>
                    )}
                  </>
                ) : (
                  <p className="mt-4 text-sm text-text-muted">Not selected.</p>
                )}
              </div>
            ))}
          </section>

          {source && (
            <section className="space-y-5 rounded-lg border border-border-subtle bg-surface p-5">
              <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
                <div>
                  <h3 className="text-xl">Record editor</h3>
                  <p className="mt-1 text-sm text-text-muted">
                    Corrections, aliases, and references are written to the audit log.
                  </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <input
                    className="input"
                    aria-label="Media operator name"
                    placeholder="Operator name"
                    value={actorName}
                    onChange={(event) => setActorName(event.target.value)}
                  />
                  <input
                    className="input"
                    aria-label="Media edit note"
                    placeholder="Edit note"
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                  />
                </div>
              </div>

              <form
                className="rounded-lg border border-border-subtle bg-background p-4"
                onSubmit={(event) => void saveMetadata(event)}
              >
                <div className="mb-4 flex items-center justify-between">
                  <h4 className="font-medium">Metadata correction</h4>
                  <button className="btn btn-secondary" type="submit" disabled={busy}>
                    Save metadata
                  </button>
                </div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  <select
                    className="input"
                    aria-label="Canonical media type"
                    value={metadata.type}
                    onChange={(event) =>
                      setMetadata((current) => ({
                        ...current,
                        type: event.target.value as MediaType,
                      }))
                    }
                  >
                    {[
                      "book",
                      "movie",
                      "documentary",
                      "tv_show",
                      "study",
                      "podcast",
                      "article",
                      "standup_special",
                      "person",
                      "place",
                    ].map((type) => (
                      <option key={type} value={type}>
                        {type.replaceAll("_", " ")}
                      </option>
                    ))}
                  </select>
                  <input
                    className="input"
                    aria-label="Canonical title"
                    required
                    value={metadata.title}
                    onChange={(event) =>
                      setMetadata((current) => ({ ...current, title: event.target.value }))
                    }
                  />
                  <input
                    className="input"
                    aria-label="Canonical creator"
                    placeholder="Creator"
                    value={metadata.author}
                    onChange={(event) =>
                      setMetadata((current) => ({ ...current, author: event.target.value }))
                    }
                  />
                  <input
                    className="input"
                    aria-label="Google Books identifier"
                    placeholder="Google Books ID"
                    value={metadata.google_books_id}
                    onChange={(event) =>
                      setMetadata((current) => ({
                        ...current,
                        google_books_id: event.target.value,
                      }))
                    }
                  />
                  <input
                    className="input"
                    aria-label="Open Library identifier"
                    placeholder="Open Library ID"
                    value={metadata.open_library_id}
                    onChange={(event) =>
                      setMetadata((current) => ({
                        ...current,
                        open_library_id: event.target.value,
                      }))
                    }
                  />
                  <input
                    className="input"
                    aria-label="IMDb identifier"
                    placeholder="IMDb ID"
                    value={metadata.imdb_id}
                    onChange={(event) =>
                      setMetadata((current) => ({ ...current, imdb_id: event.target.value }))
                    }
                  />
                  <input
                    className="input"
                    aria-label="Wikipedia identifier"
                    placeholder="Wikipedia ID"
                    value={metadata.wikipedia_id}
                    onChange={(event) =>
                      setMetadata((current) => ({ ...current, wikipedia_id: event.target.value }))
                    }
                  />
                  <textarea
                    className="input min-h-24 md:col-span-2 xl:col-span-1"
                    aria-label="Canonical description"
                    placeholder="Description"
                    value={metadata.description}
                    onChange={(event) =>
                      setMetadata((current) => ({ ...current, description: event.target.value }))
                    }
                  />
                </div>
              </form>

              <div className="grid gap-5 lg:grid-cols-2">
                <form
                  className="rounded-lg border border-border-subtle bg-background p-4"
                  onSubmit={(event) => void addAlias(event)}
                >
                  <div className="flex items-center justify-between gap-3">
                    <h4 className="font-medium">Aliases</h4>
                    <button
                      className="btn btn-secondary"
                      type="submit"
                      disabled={busy || !alias.trim()}
                    >
                      Add alias
                    </button>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {source.aliases.map((item) => (
                      <span
                        key={item.normalized_alias}
                        className="rounded-full border border-border px-3 py-1 text-sm"
                      >
                        {item.alias} <span className="text-text-muted">({item.source})</span>
                      </span>
                    ))}
                    {!source.aliases.length && (
                      <span className="text-sm text-text-muted">No aliases recorded.</span>
                    )}
                  </div>
                  <input
                    className="input mt-4 w-full"
                    aria-label="New media alias"
                    placeholder="Alternate title"
                    value={alias}
                    onChange={(event) => setAlias(event.target.value)}
                  />
                </form>

                <form
                  className="rounded-lg border border-border-subtle bg-background p-4"
                  onSubmit={(event) => void saveReference(event)}
                >
                  <div className="flex items-center justify-between gap-3">
                    <h4 className="font-medium">External references</h4>
                    <button
                      className="btn btn-secondary"
                      type="submit"
                      disabled={busy || !reference.external_id.trim()}
                    >
                      Save reference
                    </button>
                  </div>
                  <div className="mt-4 space-y-2 text-sm">
                    {source.external_refs.map((item) => (
                      <div
                        key={`${item.source}:${item.external_id}`}
                        className="flex justify-between gap-3"
                      >
                        <span className="capitalize text-text-muted">
                          {item.source.replaceAll("_", " ")}
                        </span>
                        <span className="truncate font-mono">{item.label ?? item.external_id}</span>
                      </div>
                    ))}
                    {!source.external_refs.length && (
                      <span className="text-text-muted">No references recorded.</span>
                    )}
                  </div>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <select
                      className="input"
                      aria-label="External reference source"
                      value={reference.source}
                      onChange={(event) =>
                        setReference((current) => ({
                          ...current,
                          source: event.target.value as OpsMediaExternalRefSource,
                        }))
                      }
                    >
                      {[
                        "manual",
                        "google_books",
                        "open_library",
                        "imdb",
                        "tmdb",
                        "wikipedia",
                        "doi",
                        "pubmed",
                        "semantic_scholar",
                      ].map((sourceOption) => (
                        <option key={sourceOption} value={sourceOption}>
                          {sourceOption.replaceAll("_", " ")}
                        </option>
                      ))}
                    </select>
                    <input
                      className="input"
                      aria-label="External reference identifier"
                      placeholder="Identifier"
                      value={reference.external_id}
                      onChange={(event) =>
                        setReference((current) => ({ ...current, external_id: event.target.value }))
                      }
                    />
                    <input
                      className="input"
                      aria-label="External reference label"
                      placeholder="Display label"
                      value={reference.label}
                      onChange={(event) =>
                        setReference((current) => ({ ...current, label: event.target.value }))
                      }
                    />
                    <input
                      className="input"
                      aria-label="External reference URL"
                      placeholder="https://..."
                      value={reference.url}
                      onChange={(event) =>
                        setReference((current) => ({ ...current, url: event.target.value }))
                      }
                    />
                  </div>
                </form>
              </div>

              {!!source.relations.length && (
                <div className="rounded-lg border border-border-subtle bg-background p-4">
                  <h4 className="font-medium">Relations</h4>
                  <div className="mt-4 divide-y divide-border-subtle text-sm">
                    {source.relations.map((relation) => (
                      <div
                        key={`${relation.direction}:${relation.relation_type}:${relation.related_media.id}`}
                        className="flex justify-between gap-4 py-3"
                      >
                        <span className="capitalize text-text-muted">
                          {relation.direction} {relation.relation_type.replaceAll("_", " ")}
                        </span>
                        <span>{relation.related_media.title}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <form
                className="rounded-lg border border-border-subtle bg-background p-4"
                onSubmit={(event) => void recoverSplit(event)}
              >
                <div className="flex flex-col justify-between gap-3 md:flex-row md:items-start">
                  <div>
                    <h4 className="font-medium">Split recovery</h4>
                    <p className="mt-1 text-sm text-text-muted">
                      Move incorrectly merged mentions into a new canonical record.
                    </p>
                  </div>
                  <button
                    className="btn btn-secondary"
                    type="submit"
                    disabled={busy || !splitMentionIds.length || !split.title.trim()}
                  >
                    Recover selected mentions
                  </button>
                </div>
                <div className="mt-4 grid gap-5 lg:grid-cols-[1fr_1fr]">
                  <div className="space-y-2">
                    {source.mentions.map((mention) => (
                      <label
                        key={mention.id}
                        className="flex gap-3 rounded-lg border border-border-subtle p-3 text-sm"
                      >
                        <input
                          aria-label={`Recover mention from ${mention.episode_title}`}
                          type="checkbox"
                          checked={splitMentionIds.includes(mention.id)}
                          onChange={(event) =>
                            setSplitMentionIds((current) =>
                              event.target.checked
                                ? [...current, mention.id]
                                : current.filter((id) => id !== mention.id),
                            )
                          }
                        />
                        <span>
                          <span className="block text-text">{mention.episode_title}</span>
                          <span className="mt-1 block text-text-muted">
                            {mention.context ?? "No evidence excerpt."}
                          </span>
                        </span>
                      </label>
                    ))}
                    {!source.mentions.length && (
                      <p className="text-sm text-text-muted">
                        No published mentions available to recover.
                      </p>
                    )}
                  </div>
                  <div className="grid content-start gap-3 sm:grid-cols-2">
                    <select
                      className="input"
                      aria-label="Recovered media type"
                      value={split.type}
                      onChange={(event) =>
                        setSplit((current) => ({
                          ...current,
                          type: event.target.value as MediaType,
                        }))
                      }
                    >
                      {[
                        "book",
                        "movie",
                        "documentary",
                        "tv_show",
                        "study",
                        "podcast",
                        "article",
                        "standup_special",
                        "person",
                        "place",
                      ].map((type) => (
                        <option key={type} value={type}>
                          {type.replaceAll("_", " ")}
                        </option>
                      ))}
                    </select>
                    <input
                      className="input"
                      aria-label="Recovered media title"
                      placeholder="Recovered title"
                      required
                      value={split.title}
                      onChange={(event) =>
                        setSplit((current) => ({ ...current, title: event.target.value }))
                      }
                    />
                    <input
                      className="input"
                      aria-label="Recovered media creator"
                      placeholder="Creator"
                      value={split.author}
                      onChange={(event) =>
                        setSplit((current) => ({ ...current, author: event.target.value }))
                      }
                    />
                    <textarea
                      className="input min-h-20"
                      aria-label="Recovered media description"
                      placeholder="Description"
                      value={split.description}
                      onChange={(event) =>
                        setSplit((current) => ({ ...current, description: event.target.value }))
                      }
                    />
                  </div>
                </div>
              </form>
            </section>
          )}

          <section className="rounded-lg border border-border-subtle bg-surface p-5">
            <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
              <div>
                <h3 className="text-xl">Merge preview</h3>
                <p className="mt-1 text-sm text-text-muted">
                  Preview field changes, moved mentions, and aliases before committing.
                </p>
              </div>
              <button
                className="btn btn-secondary"
                type="button"
                disabled={busy || !source || !target}
                onClick={() => void createPreview()}
              >
                Generate preview
              </button>
            </div>
            {preview ? (
              <div className="mt-6 space-y-6">
                <div className="grid gap-4 sm:grid-cols-3">
                  <div className="rounded-lg border border-border-subtle bg-background p-4">
                    <div className="text-sm text-text-muted">Mentions moving</div>
                    <div className="mt-2 font-mono text-3xl">{preview.mentions_to_move}</div>
                  </div>
                  <div className="rounded-lg border border-border-subtle bg-background p-4">
                    <div className="text-sm text-text-muted">Fields changing</div>
                    <div className="mt-2 font-mono text-3xl">{preview.field_changes.length}</div>
                  </div>
                  <div className="rounded-lg border border-border-subtle bg-background p-4">
                    <div className="text-sm text-text-muted">Aliases added</div>
                    <div className="mt-2 font-mono text-3xl">{preview.alias_additions.length}</div>
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead className="text-left text-xs uppercase text-text-muted">
                      <tr>
                        <th className="pb-3 font-medium">Field</th>
                        <th className="pb-3 font-medium">Source</th>
                        <th className="pb-3 font-medium">Survivor</th>
                        <th className="pb-3 font-medium">After merge</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle">
                      {preview.field_changes.map((change) => (
                        <tr key={change.field}>
                          <td className="py-3 capitalize">{change.field.replaceAll("_", " ")}</td>
                          <td className="py-3 text-text-secondary">
                            {formatValue(change.source_value)}
                          </td>
                          <td className="py-3 text-text-secondary">
                            {formatValue(change.target_value)}
                          </td>
                          <td className="py-3 text-accent">{formatValue(change.merged_value)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!!preview.alias_additions.length && (
                  <div>
                    <h4 className="text-sm font-medium text-text-secondary">
                      New aliases on survivor
                    </h4>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {preview.alias_additions.map((alias) => (
                        <span
                          key={alias.normalized_alias}
                          className="rounded-full border border-border px-3 py-1 text-sm"
                        >
                          {alias.alias} <span className="text-text-muted">({alias.source})</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                <div className="flex justify-end">
                  <button
                    className="btn btn-primary"
                    type="button"
                    disabled={busy}
                    onClick={() => void executeMerge()}
                  >
                    Merge into survivor
                  </button>
                </div>
              </div>
            ) : (
              <p className="mt-6 text-sm text-text-muted">No merge preview generated.</p>
            )}
          </section>
        </div>
      </section>
    </div>
  );
}
