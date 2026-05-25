import { useEffect, useState } from "react";
import { AsyncState } from "./AsyncState";
import {
  beginAccountCheckout,
  getAccountPreferences,
  getAccountSubscription,
  getCurrentAccount,
  updateAccountPreferences,
} from "../lib/api";
import type {
  AccountDigestFrequency,
  AccountPreference,
  AccountSubscription,
  AccountUser,
} from "../lib/types";

export function AccountSettings() {
  const [account, setAccount] = useState<AccountUser | null>(null);
  const [preferences, setPreferences] = useState<AccountPreference | null>(null);
  const [subscription, setSubscription] = useState<AccountSubscription | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSignedOut, setIsSignedOut] = useState(false);

  useEffect(() => {
    Promise.all([getCurrentAccount(), getAccountPreferences(), getAccountSubscription()])
      .then(([nextAccount, nextPreferences, nextSubscription]) => {
        setAccount(nextAccount);
        setPreferences(nextPreferences);
        setSubscription(nextSubscription);
      })
      .catch(() => setIsSignedOut(true))
      .finally(() => setIsLoading(false));
  }, []);

  async function saveSettings() {
    if (preferences === null) {
      return;
    }
    const updated = await updateAccountPreferences({
      digest_enabled: preferences.digest_enabled,
      digest_frequency: preferences.digest_frequency,
    });
    setPreferences(updated);
    setNotice("Notification settings saved.");
  }

  async function beginUpgrade() {
    const checkout = await beginAccountCheckout();
    window.location.assign(checkout.checkout_url);
  }

  if (isLoading) {
    return <AsyncState title="Loading settings..." variant="loading" />;
  }
  if (isSignedOut || account === null || preferences === null || subscription === null) {
    return (
      <div className="space-y-5">
        <AsyncState
          title="Sign in to manage settings"
          message="Notification and membership settings belong to your signed-in account."
        />
        <a className="btn btn-primary" href="/sign-in">
          Sign in
        </a>
      </div>
    );
  }
  const apiQuota = subscription.quotas.find((quota) => quota.feature === "api_requests");
  return (
    <form
      className="space-y-6"
      onSubmit={(event) => {
        event.preventDefault();
        void saveSettings();
      }}
    >
      <div className="rounded-lg border border-border-subtle bg-surface p-5">
        <p className="text-xs uppercase text-text-muted">Account email</p>
        <p className="mt-2 text-lg text-text">{account.email}</p>
      </div>
      <section className="space-y-4 rounded-lg border border-border-subtle p-5">
        <div>
          <p className="text-xs uppercase text-text-muted">Membership</p>
          <h2 className="mt-1 text-xl text-text">
            {subscription.paid_access ? "Podex Plus" : "Free discovery"}
          </h2>
        </div>
        <p className="text-sm text-text-secondary">
          {subscription.paid_access
            ? `${apiQuota?.remaining ?? 0} of ${apiQuota?.limit ?? 0} personalized API actions remaining this month.`
            : "Public discovery is free. Plus adds saves, follows, alerts, and digests when paid access launches."}
        </p>
        {subscription.paid_tier_enabled ? (
          !subscription.paid_access && (
            <button className="btn btn-primary" onClick={() => void beginUpgrade()} type="button">
              Upgrade to Plus
            </button>
          )
        ) : (
          <p className="text-sm text-text-muted">
            Paid upgrades are not available until launch review is complete.
          </p>
        )}
      </section>
      <section className="space-y-5">
        <div>
          <p className="text-xs uppercase text-text-muted">Notifications</p>
          <h2 className="mt-1 text-xl text-text">Digest delivery</h2>
        </div>
        <label className="flex items-center gap-3 text-text">
          <input
            checked={preferences.digest_enabled}
            className="h-4 w-4"
            onChange={(event) =>
              setPreferences({
                ...preferences,
                digest_enabled: event.target.checked,
              })
            }
            type="checkbox"
          />
          Digest emails enabled
        </label>
        <label className="block max-w-xs text-sm text-text-secondary">
          Digest frequency
          <select
            className="mt-2 w-full rounded-md border border-border-subtle bg-surface px-3 py-2 text-text"
            onChange={(event) =>
              setPreferences({
                ...preferences,
                digest_frequency: event.target.value as AccountDigestFrequency,
              })
            }
            value={preferences.digest_frequency}
          >
            <option value="immediate">Immediate</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select>
        </label>
      </section>
      {notice && (
        <p className="text-sm text-accent" role="status">
          {notice}
        </p>
      )}
      <button className="btn btn-primary" type="submit">
        Save settings
      </button>
    </form>
  );
}
