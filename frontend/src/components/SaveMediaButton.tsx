import { useEffect, useState } from "react";
import { getCurrentAccount, getSavedMedia, removeSavedMedia, saveMedia } from "../lib/api";

interface Props {
  mediaId: string;
}

export function SaveMediaButton({ mediaId }: Props) {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);
  const [isSaved, setIsSaved] = useState(false);
  const [isBusy, setIsBusy] = useState(false);

  useEffect(() => {
    Promise.all([getCurrentAccount(), getSavedMedia()])
      .then(([, saves]) => {
        setIsAuthenticated(true);
        setIsSaved(saves.items.some((item) => item.media.id === mediaId));
      })
      .catch(() => setIsAuthenticated(false));
  }, [mediaId]);

  async function toggleSave() {
    setIsBusy(true);
    try {
      if (isSaved) {
        await removeSavedMedia(mediaId);
        setIsSaved(false);
      } else {
        await saveMedia(mediaId);
        setIsSaved(true);
      }
    } finally {
      setIsBusy(false);
    }
  }

  if (isAuthenticated === null) {
    return <span className="text-sm text-text-muted">Loading save status...</span>;
  }
  if (!isAuthenticated) {
    return (
      <a
        className="btn btn-secondary"
        href={`/sign-in?redirect=${encodeURIComponent(`/media/${mediaId}`)}`}
      >
        Sign in to save
      </a>
    );
  }
  return (
    <button
      className="btn btn-secondary"
      type="button"
      disabled={isBusy}
      onClick={() => void toggleSave()}
    >
      {isBusy ? "Saving..." : isSaved ? "Saved" : "Save reference"}
    </button>
  );
}
