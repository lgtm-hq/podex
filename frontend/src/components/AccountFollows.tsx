import { useCallback, useEffect, useState } from "react";

import {
  type FollowedPodcastList,
  getFollowedPodcasts,
  unfollowPodcast,
} from "../lib/account";
import AccountShell from "./AccountShell";

/** Followed podcasts list with unfollow. */
export default function AccountFollows() {
  return (
    <AccountShell active="/account/follows">
      {() => <FollowsBody />}
    </AccountShell>
  );
}

function FollowsBody() {
  const [follows, setFollows] = useState<FollowedPodcastList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    getFollowedPodcasts()
      .then(setFollows)
      .catch(() => setError("Unable to load followed podcasts."));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (follows === null) {
    return <p className="text-sm text-[color:var(--color-muted)]">Loading…</p>;
  }
  if (follows.items.length === 0) {
    return (
      <p className="text-sm text-[color:var(--color-muted)]">
        You are not following any podcasts yet.
      </p>
    );
  }

  return (
    <ul className="space-y-3">
      {follows.items.map((item) => (
        <li
          key={item.podcast.id}
          className="flex items-center justify-between rounded-lg border border-[color:var(--color-hairline)] p-4"
        >
          <div>
            <a
              className="font-medium underline"
              href={`/podcasts/${item.podcast.id}`}
            >
              {item.podcast.name}
            </a>
            <p className="mt-1 text-xs text-[color:var(--color-muted)]">
              followed {new Date(item.followed_at).toLocaleDateString()}
            </p>
          </div>
          <button
            className="text-xs underline"
            type="button"
            onClick={() => {
              unfollowPodcast(item.podcast.id)
                .then(reload)
                .catch(() => setError("Unable to unfollow that podcast."));
            }}
          >
            Unfollow
          </button>
        </li>
      ))}
    </ul>
  );
}
