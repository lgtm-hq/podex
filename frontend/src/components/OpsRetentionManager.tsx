import { useCallback, useEffect, useState } from "react";

import {
  applyOpsRetention,
  getOpsRetention,
  OpsApiError,
  type OpsRetentionItem,
  type OpsRetentionPreview,
  previewOpsRetention,
} from "../lib/ops";
import OpsShell from "./OpsShell";

/** Transcript retention lifecycle review and policy application. */
export default function OpsRetentionManager() {
  return (
    <OpsShell active="/ops/retention">
      <RetentionBody />
    </OpsShell>
  );
}

function RetentionBody() {
  const [items, setItems] = useState<OpsRetentionItem[]>([]);
  const [preview, setPreview] = useState<OpsRetentionPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const describeError = (cause: unknown): string =>
    cause instanceof OpsApiError && cause.status === 401
      ? "The ops key was rejected. Use “Change key” to retry."
      : "Request failed.";

  const reload = useCallback(() => {
    getOpsRetention()
      .then((listed) => {
        setItems(listed);
        setError(null);
      })
      .catch((cause: unknown) => setError(describeError(cause)));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  return (
    <div>
      {error ? <p className="text-sm text-red-700">{error}</p> : null}
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-[color:var(--color-hairline)] text-xs uppercase tracking-wide text-[color:var(--color-muted)]">
            <th className="py-2 pr-4">Transcript</th>
            <th className="py-2 pr-4">Tier</th>
            <th className="py-2 pr-4">Raw payload</th>
            <th className="py-2 pr-4">Purged</th>
            <th className="py-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr
              key={item.id}
              className="border-b border-[color:var(--color-hairline)]"
            >
              <td className="py-3 pr-4">
                #{item.id} · {item.episode_title}
                <span className="text-[color:var(--color-muted)]">
                  {" "}
                  ({item.podcast_name})
                </span>
              </td>
              <td className="py-3 pr-4 capitalize">{item.tier}</td>
              <td className="py-3 pr-4">{item.has_raw_payload ? "yes" : "no"}</td>
              <td className="py-3 pr-4">{item.purged_at ? "yes" : "no"}</td>
              <td className="py-3">
                <button
                  className="text-xs underline"
                  type="button"
                  onClick={() => {
                    previewOpsRetention(item.id)
                      .then(setPreview)
                      .catch((cause: unknown) => setError(describeError(cause)));
                  }}
                >
                  Preview
                </button>{" "}
                <button
                  className="text-xs underline"
                  type="button"
                  onClick={() => {
                    applyOpsRetention(item.id)
                      .then((applied) => {
                        setPreview(applied);
                        reload();
                      })
                      .catch((cause: unknown) => setError(describeError(cause)));
                  }}
                >
                  Apply policy
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {items.length === 0 && !error ? (
        <p className="mt-4 text-sm text-[color:var(--color-muted)]">
          No transcripts recorded yet.
        </p>
      ) : null}

      {preview ? (
        <div className="mt-8 max-w-xl rounded-lg border border-[color:var(--color-hairline)] bg-[color:var(--color-surface)] p-5">
          <h2 className="font-display text-xl">
            Transcript #{preview.transcript.id}
          </h2>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <dt className="text-[color:var(--color-muted)]">Decision tier</dt>
            <dd className="capitalize">{preview.decision.tier}</dd>
            <dt className="text-[color:var(--color-muted)]">Purge eligible</dt>
            <dd>{preview.decision.purge_eligible ? "yes" : "no"}</dd>
            <dt className="text-[color:var(--color-muted)]">Blockers</dt>
            <dd>
              {preview.decision.purge_blockers.length > 0
                ? preview.decision.purge_blockers.join(", ")
                : "none"}
            </dd>
            <dt className="text-[color:var(--color-muted)]">Coverage ready</dt>
            <dd>{preview.derivative_coverage_ready ? "yes" : "no"}</dd>
            <dt className="text-[color:var(--color-muted)]">Missing classes</dt>
            <dd>
              {preview.missing_query_classes.length > 0
                ? preview.missing_query_classes.join(", ")
                : "none"}
            </dd>
          </dl>
        </div>
      ) : null}
    </div>
  );
}
