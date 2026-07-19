import { useEffect, useState } from "react";

import { getOpsMetrics, type OpsMetrics, OpsApiError } from "../lib/ops";
import OpsShell from "./OpsShell";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-[color:var(--color-hairline)] bg-[color:var(--color-surface)] p-5">
      <p className="text-xs uppercase tracking-[0.2em] text-[color:var(--color-muted)]">
        {label}
      </p>
      <p className="font-display mt-2 text-3xl">{value}</p>
    </div>
  );
}

/** Operational health metrics for the ops dashboard. */
export default function OpsDashboard() {
  return (
    <OpsShell active="/ops">
      <DashboardBody />
    </OpsShell>
  );
}

function DashboardBody() {
  const [metrics, setMetrics] = useState<OpsMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getOpsMetrics()
      .then(setMetrics)
      .catch((cause: unknown) => {
        setError(
          cause instanceof OpsApiError && cause.status === 401
            ? "The ops key was rejected. Use “Change key” to retry."
            : "Unable to load metrics.",
        );
      });
  }, []);

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (!metrics) return <p className="text-sm text-[color:var(--color-muted)]">Loading…</p>;

  return (
    <div>
      <h2 className="font-display text-2xl">Review queue</h2>
      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <Stat label="Pending items" value={metrics.review.pending_items} />
        <Stat label="Decisions (24h)" value={metrics.review.decisions_last_24h} />
        <Stat
          label="Median decision (min)"
          value={metrics.review.median_decision_minutes_last_24h ?? "—"}
        />
      </div>
      <h2 className="font-display mt-10 text-2xl">Alert delivery</h2>
      <div className="mt-4 grid gap-4 sm:grid-cols-4">
        <Stat
          label="Events generated (24h)"
          value={metrics.alerts.generated_events_last_24h}
        />
        <Stat
          label="Digests delivered (24h)"
          value={metrics.alerts.delivered_digests_last_24h}
        />
        <Stat
          label="Events delivered (24h)"
          value={metrics.alerts.delivered_events_last_24h}
        />
        <Stat label="Events pending" value={metrics.alerts.pending_events} />
      </div>
      <p className="mt-8 text-xs text-[color:var(--color-muted)]">
        Measured at {new Date(metrics.measured_at).toLocaleString()}
      </p>
    </div>
  );
}
