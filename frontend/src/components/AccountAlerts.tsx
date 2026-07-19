import { useCallback, useEffect, useState } from "react";

import {
  type AlertRuleList,
  deleteAlertRule,
  getAlertRules,
  setAlertRuleEnabled,
} from "../lib/account";
import AccountShell from "./AccountShell";

/** Alert rules manager: pause, resume, and delete rules. */
export default function AccountAlerts() {
  return (
    <AccountShell active="/account/alerts">{() => <AlertsBody />}</AccountShell>
  );
}

function AlertsBody() {
  const [rules, setRules] = useState<AlertRuleList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    getAlertRules()
      .then(setRules)
      .catch(() => setError("Unable to load alert rules."));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (rules === null) {
    return <p className="text-sm text-[color:var(--color-muted)]">Loading…</p>;
  }
  if (rules.items.length === 0) {
    return (
      <p className="text-sm text-[color:var(--color-muted)]">
        No alert rules yet. Save media or follow podcasts, then create alerts
        from their pages to get notified about new activity.
      </p>
    );
  }

  return (
    <ul className="space-y-3">
      {rules.items.map((rule) => (
        <li
          key={rule.id}
          className="flex items-center justify-between rounded-lg border border-[color:var(--color-hairline)] p-4"
        >
          <div>
            <p className="font-medium">
              {rule.event_type === "new_mention"
                ? "New mentions of saved media"
                : "New episodes from followed podcast"}
              <span className="text-[color:var(--color-muted)]">
                {" "}
                · {rule.target_type} #{rule.target_id}
              </span>
            </p>
            <p className="mt-1 text-xs text-[color:var(--color-muted)]">
              {rule.enabled ? "active" : "paused"} · created{" "}
              {new Date(rule.created_at).toLocaleDateString()}
            </p>
          </div>
          <div className="flex gap-3">
            <button
              className="text-xs underline"
              type="button"
              onClick={() => {
                setAlertRuleEnabled(rule.id, !rule.enabled)
                  .then(reload)
                  .catch(() => setError("Unable to update that rule."));
              }}
            >
              {rule.enabled ? "Pause" : "Resume"}
            </button>
            <button
              className="text-xs text-[color:var(--color-muted)] underline"
              type="button"
              onClick={() => {
                deleteAlertRule(rule.id)
                  .then(reload)
                  .catch(() => setError("Unable to delete that rule."));
              }}
            >
              Delete
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}
