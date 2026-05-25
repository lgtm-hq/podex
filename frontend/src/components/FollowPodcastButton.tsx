import { useEffect, useState } from "react";
import { followPodcast, getCurrentAccount, getFollowedPodcasts, unfollowPodcast } from "../lib/api";

interface Props {
  podcastId: string;
  slug: string;
}

export function FollowPodcastButton({ podcastId, slug }: Props) {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);
  const [isFollowed, setIsFollowed] = useState(false);
  const [isBusy, setIsBusy] = useState(false);

  useEffect(() => {
    Promise.all([getCurrentAccount(), getFollowedPodcasts()])
      .then(([, followed]) => {
        setIsAuthenticated(true);
        setIsFollowed(followed.items.some((item) => item.podcast.id === podcastId));
      })
      .catch(() => setIsAuthenticated(false));
  }, [podcastId]);

  async function toggleFollow() {
    setIsBusy(true);
    try {
      if (isFollowed) {
        await unfollowPodcast(podcastId);
        setIsFollowed(false);
      } else {
        await followPodcast(podcastId);
        setIsFollowed(true);
      }
    } finally {
      setIsBusy(false);
    }
  }

  if (isAuthenticated === null) {
    return <span className="text-sm text-text-muted">Loading follow status...</span>;
  }
  if (!isAuthenticated) {
    return (
      <a
        className="btn btn-secondary"
        href={`/sign-in?redirect=${encodeURIComponent(`/sources/${slug}`)}`}
      >
        Sign in to follow
      </a>
    );
  }
  return (
    <button
      className="btn btn-secondary"
      type="button"
      disabled={isBusy}
      onClick={() => void toggleFollow()}
    >
      {isBusy ? "Updating..." : isFollowed ? "Following" : "Follow source"}
    </button>
  );
}
