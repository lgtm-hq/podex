import { useCallback, useEffect, useState } from "react";
import { AsyncState } from "./AsyncState";
import {
  getOpsPipelineActivity,
  getOpsScheduledWork,
  planOpsScheduledWork,
  runOpsPipeline,
} from "../lib/api";
import type { OpsPipelineActivity, OpsScheduledWork, OpsScheduledWorkItem } from "../lib/types";

type WorkStatus = "" | "pending" | "running" | "completed" | "failed" | "skipped";

function statusClass(status: string): string {
  if (status === "failed") {
    return "border-red-500/30 bg-red-500/10 text-red-200";
  }
  if (["pending", "running", "in_progress"].includes(status)) {
    return "border-amber-500/30 bg-amber-500/10 text-amber-200";
  }
  if (status === "completed") {
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
  }
  return "border-border bg-background text-text-secondary";
}

function label(value: string): string {
  return value.replaceAll("_", " ");
}

function formatDate(value?: string): string {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function duration(seconds?: number): string {
  if (seconds === undefined) {
    return "-";
  }
  if (seconds < 60) {
    return `${seconds}s`;
  }
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export default function PipelineInspector() {
  const [activity, setActivity] = useState<OpsPipelineActivity | null>(null);
  const [work, setWork] = useState<OpsScheduledWork | null>(null);
  const [workStatus, setWorkStatus] = useState<WorkStatus>("");
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextActivity, nextWork] = await Promise.all([
        getOpsPipelineActivity(20, 20),
        getOpsScheduledWork(30, workStatus || undefined),
      ]);
      setActivity(nextActivity);
      setWork(nextWork);
    } catch {
      setError("Unable to load pipeline activity.");
    } finally {
      setLoading(false);
    }
  }, [workStatus]);

  useEffect(() => {
    void load();
  }, [load]);

  const queueRun = async () => {
    setActing(true);
    setNotice(null);
    setError(null);
    try {
      const run = await runOpsPipeline();
      setNotice(`Ingestion run ${run.id} queued.`);
      await load();
    } catch {
      setError("Unable to queue an ingestion run.");
    } finally {
      setActing(false);
    }
  };

  const planWork = async () => {
    setActing(true);
    setNotice(null);
    setError(null);
    try {
      const planned = await planOpsScheduledWork();
      setNotice(
        planned.length === 0
          ? "No recurring work is currently due."
          : `${planned.length} scheduled work item${planned.length === 1 ? "" : "s"} planned.`,
      );
      await load();
    } catch {
      setError("Unable to plan scheduled work.");
    } finally {
      setActing(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="flex flex-col justify-between gap-4 rounded-lg border border-border-subtle bg-surface p-5 md:flex-row md:items-center">
        <div>
          <h2 className="text-xl">Run control</h2>
          <p className="mt-1 text-sm text-text-muted">
            Queue ingestion deliberately, or materialize due recurring jobs for workers.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            className="btn btn-secondary"
            type="button"
            disabled={acting}
            onClick={() => void planWork()}
          >
            Plan due work
          </button>
          <button
            className="btn btn-primary"
            type="button"
            disabled={acting}
            onClick={() => void queueRun()}
          >
            {acting ? "Working..." : "Queue ingestion run"}
          </button>
        </div>
      </section>

      {(error || notice) && (
        <div
          role="status"
          className={`rounded-lg border p-4 text-sm ${
            error
              ? "border-red-500/30 bg-red-500/10 text-red-100"
              : "border-emerald-500/30 bg-emerald-500/10 text-emerald-100"
          }`}
        >
          {error ?? notice}
        </div>
      )}

      {loading || !activity || !work ? (
        <AsyncState title="Loading pipeline records..." variant="loading" />
      ) : (
        <>
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {[
              ["Runs completed", activity.summary.ingestion_runs_completed],
              ["Runs failed", activity.summary.ingestion_runs_failed],
              ["Jobs pending", activity.summary.transcription_jobs_pending],
              ["Repairs pending", activity.summary.projection_repairs_pending],
            ].map(([metric, value]) => (
              <div
                key={metric as string}
                className="rounded-lg border border-border-subtle bg-surface p-5"
              >
                <div className="text-sm text-text-muted">{metric}</div>
                <div className="mt-2 font-mono text-4xl">{value}</div>
              </div>
            ))}
          </section>

          <section className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
            <div className="overflow-hidden rounded-lg border border-border-subtle bg-surface">
              <div className="border-b border-border-subtle p-5">
                <h2 className="text-xl">Ingestion Runs</h2>
                <p className="mt-1 text-sm text-text-muted">
                  Recent top-level pipeline execution records.
                </p>
              </div>
              <div className="divide-y divide-border-subtle">
                {activity.runs.length ? (
                  activity.runs.map((run) => (
                    <div key={run.id} className="grid grid-cols-[1fr_auto] gap-3 p-4">
                      <div>
                        <div className="font-mono text-xs text-text-muted">{run.id}</div>
                        <div className="mt-1 text-sm text-text-secondary">
                          {formatDate(run.created_at)} / {duration(run.duration_seconds)}
                        </div>
                        {run.error_summary && (
                          <div className="mt-2 text-sm text-red-200">{run.error_summary}</div>
                        )}
                      </div>
                      <span
                        className={`h-fit rounded-full border px-2 py-1 text-xs capitalize ${statusClass(run.status)}`}
                      >
                        {label(run.status)}
                      </span>
                    </div>
                  ))
                ) : (
                  <p className="p-5 text-sm text-text-muted">No ingestion runs recorded.</p>
                )}
              </div>
            </div>

            <div className="overflow-hidden rounded-lg border border-border-subtle bg-surface">
              <div className="border-b border-border-subtle p-5">
                <h2 className="text-xl">Stage Jobs</h2>
                <p className="mt-1 text-sm text-text-muted">
                  Episode-level transcript and extraction processing.
                </p>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead className="text-left text-xs uppercase tracking-wider text-text-muted">
                    <tr>
                      <th className="px-5 py-3 font-medium">Episode</th>
                      <th className="px-5 py-3 font-medium">Stage</th>
                      <th className="px-5 py-3 font-medium">Duration</th>
                      <th className="px-5 py-3 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {activity.jobs.map((job) => (
                      <tr key={job.id}>
                        <td className="max-w-[280px] px-5 py-4">
                          <div className="truncate text-text">{job.episode_title}</div>
                          <div className="truncate text-xs text-text-muted">{job.podcast_name}</div>
                        </td>
                        <td className="px-5 py-4 capitalize text-text-secondary">
                          {label(job.job_type)}
                        </td>
                        <td className="px-5 py-4 font-mono text-text-secondary">
                          {duration(job.duration_seconds)}
                        </td>
                        <td className="px-5 py-4">
                          <span
                            className={`rounded-full border px-2 py-1 text-xs capitalize ${statusClass(job.status)}`}
                          >
                            {label(job.status)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {activity.jobs.length === 0 && (
                  <p className="p-5 text-sm text-text-muted">No stage jobs recorded.</p>
                )}
              </div>
            </div>
          </section>

          <section className="grid gap-6 xl:grid-cols-[0.8fr_1.2fr]">
            <div className="rounded-lg border border-border-subtle bg-surface p-5">
              <h2 className="text-xl">Recurring Schedules</h2>
              <div className="mt-4 space-y-3">
                {work.schedules.map((schedule) => (
                  <div
                    key={schedule.id}
                    className="rounded-lg border border-border-subtle bg-background p-4"
                  >
                    <div className="flex justify-between gap-3">
                      <div className="font-medium capitalize">{label(schedule.task_kind)}</div>
                      <span
                        className={`text-xs ${schedule.enabled ? "text-emerald-200" : "text-text-muted"}`}
                      >
                        {schedule.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </div>
                    <div className="mt-2 font-mono text-xs text-text-muted">
                      {schedule.schedule_key}
                    </div>
                    <div className="mt-3 text-sm text-text-secondary">
                      Every {schedule.interval_minutes} minutes / Next{" "}
                      {formatDate(schedule.next_due_at)}
                    </div>
                  </div>
                ))}
                {work.schedules.length === 0 && (
                  <p className="text-sm text-text-muted">No schedules configured.</p>
                )}
              </div>
            </div>

            <div className="overflow-hidden rounded-lg border border-border-subtle bg-surface">
              <div className="flex flex-col justify-between gap-3 border-b border-border-subtle p-5 sm:flex-row sm:items-center">
                <div>
                  <h2 className="text-xl">Scheduled Work Items</h2>
                  <p className="mt-1 text-sm text-text-muted">
                    Planned jobs awaiting or completing execution.
                  </p>
                </div>
                <label className="flex items-center gap-2 text-sm text-text-secondary">
                  <span>Status</span>
                  <select
                    className="input py-2"
                    value={workStatus}
                    onChange={(event) => setWorkStatus(event.target.value as WorkStatus)}
                  >
                    <option value="">All</option>
                    <option value="pending">Pending</option>
                    <option value="running">Running</option>
                    <option value="completed">Completed</option>
                    <option value="failed">Failed</option>
                    <option value="skipped">Skipped</option>
                  </select>
                </label>
              </div>
              <div className="divide-y divide-border-subtle">
                {work.work_items.map((item: OpsScheduledWorkItem) => (
                  <div
                    key={item.id}
                    className="flex flex-col justify-between gap-3 p-4 sm:flex-row sm:items-center"
                  >
                    <div>
                      <div className="capitalize text-text">{label(item.task_kind)}</div>
                      <div className="mt-1 font-mono text-xs text-text-muted">{item.work_key}</div>
                      <div className="mt-2 text-sm text-text-secondary">
                        Due {formatDate(item.due_at)}
                      </div>
                    </div>
                    <span
                      className={`h-fit rounded-full border px-2 py-1 text-xs capitalize ${statusClass(item.status)}`}
                    >
                      {label(item.status)}
                    </span>
                  </div>
                ))}
                {work.work_items.length === 0 && (
                  <p className="p-5 text-sm text-text-muted">No work items match this status.</p>
                )}
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
