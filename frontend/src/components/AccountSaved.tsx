import { useCallback, useEffect, useState } from "react";

import {
  getSavedMedia,
  removeSavedMedia,
  type SavedMediaList,
} from "../lib/account";
import AccountShell from "./AccountShell";

/** Saved media list with removal. */
export default function AccountSaved() {
  return (
    <AccountShell active="/account/saved">{() => <SavedBody />}</AccountShell>
  );
}

function SavedBody() {
  const [saved, setSaved] = useState<SavedMediaList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    getSavedMedia()
      .then(setSaved)
      .catch(() => setError("Unable to load saved media."));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (saved === null) {
    return <p className="text-sm text-[color:var(--color-muted)]">Loading…</p>;
  }
  if (saved.items.length === 0) {
    return (
      <p className="text-sm text-[color:var(--color-muted)]">
        Nothing saved yet. Browse the catalog and save media to keep it here.
      </p>
    );
  }

  return (
    <ul className="space-y-3">
      {saved.items.map((item) => (
        <li
          key={item.media.id}
          className="flex items-center justify-between rounded-lg border border-[color:var(--color-hairline)] p-4"
        >
          <div>
            <a className="font-medium underline" href={`/media/${item.media.id}`}>
              {item.media.title}
            </a>
            <p className="mt-1 text-xs text-[color:var(--color-muted)]">
              {item.media.type}
              {item.media.author ? ` · ${item.media.author}` : ""}
              {" · saved "}
              {new Date(item.saved_at).toLocaleDateString()}
            </p>
          </div>
          <button
            className="text-xs underline"
            type="button"
            onClick={() => {
              removeSavedMedia(item.media.id)
                .then(reload)
                .catch(() => setError("Unable to remove that save."));
            }}
          >
            Remove
          </button>
        </li>
      ))}
    </ul>
  );
}
