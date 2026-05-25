import { useEffect, useState } from "react";
import { AsyncState } from "./AsyncState";
import {
  createAlertRule,
  deleteAlertRule,
  evaluateAlertRules,
  getAlertRules,
  getCurrentAccount,
  getDigests,
  getFollowedPodcasts,
  getSavedMedia,
  logoutAccount,
  removeSavedMedia,
  sendDigest,
  setAlertRuleEnabled,
  unfollowPodcast,
} from "../lib/api";
import type {
  AccountAlertRule,
  AccountAlertTargetType,
  AccountDigest,
  AccountFollowedPodcast,
  AccountSavedMedia,
  AccountUser,
} from "../lib/types";

type AccountSection = "overview" | "saved" | "follows" | "alerts";

interface AccountHomeProps {
  section?: AccountSection;
}

export function AccountHome({ section = "overview" }: AccountHomeProps) {
  const [account, setAccount] = useState<AccountUser | null>(null);
  const [saves, setSaves] = useState<AccountSavedMedia[]>([]);
  const [follows, setFollows] = useState<AccountFollowedPodcast[]>([]);
  const [alerts, setAlerts] = useState<AccountAlertRule[]>([]);
  const [digests, setDigests] = useState<AccountDigest[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSignedOut, setIsSignedOut] = useState(false);

  useEffect(() => {
    Promise.all([
      getCurrentAccount(),
      getSavedMedia(),
      getFollowedPodcasts(),
      getAlertRules(),
      getDigests(),
    ])
      .then(([nextAccount, nextSaves, nextFollows, nextAlerts, nextDigests]) => {
        setAccount(nextAccount);
        setSaves(nextSaves.items);
        setFollows(nextFollows.items);
        setAlerts(nextAlerts.items);
        setDigests(nextDigests.items);
      })
      .catch(() => setIsSignedOut(true))
      .finally(() => setIsLoading(false));
  }, []);

  async function signOut() {
    await logoutAccount();
    setAccount(null);
    setIsSignedOut(true);
  }

  async function removeSave(mediaId: string) {
    await removeSavedMedia(mediaId);
    setSaves((items) => items.filter((item) => item.media.id !== mediaId));
  }

  async function removeFollow(podcastId: string) {
    await unfollowPodcast(podcastId);
    setFollows((items) => items.filter((item) => item.podcast.id !== podcastId));
  }

  function existingAlert(targetType: AccountAlertTargetType, targetId: string) {
    return alerts.find((rule) => rule.target_type === targetType && rule.target_id === targetId);
  }

  async function addAlert(targetType: AccountAlertTargetType, targetId: string) {
    const created = await createAlertRule({
      target_type: targetType,
      target_id: targetId,
      event_type: targetType === "media" ? "new_mention" : "new_episode",
    });
    setAlerts((items) => [created, ...items.filter((item) => item.id !== created.id)]);
  }

  async function toggleAlert(rule: AccountAlertRule) {
    const updated = await setAlertRuleEnabled(rule.id, !rule.enabled);
    setAlerts((items) => items.map((item) => (item.id === updated.id ? updated : item)));
  }

  async function removeAlert(ruleId: string) {
    await deleteAlertRule(ruleId);
    setAlerts((items) => items.filter((item) => item.id !== ruleId));
  }

  async function checkAlerts() {
    const evaluated = await evaluateAlertRules();
    setNotice(
      evaluated.generated === 0
        ? "No new alert activity."
        : `${evaluated.generated} new alert update${evaluated.generated === 1 ? "" : "s"} found.`,
    );
  }

  async function deliverDigest() {
    const delivered = await sendDigest();
    const digest = delivered.digest;
    if (digest) {
      setDigests((items) => [digest, ...items]);
    }
    setNotice(delivered.delivered ? "Digest delivered to your email." : "No new activity to send.");
  }

  if (isLoading) {
    return <AsyncState title="Loading your account..." variant="loading" />;
  }
  if (isSignedOut || account === null) {
    return (
      <div className="space-y-5">
        <AsyncState
          title="Sign in to manage your Podex account"
          message="Saved references, followed sources, and alerts are available after sign-in."
        />
        <a className="btn btn-primary" href="/sign-in">
          Sign in
        </a>
      </div>
    );
  }
  const showSaved = section === "overview" || section === "saved";
  const showFollows = section === "overview" || section === "follows";
  const showAlerts = section === "overview" || section === "alerts";
  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border-subtle bg-surface p-5">
        <p className="text-xs uppercase text-text-muted">Signed in as</p>
        <p className="mt-2 text-lg text-text">{account.email}</p>
      </div>
      {showSaved && (
        <section className="space-y-3">
          <div>
            <p className="text-xs uppercase text-text-muted">Saved references</p>
            <h2 className="mt-1 text-xl text-text">Your reading and watching list</h2>
          </div>
          {saves.length === 0 ? (
            <p className="text-text-secondary">
              No saved references yet. Save one from any reference page.
            </p>
          ) : (
            saves.map((save) => (
              <div
                className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border-subtle p-4"
                key={save.media.id}
              >
                <div>
                  <a
                    className="font-medium text-text hover:text-accent"
                    href={`/media/${save.media.id}`}
                  >
                    {save.media.title}
                  </a>
                  <p className="mt-1 text-sm text-text-muted">
                    {save.media.author ?? save.media.type}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    className="btn btn-secondary"
                    type="button"
                    disabled={Boolean(existingAlert("media", save.media.id))}
                    onClick={() => void addAlert("media", save.media.id)}
                  >
                    Alert on mentions
                  </button>
                  <button
                    className="btn btn-secondary"
                    type="button"
                    onClick={() => void removeSave(save.media.id)}
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))
          )}
        </section>
      )}
      {showFollows && (
        <section className="space-y-3">
          <div>
            <p className="text-xs uppercase text-text-muted">Followed sources</p>
            <h2 className="mt-1 text-xl text-text">Podcasts you track</h2>
          </div>
          {follows.length === 0 ? (
            <p className="text-text-secondary">
              No followed sources yet. Follow one from any source page.
            </p>
          ) : (
            follows.map((follow) => (
              <div
                className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border-subtle p-4"
                key={follow.podcast.id}
              >
                <div>
                  <a
                    className="font-medium text-text hover:text-accent"
                    href={`/sources/${follow.podcast.slug}`}
                  >
                    {follow.podcast.name}
                  </a>
                  <p className="mt-1 text-sm text-text-muted">
                    {follow.podcast.episode_count ?? 0} episodes
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    className="btn btn-secondary"
                    type="button"
                    disabled={Boolean(existingAlert("podcast", follow.podcast.id))}
                    onClick={() => void addAlert("podcast", follow.podcast.id)}
                  >
                    Alert on episodes
                  </button>
                  <button
                    className="btn btn-secondary"
                    type="button"
                    onClick={() => void removeFollow(follow.podcast.id)}
                  >
                    Unfollow
                  </button>
                </div>
              </div>
            ))
          )}
        </section>
      )}
      {showAlerts && (
        <section className="space-y-3">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-xs uppercase text-text-muted">Alerts</p>
              <h2 className="mt-1 text-xl text-text">Activity rules</h2>
            </div>
            <button className="btn btn-secondary" type="button" onClick={() => void checkAlerts()}>
              Check now
            </button>
          </div>
          {notice && (
            <p role="status" className="text-sm text-accent">
              {notice}
            </p>
          )}
          {alerts.length === 0 ? (
            <p className="text-text-secondary">
              No alert rules yet. Add one to a saved reference or followed source.
            </p>
          ) : (
            alerts.map((rule) => (
              <div
                className="flex items-center justify-between gap-4 rounded-lg border border-border-subtle p-4"
                key={rule.id}
              >
                <p className="text-sm text-text-secondary">
                  {rule.event_type === "new_mention" ? "New mentions" : "New episodes"} for{" "}
                  {rule.target_id}
                </p>
                <div className="flex gap-2">
                  <button
                    className="btn btn-secondary"
                    type="button"
                    onClick={() => void toggleAlert(rule)}
                  >
                    {rule.enabled ? "Pause" : "Resume"}
                  </button>
                  <button
                    className="btn btn-secondary"
                    type="button"
                    onClick={() => void removeAlert(rule.id)}
                  >
                    Delete alert
                  </button>
                </div>
              </div>
            ))
          )}
        </section>
      )}
      {showAlerts && (
        <section className="space-y-3">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-xs uppercase text-text-muted">Digests</p>
              <h2 className="mt-1 text-xl text-text">Email delivery history</h2>
            </div>
            <button className="btn btn-primary" type="button" onClick={() => void deliverDigest()}>
              Send digest
            </button>
          </div>
          {digests.length === 0 ? (
            <p className="text-text-secondary">
              No digests sent yet. New alert activity can be delivered here.
            </p>
          ) : (
            digests.map((digest) => (
              <div className="rounded-lg border border-border-subtle p-4" key={digest.id}>
                <p className="font-medium text-text">{digest.subject}</p>
                <p className="mt-1 text-sm text-text-muted">
                  {digest.event_count} delivered update{digest.event_count === 1 ? "" : "s"}
                </p>
              </div>
            ))
          )}
        </section>
      )}
      <button className="btn btn-secondary" type="button" onClick={() => void signOut()}>
        Sign out
      </button>
    </div>
  );
}
