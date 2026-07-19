import { useEffect, useState } from "react";

import {
  getPreferences,
  type Preference,
  updatePreferences,
} from "../lib/account";
import AccountShell from "./AccountShell";

const FREQUENCIES = ["immediate", "daily", "weekly"] as const;

/** Notification preference settings. */
export default function AccountSettings() {
  return (
    <AccountShell active="/account/settings">
      {() => <SettingsBody />}
    </AccountShell>
  );
}

function SettingsBody() {
  const [preference, setPreference] = useState<Preference | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getPreferences()
      .then(setPreference)
      .catch(() => setError("Unable to load preferences."));
  }, []);

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (preference === null) {
    return <p className="text-sm text-[color:var(--color-muted)]">Loading…</p>;
  }

  const persist = (next: {
    digest_enabled: boolean;
    digest_frequency: (typeof FREQUENCIES)[number];
  }) => {
    setSaved(false);
    updatePreferences(next)
      .then((updated) => {
        setPreference(updated);
        setSaved(true);
      })
      .catch(() => setError("Unable to save preferences."));
  };

  return (
    <div className="max-w-xl rounded-lg border border-[color:var(--color-hairline)] bg-[color:var(--color-surface)] p-6">
      <h2 className="font-display text-2xl">Notifications</h2>
      <label className="mt-4 flex items-center gap-3 text-sm">
        <input
          type="checkbox"
          checked={preference.digest_enabled}
          onChange={(event) =>
            persist({
              digest_enabled: event.target.checked,
              digest_frequency: preference.digest_frequency as
                | "immediate"
                | "daily"
                | "weekly",
            })
          }
        />
        Send me activity digests by email
      </label>
      <label className="mt-4 block text-sm">
        <span className="text-[color:var(--color-muted)]">Frequency</span>
        <select
          className="mt-1 block rounded border border-[color:var(--color-hairline)] bg-white px-3 py-2 text-sm"
          value={preference.digest_frequency}
          onChange={(event) =>
            persist({
              digest_enabled: preference.digest_enabled,
              digest_frequency: event.target.value as
                | "immediate"
                | "daily"
                | "weekly",
            })
          }
        >
          {FREQUENCIES.map((frequency) => (
            <option key={frequency} value={frequency}>
              {frequency}
            </option>
          ))}
        </select>
      </label>
      {saved ? (
        <p className="mt-4 text-sm text-[color:var(--color-accent)]">Saved.</p>
      ) : null}
    </div>
  );
}
