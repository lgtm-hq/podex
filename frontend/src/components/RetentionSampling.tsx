import { useEffect, useState, type SyntheticEvent } from "react";
import {
  decideOpsTakedownRequest,
  evaluateOpsTranscriptRetention,
  getOpsRetentionSampling,
  getOpsTakedownRequests,
  getOpsTranscriptRetention,
  previewOpsTranscriptRetention,
  purgeOpsTranscript,
  reacquireOpsTranscript,
  recalculateOpsRetentionSampling,
} from "../lib/api";
import type {
  OpsRetentionSamplingReport,
  OpsTranscriptRetentionPolicyInput,
  OpsTranscriptRetentionPreview,
  OpsTranscriptRetentionSummary,
  OpsTakedownRequest,
} from "../lib/types";

export default function RetentionSampling() {
  const [report, setReport] = useState<OpsRetentionSamplingReport | null>(null);
  const [form, setForm] = useState({
    policy_version: "retention-sample-v2",
    sample_rate: "0.075",
    actor_name: "",
    note: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [transcripts, setTranscripts] = useState<OpsTranscriptRetentionSummary[]>([]);
  const [selected, setSelected] = useState<OpsTranscriptRetentionSummary | null>(null);
  const [preview, setPreview] = useState<OpsTranscriptRetentionPreview | null>(null);
  const [takedowns, setTakedowns] = useState<OpsTakedownRequest[]>([]);
  const [takedownNote, setTakedownNote] = useState("");
  const [takedownActor, setTakedownActor] = useState("");
  const [lifecycle, setLifecycle] = useState({
    policy_version: "retention-lifecycle-v1",
    hot_days: "30",
    warm_days: "180",
    min_purge_confidence: "0.85",
    source_retention_opt_out: false,
    actor_name: "",
    note: "",
  });

  useEffect(() => {
    Promise.all([getOpsRetentionSampling(), getOpsTranscriptRetention(), getOpsTakedownRequests()])
      .then(([current, assets, requests]) => {
        setReport(current);
        setTranscripts(assets.items);
        setTakedowns(requests.items);
        setForm((value) => ({
          ...value,
          policy_version: current.policy_version,
          sample_rate: String(current.sample_rate),
        }));
      })
      .catch(() => setError("Unable to load retention sample coverage."));
  }, []);

  async function recalculate(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const next = await recalculateOpsRetentionSampling({
        policy_version: form.policy_version.trim(),
        sample_rate: Number(form.sample_rate),
        actor_name: form.actor_name || null,
        note: form.note || null,
      });
      setReport(next);
      setError(null);
      setNotice(`${next.sampled_count} transcripts retained under ${next.policy_version}.`);
    } catch {
      setError("Unable to recalculate retention sample.");
    }
  }

  function lifecyclePayload(): OpsTranscriptRetentionPolicyInput {
    return {
      policy_version: lifecycle.policy_version,
      hot_days: Number(lifecycle.hot_days),
      warm_days: Number(lifecycle.warm_days),
      min_purge_confidence: Number(lifecycle.min_purge_confidence),
      source_retention_opt_out: lifecycle.source_retention_opt_out,
      actor_name: lifecycle.actor_name || null,
      note: lifecycle.note || null,
    };
  }

  async function previewLifecycle() {
    if (!selected) return;
    try {
      setPreview(await previewOpsTranscriptRetention(selected.id, lifecyclePayload()));
      setError(null);
    } catch {
      setError("Unable to preview transcript lifecycle.");
    }
  }

  async function applyEvaluation() {
    if (!selected) return;
    try {
      const next = await evaluateOpsTranscriptRetention(selected.id, lifecyclePayload());
      setPreview(next);
      setSelected(next.transcript);
      setTranscripts((items) =>
        items.map((item) => (item.id === next.transcript.id ? next.transcript : item)),
      );
      setNotice(`Retention evaluation saved for ${next.transcript.episode_title}.`);
    } catch {
      setError("Unable to save retention evaluation.");
    }
  }

  async function purgeTranscript() {
    if (!selected) return;
    try {
      const result = await purgeOpsTranscript(selected.id, lifecyclePayload());
      setSelected(result.transcript);
      setTranscripts((items) =>
        items.map((item) => (item.id === result.transcript.id ? result.transcript : item)),
      );
      setPreview(null);
      setNotice(`Raw transcript purged; digest ${result.digest.id} retained.`);
    } catch {
      setError("Unable to purge raw transcript.");
    }
  }

  async function reacquireTranscript() {
    if (!selected) return;
    try {
      const result = await reacquireOpsTranscript(selected.id, {
        actor_name: lifecycle.actor_name || null,
        note: lifecycle.note || null,
      });
      setSelected(result.transcript);
      setTranscripts((items) => [result.transcript, ...items]);
      setPreview(null);
      setNotice(
        `Raw transcript re-acquired as ${result.transcript.id}; retention restarted at hot.`,
      );
    } catch {
      setError("Unable to re-acquire raw transcript.");
    }
  }

  async function decideTakedown(id: string, status: "approved" | "rejected") {
    if (!takedownNote.trim()) {
      setError("A decision note is required for a takedown request.");
      return;
    }
    try {
      const updated = await decideOpsTakedownRequest(id, {
        status,
        actor_name: takedownActor || null,
        note: takedownNote.trim(),
      });
      setTakedowns((items) => items.map((item) => (item.id === updated.id ? updated : item)));
      setNotice(`Takedown request ${updated.id} ${updated.status}.`);
      setError(null);
    } catch {
      setError("Unable to decide takedown request.");
    }
  }

  return (
    <div className="space-y-6">
      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-100"
        >
          {error}
        </div>
      )}
      {notice && (
        <div
          role="status"
          className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4 text-emerald-100"
        >
          {notice}
        </div>
      )}

      {report && (
        <>
          <section className="grid gap-4 md:grid-cols-3">
            <div className="rounded-lg border border-border-subtle bg-surface p-5">
              <p className="text-sm text-text-muted">Eligible transcripts</p>
              <div className="mt-2 font-mono text-4xl">
                {report.eligible_count.toLocaleString()}
              </div>
            </div>
            <div className="rounded-lg border border-border-subtle bg-surface p-5">
              <p className="text-sm text-text-muted">Permanent sample</p>
              <div className="mt-2 font-mono text-4xl">{report.sampled_count.toLocaleString()}</div>
              <p className="mt-2 text-sm text-text-secondary">
                {Math.round(report.sample_rate * 1000) / 10}% policy rate
              </p>
            </div>
            <div className="rounded-lg border border-border-subtle bg-surface p-5">
              <p className="text-sm text-text-muted">Policy version</p>
              <div className="mt-3 font-mono text-lg">{report.policy_version}</div>
              <p className="mt-2 text-sm text-text-secondary">
                {report.target_count} targeted selections
              </p>
            </div>
          </section>

          <section className="grid gap-6 xl:grid-cols-[0.72fr_1.28fr]">
            <form
              className="rounded-lg border border-border-subtle bg-surface p-5"
              onSubmit={recalculate}
            >
              <h2 className="text-xl">Sampling policy</h2>
              <p className="mt-1 text-sm text-text-muted">
                Recalculations are versioned and audit logged.
              </p>
              <label className="mt-5 block text-sm text-text-secondary">
                Policy version
                <input
                  required
                  aria-label="Sampling policy version"
                  className="input mt-2"
                  value={form.policy_version}
                  onChange={(event) => setForm({ ...form, policy_version: event.target.value })}
                />
              </label>
              <label className="mt-4 block text-sm text-text-secondary">
                Target rate
                <select
                  aria-label="Sampling target rate"
                  className="input mt-2"
                  value={form.sample_rate}
                  onChange={(event) => setForm({ ...form, sample_rate: event.target.value })}
                >
                  <option value="0.05">5%</option>
                  <option value="0.075">7.5%</option>
                  <option value="0.1">10%</option>
                </select>
              </label>
              <input
                aria-label="Sampling operator name"
                className="input mt-4"
                placeholder="Operator name"
                value={form.actor_name}
                onChange={(event) => setForm({ ...form, actor_name: event.target.value })}
              />
              <textarea
                aria-label="Sampling policy note"
                className="input mt-4 min-h-20"
                placeholder="Reason for policy change"
                value={form.note}
                onChange={(event) => setForm({ ...form, note: event.target.value })}
              />
              <button className="btn btn-primary mt-4" type="submit">
                Recalculate sample
              </button>
            </form>

            <div className="rounded-lg border border-border-subtle bg-surface p-5">
              <h2 className="text-xl">Coverage by stratum</h2>
              <p className="mt-1 text-sm text-text-muted">
                Source, topic, confidence band, and age bucket assignments.
              </p>
              <div className="mt-5 overflow-x-auto">
                <table className="w-full min-w-[620px] text-left text-sm">
                  <thead className="text-text-muted">
                    <tr className="border-b border-border-subtle">
                      <th className="pb-3 font-medium">Source</th>
                      <th className="pb-3 font-medium">Topic</th>
                      <th className="pb-3 font-medium">Confidence</th>
                      <th className="pb-3 font-medium">Age</th>
                      <th className="pb-3 text-right font-medium">Sample</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.strata.map((stratum) => (
                      <tr
                        key={`${stratum.source}-${stratum.topic}-${stratum.confidence_band}-${stratum.age_bucket}`}
                        className="border-b border-border-subtle"
                      >
                        <td className="py-3">{stratum.source}</td>
                        <td className="py-3 capitalize">{stratum.topic.replaceAll("_", " ")}</td>
                        <td className="py-3 capitalize">{stratum.confidence_band}</td>
                        <td className="py-3 capitalize">
                          {stratum.age_bucket.replaceAll("_", " ")}
                        </td>
                        <td className="py-3 text-right font-mono">
                          {stratum.sampled_count}/{stratum.eligible_count}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {report.strata.length === 0 && (
                  <p className="py-5 text-sm text-text-muted">
                    No eligible transcripts have been assigned yet.
                  </p>
                )}
              </div>
            </div>
          </section>

          <section className="rounded-lg border border-border-subtle bg-surface p-5">
            <div>
              <h2 className="text-xl">Transcript lifecycle review</h2>
              <p className="mt-1 text-sm text-text-muted">
                Dry-run tier transitions and purge only after derivative coverage is complete.
              </p>
            </div>
            <div className="mt-5 grid gap-5 xl:grid-cols-[0.42fr_0.58fr]">
              <div className="space-y-2">
                {transcripts.map((transcript) => (
                  <button
                    key={transcript.id}
                    type="button"
                    className={`w-full rounded-lg border p-3 text-left ${selected?.id === transcript.id ? "border-accent bg-background" : "border-border-subtle bg-background/50"}`}
                    onClick={() => {
                      setSelected(transcript);
                      setPreview(null);
                    }}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium">{transcript.episode_title}</span>
                      <span className="rounded-full border border-border px-2 py-1 text-xs capitalize">
                        {transcript.tier}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-text-muted">
                      {transcript.podcast_name} · {transcript.provider} · {transcript.id}
                    </p>
                  </button>
                ))}
                {transcripts.length === 0 && (
                  <p className="text-sm text-text-muted">No transcript assets available.</p>
                )}
              </div>
              <div className="rounded-lg border border-border-subtle bg-background p-4">
                {selected ? (
                  <div className="space-y-4">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <input
                        required
                        aria-label="Lifecycle policy version"
                        className="input"
                        value={lifecycle.policy_version}
                        onChange={(event) =>
                          setLifecycle({ ...lifecycle, policy_version: event.target.value })
                        }
                      />
                      <input
                        aria-label="Minimum purge confidence"
                        className="input"
                        type="number"
                        min="0"
                        max="1"
                        step="0.01"
                        value={lifecycle.min_purge_confidence}
                        onChange={(event) =>
                          setLifecycle({ ...lifecycle, min_purge_confidence: event.target.value })
                        }
                      />
                      <input
                        aria-label="Hot retention days"
                        className="input"
                        type="number"
                        min="0"
                        value={lifecycle.hot_days}
                        onChange={(event) =>
                          setLifecycle({ ...lifecycle, hot_days: event.target.value })
                        }
                      />
                      <input
                        aria-label="Warm retention days"
                        className="input"
                        type="number"
                        min="0"
                        value={lifecycle.warm_days}
                        onChange={(event) =>
                          setLifecycle({ ...lifecycle, warm_days: event.target.value })
                        }
                      />
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <input
                        aria-label="Lifecycle operator name"
                        className="input"
                        placeholder="Operator name"
                        value={lifecycle.actor_name}
                        onChange={(event) =>
                          setLifecycle({ ...lifecycle, actor_name: event.target.value })
                        }
                      />
                      <input
                        aria-label="Lifecycle note"
                        className="input"
                        placeholder="Decision note"
                        value={lifecycle.note}
                        onChange={(event) =>
                          setLifecycle({ ...lifecycle, note: event.target.value })
                        }
                      />
                    </div>
                    <label className="flex items-center gap-3 text-sm text-text-secondary">
                      <input
                        aria-label="Suppress raw retention for source"
                        type="checkbox"
                        checked={lifecycle.source_retention_opt_out}
                        onChange={(event) =>
                          setLifecycle({
                            ...lifecycle,
                            source_retention_opt_out: event.target.checked,
                          })
                        }
                      />
                      Suppress future raw retention for this podcast source
                    </label>
                    <div className="flex flex-wrap gap-3">
                      <button
                        type="button"
                        className="btn btn-secondary"
                        onClick={() => void previewLifecycle()}
                      >
                        Preview lifecycle
                      </button>
                      <button
                        type="button"
                        className="btn btn-secondary"
                        onClick={() => void applyEvaluation()}
                      >
                        Save evaluation
                      </button>
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={!preview?.purge_eligible || !selected.has_raw_payload}
                        onClick={() => void purgeTranscript()}
                      >
                        Purge raw transcript
                      </button>
                      <button
                        type="button"
                        className="btn btn-secondary"
                        disabled={selected.tier !== "purged"}
                        onClick={() => void reacquireTranscript()}
                      >
                        Re-acquire raw transcript
                      </button>
                    </div>
                    {preview && (
                      <div className="rounded-lg border border-border-subtle p-4 text-sm">
                        <div className="flex flex-wrap justify-between gap-3">
                          <span>
                            Proposed tier{" "}
                            <strong className="capitalize">{preview.proposed_tier}</strong>
                          </span>
                          <span
                            className={
                              preview.purge_eligible ? "text-emerald-200" : "text-amber-200"
                            }
                          >
                            {preview.purge_eligible ? "Purge eligible" : "Purge blocked"}
                          </span>
                        </div>
                        <p className="mt-2 text-text-secondary">
                          Derivative coverage:{" "}
                          {preview.derivative_coverage_ready ? "complete" : "incomplete"}
                        </p>
                        {(preview.purge_blockers.length > 0 ||
                          preview.missing_query_classes.length > 0) && (
                          <p className="mt-2 text-text-muted">
                            {[...preview.purge_blockers, ...preview.missing_query_classes]
                              .join(", ")
                              .replaceAll("_", " ")}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-text-muted">
                    Select a transcript to preview its lifecycle gates.
                  </p>
                )}
              </div>
            </div>
          </section>

          <section className="rounded-lg border border-border-subtle bg-surface p-5">
            <h2 className="text-xl">Takedown intake</h2>
            <p className="mt-1 text-sm text-text-muted">
              Review creator and rights-holder submissions. Approving executes the selected
              suppression actions immediately.
            </p>
            <div className="mt-5 grid gap-5 xl:grid-cols-[0.62fr_0.38fr]">
              <div className="space-y-3">
                {takedowns.map((request) => (
                  <div
                    key={request.id}
                    className="rounded-lg border border-border-subtle bg-background p-4"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="font-medium">{request.requester_name}</div>
                        <p className="mt-1 text-sm text-text-muted">
                          {request.requester_type.replaceAll("_", " ")} · {request.subject_type}{" "}
                          {request.subject_id}
                        </p>
                      </div>
                      <span className="rounded-full border border-border px-2 py-1 text-xs capitalize">
                        {request.status}
                      </span>
                    </div>
                    <p className="mt-3 text-sm text-text-secondary">{request.basis}</p>
                    <p className="mt-2 text-sm text-text-muted">
                      {request.requested_actions.join(", ").replaceAll("_", " ")}
                    </p>
                    {request.status === "pending" && (
                      <div className="mt-4 flex gap-3">
                        <button
                          type="button"
                          className="btn btn-primary"
                          onClick={() => void decideTakedown(request.id, "approved")}
                        >
                          Approve request
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => void decideTakedown(request.id, "rejected")}
                        >
                          Reject request
                        </button>
                      </div>
                    )}
                  </div>
                ))}
                {takedowns.length === 0 && (
                  <p className="text-sm text-text-muted">No takedown requests submitted.</p>
                )}
              </div>
              <div className="space-y-3">
                <input
                  aria-label="Takedown operator name"
                  className="input"
                  placeholder="Operator name"
                  value={takedownActor}
                  onChange={(event) => setTakedownActor(event.target.value)}
                />
                <textarea
                  required
                  aria-label="Takedown decision note"
                  className="input min-h-28"
                  placeholder="Decision record"
                  value={takedownNote}
                  onChange={(event) => setTakedownNote(event.target.value)}
                />
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
