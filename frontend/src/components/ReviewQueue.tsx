import { type SyntheticEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  approveOpsReviewItem,
  getOpsReviewQueue,
  mergeOpsReviewItem,
  reclassifyOpsReviewItem,
  rejectOpsReviewItem,
  splitOpsReviewItem,
} from "../lib/api";
import type {
  MediaType,
  OpsReviewPriority,
  OpsReviewQueueItem,
  OpsReviewStatus,
} from "../lib/types";

type Action = "merge" | "reclassify" | "split" | null;

interface SplitCandidate {
  type: MediaType;
  raw_title: string;
  suggested_author: string;
}

const mediaTypes: MediaType[] = [
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
];

function label(value: string): string {
  return value.replaceAll("_", " ");
}

function formatDate(value?: string): string {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function timestamp(value?: number): string {
  if (value === undefined) {
    return "-";
  }
  const minutes = Math.floor(value / 60);
  return `${minutes}:${String(value % 60).padStart(2, "0")}`;
}

function tone(status: string): string {
  if (["approved", "published"].includes(status)) {
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
  }
  if (["rejected", "failed"].includes(status)) {
    return "border-red-500/30 bg-red-500/10 text-red-200";
  }
  if (["pending", "pending_review", "in_review"].includes(status)) {
    return "border-amber-500/30 bg-amber-500/10 text-amber-200";
  }
  return "border-border bg-background text-text-secondary";
}

function SplitFields({
  rows,
  setRows,
}: {
  rows: SplitCandidate[];
  setRows: (rows: SplitCandidate[]) => void;
}) {
  const change = (index: number, changes: Partial<SplitCandidate>) => {
    setRows(rows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...changes } : row)));
  };

  return (
    <div className="space-y-3">
      {rows.map((row, index) => (
        <div
          key={index}
          className="grid gap-3 rounded-lg border border-border-subtle p-3 md:grid-cols-[150px_1fr_1fr_auto]"
        >
          <select
            aria-label={`Split type ${index + 1}`}
            className="input"
            value={row.type}
            onChange={(event) => change(index, { type: event.target.value as MediaType })}
          >
            {mediaTypes.map((type) => (
              <option key={type} value={type}>
                {label(type)}
              </option>
            ))}
          </select>
          <input
            aria-label={`Split title ${index + 1}`}
            className="input"
            placeholder="Extracted title"
            value={row.raw_title}
            required
            onChange={(event) => change(index, { raw_title: event.target.value })}
          />
          <input
            aria-label={`Split author ${index + 1}`}
            className="input"
            placeholder="Author or creator"
            value={row.suggested_author}
            onChange={(event) => change(index, { suggested_author: event.target.value })}
          />
          <button
            className="btn btn-ghost"
            type="button"
            disabled={rows.length <= 2}
            onClick={() => setRows(rows.filter((_, rowIndex) => rowIndex !== index))}
          >
            Remove
          </button>
        </div>
      ))}
      <button
        className="btn btn-secondary"
        type="button"
        onClick={() => setRows([...rows, { type: "book", raw_title: "", suggested_author: "" }])}
      >
        Add replacement
      </button>
    </div>
  );
}

export default function ReviewQueue() {
  const [items, setItems] = useState<OpsReviewQueueItem[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<OpsReviewStatus | "">("pending");
  const [priority, setPriority] = useState<OpsReviewPriority | "">("");
  const [mediaType, setMediaType] = useState<MediaType | "">("");
  const [source, setSource] = useState("");
  const [assignment, setAssignment] = useState("");
  const [minimumConfidence, setMinimumConfidence] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actorName, setActorName] = useState("");
  const [note, setNote] = useState("");
  const [action, setAction] = useState<Action>(null);
  const [mergeTarget, setMergeTarget] = useState("");
  const [editType, setEditType] = useState<MediaType>("book");
  const [editTitle, setEditTitle] = useState("");
  const [editNormalizedTitle, setEditNormalizedTitle] = useState("");
  const [editAuthor, setEditAuthor] = useState("");
  const [splitCandidates, setSplitCandidates] = useState<SplitCandidate[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getOpsReviewQueue(status, priority, 100);
      setItems(result.items);
      setTotal(result.total);
      setSelectedId((current) => {
        if (current && result.items.some((item) => item.id === current)) {
          return current;
        }
        return result.items[0]?.id ?? null;
      });
    } catch {
      setError("Unable to load review queue.");
    } finally {
      setLoading(false);
    }
  }, [priority, status]);

  useEffect(() => {
    void load();
  }, [load]);

  const filteredItems = useMemo(() => {
    const normalizedSource = source.trim().toLowerCase();
    const normalizedAssignment = assignment.trim().toLowerCase();
    return items.filter((item) => {
      const sourceText = [
        item.podcast_name,
        item.podcast_slug,
        item.candidate.extraction_source ?? "",
        item.candidate.source_job_backend ?? "",
      ]
        .join(" ")
        .toLowerCase();
      const assignee = (item.assigned_to ?? "unassigned").toLowerCase();
      return (
        (!mediaType || item.candidate.type === mediaType) &&
        (!normalizedSource || sourceText.includes(normalizedSource)) &&
        (!normalizedAssignment || assignee.includes(normalizedAssignment)) &&
        item.candidate.confidence * 100 >= minimumConfidence
      );
    });
  }, [assignment, items, mediaType, minimumConfidence, source]);

  const selected = filteredItems.find((item) => item.id === selectedId) ?? filteredItems[0];
  const isOpen = selected && ["pending", "in_review"].includes(selected.status);

  useEffect(() => {
    if (filteredItems.length > 0 && !filteredItems.some((item) => item.id === selectedId)) {
      setSelectedId(filteredItems[0].id);
    }
  }, [filteredItems, selectedId]);

  const decisionPayload = {
    actor_name: actorName.trim() || null,
    note: note.trim() || null,
  };

  const runDecision = async (nextAction: "approve" | "reject") => {
    if (!selected) {
      return;
    }
    setActing(true);
    setError(null);
    setNotice(null);
    try {
      if (nextAction === "approve") {
        await approveOpsReviewItem(selected.id, decisionPayload);
      } else {
        await rejectOpsReviewItem(selected.id, decisionPayload);
      }
      setNotice(
        `${selected.candidate.raw_title} ${nextAction === "approve" ? "approved" : "rejected"}.`,
      );
      await load();
    } catch {
      setError(`Unable to ${nextAction} this review item.`);
    } finally {
      setActing(false);
    }
  };

  const openAction = (nextAction: Exclude<Action, null>) => {
    if (!selected) {
      return;
    }
    setAction(nextAction);
    setError(null);
    if (nextAction === "merge") {
      setMergeTarget("");
    } else if (nextAction === "reclassify") {
      setEditType(selected.candidate.type);
      setEditTitle(selected.candidate.raw_title);
      setEditNormalizedTitle(selected.candidate.normalized_title ?? "");
      setEditAuthor(selected.candidate.suggested_author ?? "");
    } else {
      setSplitCandidates([
        {
          type: selected.candidate.type,
          raw_title: selected.candidate.raw_title,
          suggested_author: selected.candidate.suggested_author ?? "",
        },
        { type: "book", raw_title: "", suggested_author: "" },
      ]);
    }
  };

  const submitAction = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected || !action) {
      return;
    }
    setActing(true);
    setError(null);
    setNotice(null);
    try {
      if (action === "merge") {
        if (!mergeTarget.trim()) {
          setError("A canonical media ID is required for merging.");
          return;
        }
        await mergeOpsReviewItem(selected.id, {
          ...decisionPayload,
          target_id: mergeTarget.trim(),
        });
        setNotice(`${selected.candidate.raw_title} merged into ${mergeTarget.trim()}.`);
      } else if (action === "reclassify") {
        await reclassifyOpsReviewItem(selected.id, {
          ...decisionPayload,
          type: editType,
          raw_title: editTitle.trim(),
          normalized_title: editNormalizedTitle.trim() || null,
          suggested_author: editAuthor.trim() || null,
        });
        setNotice(`${selected.candidate.raw_title} reclassified.`);
      } else {
        await splitOpsReviewItem(selected.id, {
          ...decisionPayload,
          candidates: splitCandidates.map((candidate) => ({
            type: candidate.type,
            raw_title: candidate.raw_title.trim(),
            suggested_author: candidate.suggested_author.trim() || null,
          })),
        });
        setNotice(
          `${selected.candidate.raw_title} split into ${splitCandidates.length} review items.`,
        );
      }
      setAction(null);
      await load();
    } catch {
      setError(`Unable to ${action} this review item.`);
    } finally {
      setActing(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border-subtle bg-surface p-5">
        <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
          <div>
            <h2 className="text-xl">Candidate queue</h2>
            <p className="mt-1 text-sm text-text-muted">
              {total} items match server filters. Narrow by evidence source, assignment, type, or
              confidence.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <select
              className="input"
              aria-label="Review status"
              value={status}
              onChange={(event) => setStatus(event.target.value as OpsReviewStatus | "")}
            >
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="in_review">In review</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
              <option value="merged">Merged</option>
              <option value="split">Split</option>
            </select>
            <select
              className="input"
              aria-label="Review priority"
              value={priority}
              onChange={(event) => setPriority(event.target.value as OpsReviewPriority | "")}
            >
              <option value="">All priorities</option>
              <option value="high">High priority</option>
              <option value="medium">Medium priority</option>
              <option value="low">Low priority</option>
            </select>
            <select
              className="input"
              aria-label="Media type"
              value={mediaType}
              onChange={(event) => setMediaType(event.target.value as MediaType | "")}
            >
              <option value="">All types</option>
              {mediaTypes.map((type) => (
                <option key={type} value={type}>
                  {label(type)}
                </option>
              ))}
            </select>
            <input
              className="input"
              aria-label="Evidence source"
              placeholder="Source or backend"
              value={source}
              onChange={(event) => setSource(event.target.value)}
            />
            <input
              className="input"
              aria-label="Assignment"
              placeholder="Assignee"
              value={assignment}
              onChange={(event) => setAssignment(event.target.value)}
            />
          </div>
        </div>
        <label className="mt-5 flex max-w-md items-center gap-4 text-sm text-text-secondary">
          <span className="whitespace-nowrap">Confidence {minimumConfidence}%+</span>
          <input
            className="w-full accent-amber-500"
            type="range"
            min="0"
            max="100"
            value={minimumConfidence}
            onChange={(event) => setMinimumConfidence(Number(event.target.value))}
          />
        </label>
      </section>

      {(error || notice) && (
        <div
          role="status"
          className={`rounded-lg border p-4 text-sm ${error ? "border-red-500/30 bg-red-500/10 text-red-100" : "border-emerald-500/30 bg-emerald-500/10 text-emerald-100"}`}
        >
          {error ?? notice}
        </div>
      )}

      {loading ? (
        <div className="rounded-lg border border-border-subtle bg-surface p-8 text-sm text-text-muted">
          Loading review evidence...
        </div>
      ) : filteredItems.length === 0 ? (
        <div className="rounded-lg border border-border-subtle bg-surface p-8 text-sm text-text-muted">
          No review items match these filters.
        </div>
      ) : (
        <section className="grid gap-6 xl:grid-cols-[360px_1fr]">
          <div className="overflow-hidden rounded-lg border border-border-subtle bg-surface">
            <div className="border-b border-border-subtle p-4 text-sm text-text-muted">
              {filteredItems.length} visible candidate{filteredItems.length === 1 ? "" : "s"}
            </div>
            <div className="divide-y divide-border-subtle">
              {filteredItems.map((item) => (
                <button
                  key={item.id}
                  className={`w-full p-4 text-left transition-colors ${selected?.id === item.id ? "bg-accent/10" : "hover:bg-background"}`}
                  type="button"
                  onClick={() => setSelectedId(item.id)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="rounded-full border border-border px-2 py-1 text-xs capitalize text-text-secondary">
                      {label(item.candidate.type)}
                    </span>
                    <span className="font-mono text-xs text-text-muted">
                      {Math.round(item.candidate.confidence * 100)}%
                    </span>
                  </div>
                  <div className="mt-3 truncate font-medium text-text">
                    {item.candidate.raw_title}
                  </div>
                  <div className="mt-1 truncate text-sm text-text-muted">{item.episode_title}</div>
                  <div className="mt-3 flex items-center justify-between">
                    <span
                      className={`rounded-full border px-2 py-1 text-xs capitalize ${tone(item.status)}`}
                    >
                      {label(item.status)}
                    </span>
                    <span className="text-xs uppercase text-text-muted">{item.priority}</span>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {selected && (
            <div className="space-y-6">
              <div className="rounded-lg border border-border-subtle bg-surface p-5">
                <div className="flex flex-col justify-between gap-5 md:flex-row">
                  <div>
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span
                        className={`rounded-full border px-2 py-1 capitalize ${tone(selected.status)}`}
                      >
                        {label(selected.status)}
                      </span>
                      <span className="rounded-full border border-border px-2 py-1 capitalize text-text-secondary">
                        {label(selected.candidate.type)}
                      </span>
                      <span className="font-mono text-text-muted">
                        {Math.round(selected.candidate.confidence * 100)}% confidence
                      </span>
                    </div>
                    <h2 className="mt-4 text-2xl">{selected.candidate.raw_title}</h2>
                    <p className="mt-2 text-sm text-text-secondary">
                      {selected.candidate.suggested_author ?? "No creator proposed"} /{" "}
                      {selected.podcast_name}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2 md:justify-end">
                    <a className="btn btn-secondary" href={`/episodes/${selected.episode_id}`}>
                      Open episode evidence
                    </a>
                    {selected.candidate.media_id && (
                      <a
                        className="btn btn-secondary"
                        href={`/media/${selected.candidate.media_id}`}
                      >
                        Canonical media
                      </a>
                    )}
                  </div>
                </div>
                {isOpen && (
                  <>
                    <div className="mt-6 grid gap-3 md:grid-cols-2">
                      <input
                        className="input"
                        aria-label="Operator name"
                        placeholder="Operator name"
                        value={actorName}
                        onChange={(event) => setActorName(event.target.value)}
                      />
                      <input
                        className="input"
                        aria-label="Decision note"
                        placeholder="Decision note"
                        value={note}
                        onChange={(event) => setNote(event.target.value)}
                      />
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <button
                        className="btn btn-primary"
                        type="button"
                        disabled={acting}
                        onClick={() => void runDecision("approve")}
                      >
                        Approve
                      </button>
                      <button
                        className="btn btn-secondary"
                        type="button"
                        disabled={acting}
                        onClick={() => void runDecision("reject")}
                      >
                        Reject
                      </button>
                      <button
                        className="btn btn-secondary"
                        type="button"
                        disabled={acting}
                        onClick={() => openAction("merge")}
                      >
                        Merge
                      </button>
                      <button
                        className="btn btn-secondary"
                        type="button"
                        disabled={acting}
                        onClick={() => openAction("split")}
                      >
                        Split
                      </button>
                      <button
                        className="btn btn-secondary"
                        type="button"
                        disabled={acting}
                        onClick={() => openAction("reclassify")}
                      >
                        Reclassify
                      </button>
                    </div>
                  </>
                )}
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-lg border border-border-subtle bg-surface p-5">
                  <h3 className="text-lg">Transcript evidence</h3>
                  <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <dt className="text-text-muted">Episode</dt>
                      <dd className="mt-1 text-text">{selected.episode_title}</dd>
                    </div>
                    <div>
                      <dt className="text-text-muted">Timestamp</dt>
                      <dd className="mt-1 font-mono text-text">
                        {timestamp(selected.candidate.timestamp_seconds)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-text-muted">Extraction source</dt>
                      <dd className="mt-1 text-text">
                        {selected.candidate.extraction_source ?? "-"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-text-muted">Assigned to</dt>
                      <dd className="mt-1 text-text">{selected.assigned_to ?? "Unassigned"}</dd>
                    </div>
                  </dl>
                  <blockquote className="mt-5 border-l-2 border-accent pl-4 text-sm leading-7 text-text-secondary">
                    {selected.candidate.context ?? "No transcript snippet captured."}
                  </blockquote>
                </div>
                <div className="rounded-lg border border-border-subtle bg-surface p-5">
                  <h3 className="text-lg">Extraction history</h3>
                  <div className="mt-4 space-y-3">
                    {selected.candidate.extraction_jobs.length ? (
                      selected.candidate.extraction_jobs.map((job) => (
                        <div
                          key={job.id}
                          className="flex items-start justify-between gap-4 border-b border-border-subtle pb-3 text-sm last:border-0"
                        >
                          <div>
                            <div className="font-mono text-xs text-text-muted">{job.id}</div>
                            <div className="mt-1 text-text-secondary">
                              {job.backend ?? "backend"} / {job.model ?? "default model"}
                            </div>
                          </div>
                          <div className="text-right">
                            <span
                              className={`rounded-full border px-2 py-1 text-xs capitalize ${tone(job.status)}`}
                            >
                              {label(job.status)}
                            </span>
                            <div className="mt-2 text-xs text-text-muted">
                              {formatDate(job.created_at)}
                            </div>
                          </div>
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-text-muted">No extraction jobs recorded.</p>
                    )}
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-border-subtle bg-surface p-5">
                <h3 className="text-lg">Candidate provenance</h3>
                <div className="mt-4 overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead className="text-left text-xs uppercase text-text-muted">
                      <tr>
                        <th className="pb-3 font-medium">Event</th>
                        <th className="pb-3 font-medium">Change</th>
                        <th className="pb-3 font-medium">Title</th>
                        <th className="pb-3 font-medium">When</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle">
                      {selected.candidate.provenance.map((entry) => (
                        <tr key={entry.id}>
                          <td className="py-3 capitalize">{label(entry.event_type)}</td>
                          <td className="py-3 text-text-secondary">
                            {entry.change_summary ?? "-"}
                          </td>
                          <td className="py-3">{entry.raw_title}</td>
                          <td className="py-3 text-text-muted">{formatDate(entry.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!selected.candidate.provenance.length && (
                    <p className="py-3 text-sm text-text-muted">No provenance events recorded.</p>
                  )}
                </div>
              </div>
            </div>
          )}
        </section>
      )}

      {action && selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <form
            role="dialog"
            aria-label={`${label(action)} review item`}
            className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-lg border border-border bg-surface p-6 shadow-2xl"
            onSubmit={(event) => void submitAction(event)}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs uppercase text-accent">{label(action)} candidate</p>
                <h2 className="mt-2 text-2xl">{selected.candidate.raw_title}</h2>
              </div>
              <button className="btn btn-ghost" type="button" onClick={() => setAction(null)}>
                Close
              </button>
            </div>
            {action === "merge" && (
              <label className="mt-6 block space-y-2 text-sm text-text-secondary">
                <span>Canonical media ID</span>
                <input
                  className="input w-full"
                  value={mergeTarget}
                  placeholder="med_..."
                  required
                  onChange={(event) => setMergeTarget(event.target.value)}
                />
              </label>
            )}
            {action === "reclassify" && (
              <div className="mt-6 grid gap-4 md:grid-cols-2">
                <select
                  className="input"
                  aria-label="Reclassified media type"
                  value={editType}
                  onChange={(event) => setEditType(event.target.value as MediaType)}
                >
                  {mediaTypes.map((type) => (
                    <option key={type} value={type}>
                      {label(type)}
                    </option>
                  ))}
                </select>
                <input
                  className="input"
                  aria-label="Reclassified title"
                  required
                  value={editTitle}
                  onChange={(event) => setEditTitle(event.target.value)}
                />
                <input
                  className="input"
                  aria-label="Normalized title"
                  placeholder="Normalized title"
                  value={editNormalizedTitle}
                  onChange={(event) => setEditNormalizedTitle(event.target.value)}
                />
                <input
                  className="input"
                  aria-label="Suggested author"
                  placeholder="Author or creator"
                  value={editAuthor}
                  onChange={(event) => setEditAuthor(event.target.value)}
                />
              </div>
            )}
            {action === "split" && (
              <div className="mt-6">
                <SplitFields rows={splitCandidates} setRows={setSplitCandidates} />
              </div>
            )}
            <div className="mt-6 flex justify-end gap-3">
              <button className="btn btn-secondary" type="button" onClick={() => setAction(null)}>
                Cancel
              </button>
              <button className="btn btn-primary" type="submit" disabled={acting}>
                {acting ? "Working..." : `Confirm ${action}`}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
