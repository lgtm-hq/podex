import { useEffect, useState } from "react";

import {
  getOpsPipelines,
  OpsApiError,
  type OpsPipelineActivity,
} from "../lib/ops";
import OpsShell from "./OpsShell";

/** Recent ingestion run inspection. */
export default function OpsPipelineRuns() {
  return (
    <OpsShell active="/ops/pipelines">
      <RunsBody />
    </OpsShell>
  );
}

function RunsBody() {
  const [activity, setActivity] = useState<OpsPipelineActivity | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getOpsPipelines()
      .then(setActivity)
      .catch((cause: unknown) => {
        setError(
          cause instanceof OpsApiError && cause.status === 401
            ? "The ops key was rejected. Use “Change key” to retry."
            : "Unable to load pipeline activity.",
        );
      });
  }, []);

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (!activity) {
    return <p className="text-sm text-[color:var(--color-muted)]">Loading…</p>;
  }
  if (activity.runs.length === 0) {
    return (
      <p className="text-sm text-[color:var(--color-muted)]">
        No ingestion runs recorded yet.
      </p>
    );
  }

  return (
    <table className="w-full text-left text-sm">
      <thead>
        <tr className="border-b border-[color:var(--color-hairline)] text-xs uppercase tracking-wide text-[color:var(--color-muted)]">
          <th className="py-2 pr-4">Run</th>
          <th className="py-2 pr-4">Status</th>
          <th className="py-2 pr-4">Started</th>
          <th className="py-2 pr-4">Duration</th>
          <th className="py-2">Error</th>
        </tr>
      </thead>
      <tbody>
        {activity.runs.map((run) => (
          <tr key={run.id} className="border-b border-[color:var(--color-hairline)]">
            <td className="py-3 pr-4">#{run.id}</td>
            <td className="py-3 pr-4 capitalize">{run.status}</td>
            <td className="py-3 pr-4">
              {run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
            </td>
            <td className="py-3 pr-4">
              {run.duration_seconds !== null ? `${run.duration_seconds}s` : "—"}
            </td>
            <td className="py-3 text-[color:var(--color-muted)]">
              {run.error_summary ?? "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
