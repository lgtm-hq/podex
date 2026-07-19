import { useCallback, useEffect, useState } from "react";

import {
  archiveOpsPodcast,
  createOpsPodcast,
  getOpsPodcasts,
  OpsApiError,
  type OpsPodcast,
  updateOpsPodcast,
} from "../lib/ops";
import OpsShell from "./OpsShell";

const INPUT_CLASS =
  "w-full rounded border border-[color:var(--color-hairline)] bg-white px-3 py-2 text-sm";

/** Podcast manager: add, edit, pause, and archive catalog sources. */
export default function OpsPodcastManager() {
  return (
    <OpsShell active="/ops/podcasts">
      <ManagerBody />
    </OpsShell>
  );
}

function ManagerBody() {
  const [podcasts, setPodcasts] = useState<OpsPodcast[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [rssUrl, setRssUrl] = useState("");
  const [editing, setEditing] = useState<OpsPodcast | null>(null);
  const [editName, setEditName] = useState("");

  const describeError = (cause: unknown): string =>
    cause instanceof OpsApiError && cause.status === 401
      ? "The ops key was rejected. Use “Change key” to retry."
      : cause instanceof OpsApiError && cause.status === 409
        ? "That slug already exists."
        : "Request failed.";

  const reload = useCallback(() => {
    getOpsPodcasts({ sort: "created_at", order: "desc" })
      .then((page) => {
        setPodcasts(page.items);
        setError(null);
      })
      .catch((cause: unknown) => setError(describeError(cause)));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const submitCreate = (event: React.FormEvent) => {
    event.preventDefault();
    createOpsPodcast({
      name,
      slug,
      status: "active",
      sources: rssUrl ? { rss_url: rssUrl } : {},
    })
      .then(() => {
        setName("");
        setSlug("");
        setRssUrl("");
        reload();
      })
      .catch((cause: unknown) => setError(describeError(cause)));
  };

  const submitRename = (event: React.FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    updateOpsPodcast(editing.id, { name: editName })
      .then(() => {
        setEditing(null);
        reload();
      })
      .catch((cause: unknown) => setError(describeError(cause)));
  };

  return (
    <div>
      <form
        className="max-w-xl rounded-lg border border-[color:var(--color-hairline)] bg-[color:var(--color-surface)] p-5"
        onSubmit={submitCreate}
      >
        <h2 className="font-display text-xl">Add podcast</h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <input
            className={INPUT_CLASS}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Name"
            aria-label="Podcast name"
            required
          />
          <input
            className={INPUT_CLASS}
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
            placeholder="slug"
            aria-label="Podcast slug"
            required
          />
        </div>
        <input
          className={`${INPUT_CLASS} mt-3`}
          value={rssUrl}
          onChange={(event) => setRssUrl(event.target.value)}
          placeholder="RSS feed URL (optional)"
          aria-label="RSS feed URL"
        />
        <button
          className="mt-4 rounded bg-[color:var(--color-accent)] px-4 py-2 text-sm text-white"
          type="submit"
        >
          Create
        </button>
      </form>

      {error ? <p className="mt-4 text-sm text-red-700">{error}</p> : null}

      <table className="mt-8 w-full text-left text-sm">
        <thead>
          <tr className="border-b border-[color:var(--color-hairline)] text-xs uppercase tracking-wide text-[color:var(--color-muted)]">
            <th className="py-2 pr-4">Podcast</th>
            <th className="py-2 pr-4">Status</th>
            <th className="py-2 pr-4">Episodes</th>
            <th className="py-2 pr-4">Mentions</th>
            <th className="py-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {podcasts.map((podcast) => (
            <tr
              key={podcast.id}
              className="border-b border-[color:var(--color-hairline)]"
            >
              <td className="py-3 pr-4">
                {editing?.id === podcast.id ? (
                  <form className="flex gap-2" onSubmit={submitRename}>
                    <input
                      className={INPUT_CLASS}
                      value={editName}
                      onChange={(event) => setEditName(event.target.value)}
                      aria-label="New name"
                    />
                    <button className="text-xs underline" type="submit">
                      Save
                    </button>
                    <button
                      className="text-xs text-[color:var(--color-muted)] underline"
                      type="button"
                      onClick={() => setEditing(null)}
                    >
                      Cancel
                    </button>
                  </form>
                ) : (
                  <>
                    <span className="font-medium">{podcast.name}</span>{" "}
                    <span className="text-[color:var(--color-muted)]">
                      /{podcast.slug}
                    </span>
                  </>
                )}
              </td>
              <td className="py-3 pr-4 capitalize">{podcast.status}</td>
              <td className="py-3 pr-4">{podcast.episode_count}</td>
              <td className="py-3 pr-4">{podcast.mention_count}</td>
              <td className="py-3">
                <button
                  className="text-xs underline"
                  type="button"
                  onClick={() => {
                    setEditing(podcast);
                    setEditName(podcast.name);
                  }}
                >
                  Rename
                </button>{" "}
                <button
                  className="text-xs text-[color:var(--color-muted)] underline"
                  type="button"
                  onClick={() => {
                    archiveOpsPodcast(podcast.id)
                      .then(reload)
                      .catch((cause: unknown) => setError(describeError(cause)));
                  }}
                >
                  Archive
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {podcasts.length === 0 && !error ? (
        <p className="mt-4 text-sm text-[color:var(--color-muted)]">
          No podcasts yet.
        </p>
      ) : null}
    </div>
  );
}
